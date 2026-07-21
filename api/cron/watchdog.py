"""Watchdog cron (plan.md 2.2/2.3) — pg_cron `0 */6 * * *`, plus a daily
GitHub Actions fallback curl (two independent legs of the dead-man's switch).

1. Dead-man check: any job whose heartbeat is silent for 3x its interval
   raises an alert email ("scheduler dead: <job>").
2. Orphan-event reconciler (H7): sweeps unprocessed email_events_raw rows,
   applies what can be matched by resend_id, and marks rows older than 30 days
   as unmatched so the table stops growing without bound.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import ops
from lib import supabase as sb
from lib.auth import is_cron_authorized

# job -> expected interval in seconds (dead after 3x with no success)
INTERVALS = {
    "sequencer_tick": 5 * 60,
    "enrich_tick": 10 * 60,
    "replies_tick": 15 * 60,
    "research_tick": 15 * 60,
    "learning_tick": 24 * 3600,
    "daily_scrape": 24 * 3600,
    "daily_digest": 24 * 3600,
}
RECONCILE_BATCH = int(os.environ.get("LEADGEN_RECONCILE_BATCH", "200"))
UNMATCHED_AFTER_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _check_heartbeats() -> list[str]:
    rows = sb.select("cron_heartbeats", {"select": "*"}, limit=100)
    by = {r["job"]: r for r in rows}
    dead = []
    for job, iv in INTERVALS.items():
        hb = by.get(job)
        last = (hb or {}).get("last_ok")
        if not last:
            dead.append(f"{job}: no successful run recorded")
            continue
        age = (_now() - _parse(last)).total_seconds()
        if age > iv * 3:
            dead.append(f"{job}: last success {int(age / 60)}m ago (interval {int(iv / 60)}m)")
    if dead:
        ops.alert("watchdog", "scheduler dead: " + "; ".join(dead))
    return dead


def _apply_to_sequence(raw: dict) -> bool:
    """Try to attach a buffered event to its sequence via a matching 'sent'
    event. Returns True when applied."""
    rid = raw.get("resend_id")
    if not rid:
        return False
    sent = sb.select("sequence_events", {
        "select": "sequence_id,step", "resend_id": f"eq.{rid}", "event_type": "eq.sent",
    }, limit=1)
    if not sent:
        return False
    et = raw["event_type"]
    inserted = sb.insert("sequence_events", {
        "sequence_id": sent[0]["sequence_id"], "step": sent[0]["step"],
        "event_type": et, "resend_id": rid,
        "meta": (raw.get("payload") or {}).get("data") or {},
    }, on_conflict="resend_id,event_type", ignore_duplicates=True)
    if inserted and et in ("opened", "clicked"):
        try:
            field = "opens" if et == "opened" else "clicks"
            sb.rpc("increment_sequence_counter", {"seq_id": sent[0]["sequence_id"], "field": field})
        except Exception:
            pass
    return True


def _apply_to_blast(raw: dict) -> bool:
    rid = raw.get("resend_id")
    if not rid:
        return False
    hits = sb.select("blast_recipients", {"select": "id", "resend_id": f"eq.{rid}"}, limit=1)
    if not hits:
        return False
    flag = {"opened": {"opened": True}, "clicked": {"clicked": True},
            "bounced": {"bounced": True}}.get(raw["event_type"])
    if flag:
        sb.update("blast_recipients", {"id": hits[0]["id"]}, flag)
    return True


def _reconcile() -> dict:
    rows = sb.select("email_events_raw", {
        "select": "id,resend_id,event_type,payload,received_at",
        "processed_at": "is.null", "order": "received_at.asc",
    }, limit=RECONCILE_BATCH)
    applied = unmatched = left = 0
    cutoff = _now() - timedelta(days=UNMATCHED_AFTER_DAYS)
    for raw in rows:
        try:
            if _apply_to_sequence(raw) or _apply_to_blast(raw):
                sb.update("email_events_raw", {"id": raw["id"]},
                          {"processed_at": _now().isoformat()})
                applied += 1
            elif _parse(raw["received_at"]) < cutoff:
                sb.update("email_events_raw", {"id": raw["id"]},
                          {"processed_at": _now().isoformat(), "source": "unmatched"})
                unmatched += 1
            else:
                left += 1
        except Exception as e:
            ops.log_event("error", "watchdog-reconcile", f"row {raw.get('id')}: {e}")
            left += 1
    return {"applied": applied, "unmatched": unmatched, "left": left, "scanned": len(rows)}


def _run() -> dict:
    dead = _check_heartbeats()
    rec = _reconcile()
    return {"ok": True, "dead_jobs": dead, "reconciled": rec}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = _run(); status = 200
            ops.heartbeat("watchdog", "ok", json.dumps(body)[:400])
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
            ops.heartbeat("watchdog", f"error: {str(e)[:100]}")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
