"""Phase 4 — sequencer tick. Driven by GitHub Actions every 5 min.

Each tick:
  1. Auto-create sequences (status='active', step 0, next_send_at=now) for enriched
     leads in ACTIVE campaigns that don't have one yet.
  2. Find due sequences (active, next_send_at <= now), respecting the daily cap.
  3. For each: draft the next step, send via Resend, log the 'sent' event, advance
     current_step + schedule next_send_at (hot cadence if the lead has opened).

Stateless. Bounded per tick (BATCH) so it never approaches the 300s limit.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import sequence as seq
from lib import supabase as sb
from lib.auth import is_cron_authorized

BATCH = int(os.environ.get("LEADGEN_TICK_BATCH", "25"))
DAILY_CAP = int(os.environ.get("LEADGEN_DAILY_CAP", "120"))
CREATE_PER_TICK = int(os.environ.get("LEADGEN_CREATE_PER_TICK", "50"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _active_campaigns() -> dict[int, dict]:
    # Try to pull the per-campaign sequence_config; if the column hasn't been
    # migrated yet the select 400s, so fall back to the legacy column set. Either
    # way the engine treats a missing config as the default 7-step drip.
    try:
        rows = sb.select(
            "campaigns",
            {"select": "id,offer_brief,region,active,sequence_config", "active": "eq.true"},
            limit=500,
        )
    except Exception:
        rows = sb.select("campaigns", {"select": "id,offer_brief,region,active", "active": "eq.true"}, limit=500)
    return {c["id"]: c for c in rows}


def _sent_today() -> int:
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = sb.select("sequence_events", {"select": "id", "event_type": "eq.sent", "ts": f"gte.{_iso(midnight)}"}, limit=20000)
    return len(rows)


def _autocreate(active: dict[int, dict]) -> int:
    """Create sequences for enriched leads in active campaigns lacking one."""
    if not active:
        return 0
    cids = ",".join(str(c) for c in active)
    leads = sb.select(
        "leads",
        {"select": "id,campaign_id", "enrichment_status": "eq.enriched", "campaign_id": f"in.({cids})"},
        limit=100000,
    )
    if not leads:
        return 0
    existing = sb.select("sequences", {"select": "lead_id"}, limit=100000)
    have = {s["lead_id"] for s in existing}
    todo = [l for l in leads if l["id"] not in have][:CREATE_PER_TICK]
    if not todo:
        return 0
    now = _iso(_now())
    rows = [{
        "lead_id": l["id"],
        "campaign_id": l["campaign_id"],
        "status": "active",
        "current_step": 0,
        "next_send_at": now,
    } for l in todo]
    sb.insert("sequences", rows, on_conflict="lead_id")
    return len(rows)


def _due(active_ids: set[int]) -> list[dict]:
    rows = sb.select(
        "sequences",
        {"select": "*", "status": "eq.active", "next_send_at": f"lte.{_iso(_now())}",
         "order": "next_send_at.asc"},
        limit=BATCH * 2,
    )
    return [r for r in rows if r["campaign_id"] in active_ids][:BATCH]


def _process_one(s: dict, active: dict[int, dict]) -> dict:
    # Optimistic claim-lock: atomically push next_send_at forward, matching on its
    # CURRENT value. If another concurrent runner already claimed this row, the
    # update touches 0 rows and we bail before sending — kills double-sends.
    if s.get("next_send_at"):
        claimed = sb.update(
            "sequences",
            {"id": s["id"], "next_send_at": s["next_send_at"]},
            {"next_send_at": _iso(_now() + timedelta(hours=1))},
        )
        if not claimed:
            return {"seq": s["id"], "skipped": "claimed-by-other"}

    leads = sb.select("leads", {"select": "*", "id": f"eq.{s['lead_id']}"}, limit=1)
    if not leads:
        sb.update("sequences", {"id": s["id"]}, {"status": "done", "paused_reason": "lead-missing", "next_send_at": None})
        return {"seq": s["id"], "skipped": "lead-missing"}
    lead = leads[0]

    if not lead.get("email"):
        sb.update("sequences", {"id": s["id"]}, {"status": "paused", "paused_reason": "no-email", "next_send_at": None})
        return {"seq": s["id"], "skipped": "no-email"}

    campaign = active.get(s["campaign_id"], {})
    config = campaign.get("sequence_config")

    next_step = (s.get("current_step", 0) or 0) + 1
    if next_step > seq.max_step(config):
        sb.update("sequences", {"id": s["id"]}, {"status": "done", "paused_reason": "completed", "next_send_at": None})
        return {"seq": s["id"], "done": "completed"}

    if not seq.passes_open_gate(s, next_step):
        sb.update("sequences", {"id": s["id"]}, {"status": "done", "paused_reason": f"no-opens-at-step-{next_step}", "next_send_at": None})
        return {"seq": s["id"], "done": "gated-no-opens"}

    offer = campaign.get("offer_brief")
    region = lead.get("country") or campaign.get("region")

    draft = seq.draft_one(lead, s, next_step, offer, config)
    sb.insert("drafts", {
        "sequence_id": s["id"], "step": next_step,
        "subject": draft["subject"], "body": draft["body"],
        "angle": draft.get("angle"), "model": draft.get("model"),
    }, on_conflict="sequence_id,step")

    result = seq.send_email(lead, draft, s["id"], next_step)
    if "error" in result:
        # Soft error — pause so it doesn't spin. Surfaced in dashboard.
        sb.update("sequences", {"id": s["id"]}, {"status": "paused", "paused_reason": f"send-error: {result['error'][:80]}"})
        return {"seq": s["id"], "error": result["error"]}

    rid = result.get("resend_id")
    sb.insert("sequence_events", {
        "sequence_id": s["id"], "step": next_step, "event_type": "sent",
        "resend_id": rid, "meta": {"subject": draft["subject"], "angle": draft.get("angle")},
    })
    nxt = seq.next_send_at(next_step, s.get("opens", 0) or 0, region, config=config)
    sb.update("sequences", {"id": s["id"]}, {
        "current_step": next_step,
        "next_send_at": _iso(nxt),
        "status": "active",
    })
    return {"seq": s["id"], "sent_step": next_step, "resend_id": rid, "subject": draft["subject"]}


def _run() -> dict:
    active = _active_campaigns()
    created = _autocreate(active)

    sent_today = _sent_today()
    remaining = max(0, DAILY_CAP - sent_today)
    if remaining <= 0:
        return {"ok": True, "created": created, "sent": 0, "reason": "daily-cap-reached", "cap": DAILY_CAP}

    due = _due(set(active))[:remaining]
    results = [_process_one(s, active) for s in due]
    sent = sum(1 for r in results if "sent_step" in r)
    return {"ok": True, "created": created, "due": len(due), "sent": sent,
            "sent_today_after": sent_today + sent, "cap": DAILY_CAP, "results": results}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = _run(); status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
