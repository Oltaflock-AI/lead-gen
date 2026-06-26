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
import base64
import json
import os
import re

from lib import enrich       # reuse _strip_html for html-only replies
from lib import reply_ai
from lib import supabase as sb

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
LOOKBACK_DAYS = int(os.environ.get("LEADGEN_REPLY_LOOKBACK_DAYS", "14"))
DRAFT_CAP = int(os.environ.get("LEADGEN_REPLY_DRAFT_CAP", "25"))  # max new replies drafted per poll
BODY_CAP = 4000

# Quoted-original markers — cut the reply body here so we classify only what the
# lead actually wrote, not the cold email they quoted back.
_QUOTE_MARKERS = [
    re.compile(r"\nOn .{0,120}wrote:", re.DOTALL),
    re.compile(r"\n-+ ?Original Message ?-+", re.IGNORECASE),
    re.compile(r"\n_{5,}"),
    re.compile(r"\nFrom: .+", re.IGNORECASE),
]


def _decode_b64(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "ignore")
    except Exception:
        return ""


def _walk_text(payload: dict) -> str:
    """Pull the best body text from a Gmail message payload (prefer text/plain)."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if data and mime in ("text/plain", "text/html"):
        raw = _decode_b64(data)
        return raw if mime == "text/plain" else enrich._strip_html(raw)
    best = ""
    for p in payload.get("parts") or []:
        t = _walk_text(p)
        if t:
            best = t
            if p.get("mimeType") == "text/plain":
                break  # plain wins
    return best


def _dequote(text: str) -> str:
    """Strip the quoted original message + quoted lines from a reply body."""
    if not text:
        return ""
    cut = len(text)
    for m in (rx.search(text) for rx in _QUOTE_MARKERS):
        if m:
            cut = min(cut, m.start())
    text = text[:cut]
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()[:BODY_CAP]

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
        for l in sb.select("leads", {"select": "id,business,email,city,country", "id": f"in.({chunk})"}, limit=500):
            leads[l["id"]] = l
    out: dict[str, list[dict]] = {}
    for s in seqs:
        l = leads.get(s["lead_id"])
        if l and l.get("email"):
            s["_business"] = l.get("business")
            s["_lead"] = l
            out.setdefault(l["email"].lower(), []).append(s)
    return out


def _has_stop_intent(text: str) -> bool:
    """Return True if *text* contains a stop/unsubscribe signal."""
    return bool(_STOP_PHRASES.search(text or ""))


def _matched_senders(svc, addrs: list[str]) -> dict[str, list[dict]]:
    """Return {lowercased_email: [message, ...]} for each addr that replied.

    Each message is {msg_id, subject, snippet, body}. Fetched once per message
    with format='full' so we get the From/Subject headers, the Gmail snippet, and
    the full (de-quoted) body in a single API call.
    """
    found: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for i in range(0, len(addrs), 25):
        q = " OR ".join(f"from:{a}" for a in addrs[i:i + 25]) + f" newer_than:{LOOKBACK_DAYS}d"
        try:
            resp = svc.users().messages().list(userId="me", q=q, maxResults=50).execute()
        except Exception:
            continue
        for m in resp.get("messages", []):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            try:
                mm = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
                hs = {x["name"].lower(): x["value"] for x in mm.get("payload", {}).get("headers", [])}
                em = EMAIL_RE.search(hs.get("from", ""))
                if not em:
                    continue
                addr = em.group(0).lower()
                found.setdefault(addr, []).append({
                    "msg_id": m["id"],
                    "subject": hs.get("subject", ""),
                    "snippet": mm.get("snippet", ""),
                    "body": _dequote(_walk_text(mm.get("payload", {}))) or mm.get("snippet", ""),
                })
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

    # Which inbound messages have we already captured? Skip them so we never
    # re-draft (idempotent + saves an LLM call per poll).
    msg_ids = [m["msg_id"] for msgs in found.values() for m in msgs]
    seen_msgs: set[str] = set()
    for i in range(0, len(msg_ids), 200):
        chunk = ",".join(msg_ids[i:i + 200])
        try:
            for r in sb.select("replies", {"select": "gmail_msg_id", "gmail_msg_id": f"in.({chunk})"}, limit=500):
                seen_msgs.add(r["gmail_msg_id"])
        except Exception:
            break  # replies table not migrated yet — pausing logic still runs below

    logged = 0
    drafted = 0
    replies = []
    for em, msgs in found.items():
        combined = " ".join((m.get("body") or m.get("snippet") or "") for m in msgs)
        stop = _has_stop_intent(combined)

        if stop:
            try:
                sb.insert(
                    "suppressions",
                    {"email": em, "reason": "unsubscribe", "source": "gmail-reply"},
                    on_conflict="email",
                )
            except Exception:
                pass  # never let a failed suppression insert break the poll

        seqs = contacted.get(em, [])
        for s in seqs:
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

        # Capture each new inbound message into the reply inbox. Stop replies are
        # recorded but auto-dismissed (already suppressed); the rest get an AI
        # intent + draft for one-click send.
        first = seqs[0] if seqs else {}
        lead = first.get("_lead") or {"business": first.get("_business"), "email": em}
        for msg in msgs:
            if msg["msg_id"] in seen_msgs:
                continue
            body = (msg.get("body") or msg.get("snippet") or "")[:BODY_CAP]
            row = {
                "gmail_msg_id": msg["msg_id"],
                "sequence_id": first.get("id"),
                "lead_id": (first.get("_lead") or {}).get("id"),
                "from_email": em,
                "business": first.get("_business"),
                "in_subject": msg.get("subject"),
                "in_body": body,
            }
            if stop:
                row.update({"intent": "stop", "status": "dismissed"})
            elif drafted >= DRAFT_CAP:
                continue  # leave for the next poll rather than over-spend per tick
            else:
                d = reply_ai.classify_and_draft(body, msg.get("subject"), lead, hint_stop=stop)
                row.update({
                    "intent": d["intent"], "draft_subject": d["subject"],
                    "draft_body": d["body"], "model": d.get("model"), "status": "pending",
                })
                drafted += 1
            try:
                sb.insert("replies", row)
                seen_msgs.add(msg["msg_id"])
            except Exception:
                pass  # table missing or unique race — never break the poll

    return {"ok": True, "contacted": len(contacted), "matched": len(found),
            "logged": logged, "drafted": drafted, "replies": replies}
