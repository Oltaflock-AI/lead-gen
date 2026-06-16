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
EVENT_MAP = {
    "email.sent":             "sent",
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


def _find_sequence_step(resend_id: str, tags: dict | None) -> tuple[int | None, int | None]:
    """Resolve resend_id → (sequence_id, step).

    Preferred: read tags Resend echoes back (we set them on send).
    Fallback: lookup last 'sent' event with this resend_id.
    """
    if tags:
        seq = tags.get("sequence_id")
        step = tags.get("step")
        if seq is not None:
            try:
                return int(seq), int(step) if step is not None else None
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


def _record(payload: dict) -> dict:
    raw_type = payload.get("type", "")
    event_type = EVENT_MAP.get(raw_type)
    if not event_type:
        return {"skipped": f"unknown type {raw_type}"}

    data = payload.get("data") or {}
    resend_id = data.get("email_id") or data.get("id")
    tags = data.get("tags")
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

    sb.insert("sequence_events", row)

    # Increment counters on the sequence row.
    if event_type == "opened":
        sb.rpc("increment_sequence_counter", {"seq_id": sequence_id, "field": "opens"})
    elif event_type == "clicked":
        sb.rpc("increment_sequence_counter", {"seq_id": sequence_id, "field": "clicks"})
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
