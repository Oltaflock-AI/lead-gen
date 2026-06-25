"""Gmail reply poller for the autopilot.

Replies to our cold sends land in a human Gmail inbox (khush@oltaflock.ai),
not in Resend Inbound, so the inbound webhook never fires. This module polls
that inbox via the Gmail API, matches senders to contacted leads, and logs a
'replied' event + pauses the sequence — exactly what the webhook would do.

Auth: a stored OAuth token (with refresh_token + gmail.readonly scope) is read
from the GMAIL_TOKEN_JSON env var (the full token JSON as a string). Client id
and secret fall back to GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.

Stateless and idempotent: a sequence already paused with reason 'replied' is
skipped, so re-running never double-logs.

Stop-intent: if the reply body signals the sender wants no further contact, the
address is inserted into public.suppressions (reason='unsubscribe',
source='gmail-reply') and the sequence is paused with paused_reason='unsubscribed'.
"""
import json
import os
import re

from lib import supabase as sb

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
LOOKBACK_DAYS = int(os.environ.get("LEADGEN_REPLY_LOOKBACK_DAYS", "14"))

# Word-boundary-aware patterns for stop intent.  A bare "stop" matches but
# "non-stop" does not.  All comparisons are done on lowercased text.
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
    r"|(?<!\w)stop(?!\w)",  # bare "stop" not preceded or followed by a word char
    re.IGNORECASE,
)


def _token() -> dict | None:
    raw = os.environ.get("GMAIL_TOKEN_JSON", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _service(tok: dict):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=tok.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=tok.get("client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=tok.get("scopes") or ["https://www.googleapis.com/auth/gmail.readonly"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _contacted_map() -> dict[str, list[dict]]:
    """email (lowercased) -> list of its sequences (only active/unpaused matter)."""
    seqs = sb.select("sequences", {"select": "id,lead_id,status"}, limit=100000)
    lead_ids = list({s["lead_id"] for s in seqs if s.get("lead_id")})
    leads: dict[int, dict] = {}
    for i in range(0, len(lead_ids), 200):
        chunk = ",".join(str(x) for x in lead_ids[i:i + 200])
        for l in sb.select("leads", {"select": "id,business,email", "id": f"in.({chunk})"}, limit=500):
            leads[l["id"]] = l
    out: dict[str, list[dict]] = {}
    for s in seqs:
        l = leads.get(s["lead_id"])
        if l and l.get("email"):
            s["_business"] = l.get("business")
            out.setdefault(l["email"].lower(), []).append(s)
    return out


def _has_stop_intent(text: str) -> bool:
    """Return True if *text* contains a stop/unsubscribe signal."""
    return bool(_STOP_PHRASES.search(text or ""))


def _matched_senders(svc, addrs: list[str]) -> dict[str, str]:
    """Return {lowercased_email: snippet} for each addr that replied.

    Fetches each matching message once with format='full' so we get both the
    From header and the Gmail-generated snippet (≤200 chars of body preview)
    in a single API call per message.
    """
    found: dict[str, str] = {}
    for i in range(0, len(addrs), 25):
        q = " OR ".join(f"from:{a}" for a in addrs[i:i + 25]) + f" newer_than:{LOOKBACK_DAYS}d"
        try:
            resp = svc.users().messages().list(userId="me", q=q, maxResults=50).execute()
        except Exception:
            continue
        for m in resp.get("messages", []):
            try:
                mm = svc.users().messages().get(
                    userId="me", id=m["id"], format="full", metadataHeaders=["From"]
                ).execute()
                hs = {x["name"]: x["value"] for x in mm.get("payload", {}).get("headers", [])}
                em = EMAIL_RE.search(hs.get("From", ""))
                if em:
                    addr = em.group(0).lower()
                    # Keep the longest snippet seen for this sender (most info).
                    snippet = mm.get("snippet", "")
                    if len(snippet) > len(found.get(addr, "")):
                        found[addr] = snippet
            except Exception:
                continue
    return found


def check_and_log_replies() -> dict:
    tok = _token()
    if not tok:
        return {"ok": False, "error": "GMAIL_TOKEN_JSON not set"}

    contacted = _contacted_map()
    if not contacted:
        return {"ok": True, "matched": 0, "logged": 0, "contacted": 0}

    svc = _service(tok)
    found = _matched_senders(svc, list(contacted.keys()))

    logged = 0
    replies = []
    for em, snippet in found.items():
        stop = _has_stop_intent(snippet)

        if stop:
            try:
                sb.insert(
                    "suppressions",
                    {"email": em, "reason": "unsubscribe", "source": "gmail-reply"},
                    on_conflict="email",
                )
            except Exception:
                pass  # never let a failed suppression insert break the poll

        for s in contacted.get(em, []):
            if s.get("status") == "paused":  # already replied/paused — idempotent
                continue
            paused_reason = "unsubscribed" if stop else "replied"
            sb.update("sequences", {"id": s["id"]}, {
                "replied": True, "status": "paused",
                "paused_reason": paused_reason, "next_send_at": None,
            })
            sb.insert("sequence_events", {
                "sequence_id": s["id"], "event_type": "replied",
                "meta": {"via": "gmail-poll", "from": em, "stop_intent": stop},
            })
            logged += 1
            replies.append({
                "seq": s["id"], "email": em,
                "business": s.get("_business"), "stop_intent": stop,
            })

    return {"ok": True, "contacted": len(contacted), "matched": len(found),
            "logged": logged, "replies": replies}
