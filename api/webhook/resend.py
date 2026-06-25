"""Resend webhook — receives email events, persists to Supabase.

Events handled: email.sent, email.delivered, email.opened, email.clicked,
email.bounced, email.complained, email.delivery_delayed.

Resend signs payloads via Svix. Set RESEND_WEBHOOK_SECRET to verify.
Match resend_id back to sequence + step from the tags Resend echoes.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import supabase as sb

RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")

# Resend event → internal event_type
# NOTE: "email.sent" is intentionally NOT mapped. The sequencer already logs the
# 'sent' event at send time (same resend_id), so handling it here double-logs and
# inflates the "Sent" KPI 2x. Sequencer owns 'sent'; webhook owns everything after.
EVENT_MAP = {
    "email.delivered":        "delivered",
    "email.delivery_delayed": "delayed",
    "email.opened":           "opened",
    "email.clicked":          "clicked",
    "email.bounced":          "bounced",
    "email.complained":       "complained",
}


def _verify_svix(body: bytes, headers) -> bool:
    if not RESEND_WEBHOOK_SECRET:
        return True
    try:
        from svix.webhooks import Webhook
        wh = Webhook(RESEND_WEBHOOK_SECRET)
        hmap = {
            "svix-id":        headers.get("svix-id"),
            "svix-timestamp": headers.get("svix-timestamp"),
            "svix-signature": headers.get("svix-signature"),
        }
        wh.verify(body, hmap)
        return True
    except Exception:
        return False


def _tags_to_dict(tags) -> dict:
    """Resend echoes tags as a dict OR a list of {name, value}. Normalize."""
    if isinstance(tags, dict):
        return tags
    if isinstance(tags, list):
        out = {}
        for t in tags:
            if isinstance(t, dict) and "name" in t:
                out[t["name"]] = t.get("value")
        return out
    return {}


def _find_sequence_step(resend_id: str, tags) -> tuple[int | None, int | None]:
    """Resolve resend_id → (sequence_id, step).

    Preferred: read tags Resend echoes back (we set them on send).
    Fallback: lookup last 'sent' event with this resend_id.
    """
    td = _tags_to_dict(tags)
    if td.get("sequence_id") is not None:
        try:
            seq = int(td["sequence_id"])
            step = int(td["step"]) if td.get("step") is not None else None
            return seq, step
        except (TypeError, ValueError):
            pass
    rows = sb.select(
        "sequence_events",
        params={
            "select": "sequence_id,step",
            "resend_id": f"eq.{resend_id}",
            "event_type": "eq.sent",
            "order": "ts.desc",
        },
        limit=1,
    )
    if rows:
        return rows[0]["sequence_id"], rows[0]["step"]
    return None, None


def _is_bot_open(sequence_id: int, resend_id: str | None) -> bool:
    """Opens firing <35s after send are image-proxy / scanner bots, not humans."""
    from datetime import datetime, timezone
    params = {"select": "ts", "sequence_id": f"eq.{sequence_id}", "event_type": "eq.sent", "order": "ts.desc"}
    if resend_id:
        params["resend_id"] = f"eq.{resend_id}"
    rows = sb.select("sequence_events", params=params, limit=1)
    if not rows:
        return False
    try:
        sent = datetime.fromisoformat(rows[0]["ts"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - sent).total_seconds() < 35
    except Exception:
        return False


def _accelerate_on_open(sequence_id: int) -> None:
    """Real open → pull the next send forward so we follow up while they're warm."""
    from datetime import datetime, timedelta, timezone
    rows = sb.select("sequences", {"select": "status,current_step,next_send_at", "id": f"eq.{sequence_id}"}, limit=1)
    if not rows:
        return
    s = rows[0]
    if s["status"] != "active" or (s.get("current_step") or 0) >= 7:
        return
    pull_hours = int(os.environ.get("LEADGEN_OPEN_PULL_HOURS", "6"))
    target = datetime.now(timezone.utc) + timedelta(hours=pull_hours)
    cur = s.get("next_send_at")
    try:
        cur_dt = datetime.fromisoformat(cur.replace("Z", "+00:00")) if cur else None
    except Exception:
        cur_dt = None
    if cur_dt is None or target < cur_dt:
        sb.update("sequences", {"id": sequence_id}, {"next_send_at": target.isoformat()})


