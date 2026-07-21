"""Inbound email webhook — pauses a sequence the moment a lead replies.

Wire this to Resend Inbound (or any email forwarder that POSTs replies) at
  https://lead-gen-fawn-seven.vercel.app/webhook/inbound

When a reply lands, we match the sender's address to a lead, pause its active
sequence (so we stop emailing someone who answered), and log a 'replied' event.

Accepts both the Resend inbound shape ({type, data:{from,to,subject,text}}) and a
generic {from,to,subject} body. Tolerant of "Name <email>" formatting.
"""
import hmac
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import supabase as sb

INBOUND_SECRET = os.environ.get("INBOUND_WEBHOOK_SECRET", "")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Same stop-intent patterns as lib/gmail_replies.py (C4): a STOP reply through
# THIS channel must also land in `suppressions`, not just pause the sequence.
_STOP_PHRASES = re.compile(
    r"\b(?:"
    r"unsubscribe"
    r"|stop emailing"
    r"|remove me"
    r"|take me off"
    r"|do not contact"
    r"|don'?t contact"
    r"|not interested"
    r"|no thanks"
    r"|leave me alone"
    r"|opt[\s-]out"
    r"|fuck off"
    r")\b"
    r"|(?<![\w-])stop(?![\w-])",  # bare "stop", but not "non-stop"/"stop-gap"
    re.IGNORECASE,
)


def _is_stop(payload: dict) -> bool:
    data = payload.get("data") or payload
    text = " ".join(str(data.get(k) or "") for k in ("text", "subject", "html"))
    return bool(_STOP_PHRASES.search(text))


def _extract_email(value) -> str | None:
    """Pull a bare address out of 'Name <a@b.com>' or a list/dict form."""
    if not value:
        return None
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        value = value.get("email") or value.get("address") or ""
    m = EMAIL_RE.search(str(value))
    return m.group(0).lower() if m else None


def _sender(payload: dict) -> str | None:
    data = payload.get("data") or payload
    for key in ("from", "sender", "From", "reply_to"):
        e = _extract_email(data.get(key))
        if e:
            return e
    return None


def _mark_blast_replied(email: str) -> None:
    """A reply from this address counts toward any manual blast it received.
    Best-effort and independent of the sequence/lead match below."""
    try:
        sb.update("blast_recipients", {"email": email}, {"replied": True})
    except Exception:
        pass


def _pause_for_reply(email: str, *, stop: bool = False) -> dict:
    if stop:
        # C4: STOP intent is a hard opt-out — suppress the address globally, not
        # just pause its sequences. Insert-only so an existing row is untouched.
        sb.insert("suppressions",
                  {"email": email, "reason": "stop-request", "source": "inbound-webhook"},
                  on_conflict="email", ignore_duplicates=True)
    _mark_blast_replied(email)
    leads = sb.select("leads", {"select": "id", "email": f"eq.{email}"}, limit=50)
    if not leads:
        return {"matched": False, "email": email, "suppressed": stop}

    paused = []
    for lead in leads:
        seqs = sb.select(
            "sequences",
            {"select": "id,status", "lead_id": f"eq.{lead['id']}"},
            limit=10,
        )
        for s in seqs:
            sb.update("sequences", {"id": s["id"]}, {
                "replied": True,
                "status": "paused",
                "paused_reason": "stop-request" if stop else "replied",
                "next_send_at": None,
            })
            sb.insert("sequence_events", {
                "sequence_id": s["id"],
                "event_type": "replied",
                "meta": {"via": "inbound-webhook"},
            })
            paused.append(s["id"])
    return {"matched": True, "email": email, "paused_sequences": paused}


class handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, obj: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        self._respond(200, {"ok": True, "service": "inbound-reply-webhook"})

    def do_POST(self):
        # Shared-secret gate via the X-Webhook-Secret header (F12). Timing-safe,
        # no path bypass. Required in production; may be unset only in non-prod.
        if INBOUND_SECRET:
            got = self.headers.get("x-webhook-secret") or ""
            if not hmac.compare_digest(got, INBOUND_SECRET):
                try:
                    from lib import ops
                    ops.log_event("warn", "inbound-webhook", "rejected: bad shared secret")
                except Exception:
                    pass
                return self._respond(401, {"ok": False, "error": "secret"})
        elif os.environ.get("VERCEL_ENV") == "production":
            return self._respond(401, {"ok": False, "error": "INBOUND_WEBHOOK_SECRET not configured"})

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._respond(400, {"ok": False, "error": "bad json"})

        email = _sender(payload)
        if not email:
            return self._respond(200, {"ok": True, "result": {"matched": False, "reason": "no sender"}})

        try:
            result = _pause_for_reply(email, stop=_is_stop(payload))
            return self._respond(200, {"ok": True, "result": result})
        except Exception as e:
            return self._respond(500, {"ok": False, "error": str(e)})
