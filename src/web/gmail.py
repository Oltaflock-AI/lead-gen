"""Gmail reply detector — read-only.

We send cold outreach via Resend. Replies land in the user's Gmail inbox
(because the From address on the Resend message is the user's own domain).
This module pulls recent inbound mail and matches it against our
outreach_log to flip rows from `sent` → `replied`.
"""
import re
import sqlite3
from datetime import datetime, timezone

from googleapiclient.discovery import build

from . import db
from .sheets import get_credentials_from_dict

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _gmail(creds_dict):
    creds = get_credentials_from_dict(creds_dict)
    return build("gmail", "v1", credentials=creds)


def _extract_email(header_value):
    if not header_value:
        return ""
    m = re.search(r"<([^>]+)>", header_value)
    if m:
        return m.group(1).strip().lower()
    m = EMAIL_RE.search(header_value)
    return m.group(0).strip().lower() if m else ""


def check_replies(creds_dict, days=30, max_messages=300):
    """Scan recent inbox for replies to addresses we've contacted.

    Returns dict: {checked, matched, marked_replied, queries}.
    """
    sent_addrs = sorted(db.outreach_stats()["contacted_emails"])
    if not sent_addrs:
        return {"checked": 0, "matched": 0, "marked_replied": 0, "addresses": 0}

    service = _gmail(creds_dict)

    # Gmail's `q` param has length limits (~1000 chars). Batch the OR'd `from:`
    # queries in chunks of 30 addresses.
    chunk = 30
    matched_messages = []
    queries_run = 0

    for i in range(0, len(sent_addrs), chunk):
        slice_ = sent_addrs[i:i + chunk]
        q = " OR ".join(f"from:{a}" for a in slice_) + f" newer_than:{days}d"
        queries_run += 1
        try:
            page_token = None
            while True:
                req = service.users().messages().list(
                    userId="me", q=q, maxResults=100, pageToken=page_token
                )
                resp = req.execute()
                for m in resp.get("messages", []):
                    matched_messages.append(m["id"])
                    if len(matched_messages) >= max_messages:
                        break
                page_token = resp.get("nextPageToken")
                if not page_token or len(matched_messages) >= max_messages:
                    break
        except Exception:
            continue

    if not matched_messages:
        return {"checked": queries_run, "matched": 0, "marked_replied": 0,
                "addresses": len(sent_addrs)}

    # For each matched message, fetch the From header and update outreach_log.
    marked = 0
    seen_senders = set()
    for mid in matched_messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = _extract_email(headers.get("From", ""))
            if not sender or sender in seen_senders:
                continue
            seen_senders.add(sender)
            with sqlite3.connect(db.DB_PATH) as c:
                cur = c.execute(
                    "UPDATE outreach_log SET status = 'replied' "
                    "WHERE lead_email = ? AND status = 'sent'",
                    (sender,),
                )
                if cur.rowcount:
                    marked += cur.rowcount
        except Exception:
            continue

    return {
        "checked": queries_run,
        "matched": len(matched_messages),
        "marked_replied": marked,
        "addresses": len(sent_addrs),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