def _update_blast_recipient(resend_id: str | None, event_type: str) -> None:
    """Correlate a webhook event to a manual-blast recipient by resend_id and
    flip the matching flag. Best-effort: blast tracking is independent of the
    sequence pipeline, so failures here must never break event recording."""
    if not resend_id:
        return
    rows = sb.select("blast_recipients",
                     {"select": "id,created_at,opened,clicked",
                      "resend_id": f"eq.{resend_id}"}, limit=1)
    if not rows:
        return
    from datetime import datetime, timezone
    r = rows[0]
    now = datetime.now(timezone.utc)

    def _fresh() -> bool:  # same <35s bot-open/click guard as sequences
        try:
            t = datetime.fromisoformat((r.get("created_at") or "").replace("Z", "+00:00"))
            return (now - t).total_seconds() < 35
        except Exception:
            return False

    patch = {}
    if event_type == "opened" and not _fresh():
        patch = {"opened": True, "opened_at": now.isoformat()}
    elif event_type == "clicked" and not _fresh():
        patch = {"clicked": True, "clicked_at": now.isoformat()}
    elif event_type == "bounced":
        patch = {"bounced": True}
    if patch:
        sb.update("blast_recipients", {"id": r["id"]}, patch)


def _record(payload: dict) -> dict:
    raw_type = payload.get("type", "")
    event_type = EVENT_MAP.get(raw_type)
    if not event_type:
        return {"skipped": f"unknown type {raw_type}"}

    data = payload.get("data") or {}
    resend_id = data.get("email_id") or data.get("id")
    tags = data.get("tags")

    # Manual-blast tracking runs alongside (and independent of) the sequence
    # pipeline below. Recipients of one-off composer sends — including arbitrary
    # typed-in addresses with no lead/sequence — are correlated here by resend_id.
    try:
        _update_blast_recipient(resend_id, event_type)
    except Exception:
        pass

    sequence_id, step = _find_sequence_step(resend_id, tags) if resend_id else (None, None)

    row = {
        "sequence_id": sequence_id,
        "step": step,
        "event_type": event_type,
        "resend_id": resend_id,
        "meta": data,
    }
    if sequence_id is None:
        # Don't insert orphans into sequence_events (FK NOT NULL).
        # Buffer into existing email_events_raw for reconciliation.
        sb.insert("email_events_raw", {
            "resend_id": resend_id,
            "event_type": event_type,
            "payload": payload,
            "source": "vercel-webhook",
        })
        return {"buffered": True, "resend_id": resend_id, "type": event_type}

    # Filter bot opens before they pollute counters or trigger acceleration.
    if event_type == "opened" and _is_bot_open(sequence_id, resend_id):
        row["event_type"] = "opened_bot"
        sb.insert("sequence_events", row)
        return {"filtered": "bot-open", "sequence_id": sequence_id}

    # Same for clicks: link-prefetch scanners (CloudFront, SafeLinks, Mimecast)
    # hit every link within seconds of delivery. Don't count them as real clicks.
    if event_type == "clicked" and _is_bot_open(sequence_id, resend_id):
        row["event_type"] = "clicked_bot"
        sb.insert("sequence_events", row)
        return {"filtered": "bot-click", "sequence_id": sequence_id}

    sb.insert("sequence_events", row)

    # Increment counters + react.
    if event_type == "opened":
        sb.rpc("increment_sequence_counter", {"seq_id": sequence_id, "field": "opens"})
        _accelerate_on_open(sequence_id)
    elif event_type == "clicked":
        sb.rpc("increment_sequence_counter", {"seq_id": sequence_id, "field": "clicks"})
        _accelerate_on_open(sequence_id)
    elif event_type == "bounced":
        sb.update("sequences", {"id": sequence_id}, {"bounced": True, "status": "paused", "paused_reason": "bounced"})
    elif event_type == "complained":
        sb.update("sequences", {"id": sequence_id}, {"status": "paused", "paused_reason": "complained"})

    return {"recorded": True, "sequence_id": sequence_id, "step": step, "type": event_type}


class handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, obj: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        # Resend webhook health check
        self._respond(200, {"ok": True, "service": "resend-webhook"})

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        if not _verify_svix(body, self.headers):
            return self._respond(401, {"ok": False, "error": "signature"})

        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return self._respond(400, {"ok": False, "error": "bad json"})

        try:
            result = _record(payload)
            return self._respond(200, {"ok": True, "result": result})
        except Exception as e:
            return self._respond(500, {"ok": False, "error": str(e)})
