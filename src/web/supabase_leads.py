"""Lead-master sync: scraped + enriched leads → Supabase `leads_master`.

Idempotent upsert keyed on lowercased `email`. Re-running a scrape, generating
fresh AI scores, sending an email, or capturing a webhook event all funnel
into the same row, so over weeks/months you build a longitudinal profile per
prospect: rating drift, review-count growth, opens, clicks, replies, intent.

Schema lives in `docs/supabase-leads-master.sql` — run once in the Supabase
SQL editor before enabling sync. If the table is missing the upserts no-op
with a warning instead of crashing the request.

Env (already shared with supabase_sync):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

from . import supabase_sync as _ss

log = logging.getLogger(__name__)

TABLE = "leads_master"
HTTP_TIMEOUT = 20


def is_configured() -> bool:
    return _ss.is_configured()


def _h(prefer: str) -> dict:
    h = _ss._headers()
    h["Prefer"] = prefer
    return h


def _f(v) -> Optional[float]:
    if v in (None, "", "—"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    f = _f(v)
    return int(f) if f is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_payload(r: dict, source_csv: str) -> Optional[dict]:
    """Project one row (raw CSV dict OR row from `metrics.read_csv_with_scores`)
    into the Supabase column shape. Returns None if no usable email."""
    n = r.get("_normalized") or r
    email = (n.get("email") or r.get("Email") or r.get("email") or "").strip().lower()
    if not email:
        return None
    return {
        "email":         email,
        "business_name": n.get("business_name") or r.get("Business Name") or r.get("business_name") or "",
        "business_type": n.get("business_type") or r.get("Business Type") or r.get("business_type") or "",
        "niche":         r.get("niche") or r.get("Niche") or "",
        "city":          n.get("city") or r.get("City") or "",
        "address":       n.get("address") or r.get("Address") or "",
        "country":       r.get("country") or r.get("Country") or "",
        "phone":         n.get("phone") or r.get("Phone") or "",
        "website":       n.get("website") or r.get("Website") or "",
        "rating":        _f(n.get("rating") or r.get("Rating")),
        "review_count":  _i(n.get("review_count") or r.get("Reviews")),
        "place_id":      r.get("place_id") or r.get("Place ID") or "",
        "opening_hours_summary": r.get("opening_hours_summary") or "",
        "ai_niche_fit":  _f(r.get("ai_niche_fit")),
        "ai_lead_score": _f(r.get("ai_lead_score")),
        "ai_score_reason": r.get("ai_score_reason") or "",
        "email_verified": r.get("email_verified") or "",
        "source_csv":    source_csv,
        "last_seen_at":  _now(),
    }


def upsert_leads(rows: Iterable[dict], source_csv: str = "") -> int:
    """Bulk upsert. Returns rows-sent count (0 on failure or no-config).
    Within a single batch we keep the last row per email — PostgREST refuses
    payloads where the same conflict-key appears twice."""
    if not is_configured():
        return 0
    by_email: dict[str, dict] = {}
    for r in rows:
        p = _row_payload(r, source_csv)
        if p:
            by_email[p["email"]] = p   # last write wins
    payload = list(by_email.values())
    if not payload:
        return 0
    url = f"{_ss.SUPABASE_URL}/rest/v1/{TABLE}?on_conflict=email"
    try:
        r = requests.post(url, json=payload, headers=_h(
            "resolution=merge-duplicates,return=minimal"), timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.warning("supabase_leads upsert error: %s", e)
        return 0
    if not r.ok:
        log.warning("supabase_leads upsert HTTP %s: %s", r.status_code, r.text[:240])
        return 0
    log.info("supabase_leads upsert ok n=%d csv=%s", len(payload), source_csv)
    return len(payload)


def update_outreach(email: str, **fields) -> bool:
    """Patch outreach lifecycle fields on one lead. Silently no-ops if the
    table is missing or the email isn't found yet (the next scrape will
    create the row)."""
    if not is_configured() or not email:
        return False
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return False
    fields["last_seen_at"] = _now()
    url = f"{_ss.SUPABASE_URL}/rest/v1/{TABLE}?email=eq.{email.lower()}"
    try:
        r = requests.patch(url, json=fields, headers=_h("return=minimal"),
                           timeout=10)
    except requests.RequestException as e:
        log.warning("supabase_leads patch error %s: %s", email, e)
        return False
    if not r.ok:
        log.warning("supabase_leads patch HTTP %s for %s: %s",
                    r.status_code, email, r.text[:200])
        return False
    return True


# Convenience helpers used by the outreach send + webhook paths.
def mark_sent(email: str, business_name: str = "", subject: str = ""):
    return update_outreach(
        email,
        outreach_status="sent",
        last_sent_at=_now(),
        last_subject=subject or None,
        business_name=business_name or None,
    )


def mark_event(email: str, event_type: str):
    """event_type ∈ {delivered, opened, clicked, replied, bounced, failed}."""
    field_map = {
        "delivered": ("outreach_status", "delivered", None),
        "opened":    ("outreach_status", "opened",    "last_opened_at"),
        "clicked":   ("outreach_status", "clicked",   "last_clicked_at"),
        "replied":   ("outreach_status", "replied",   "replied_at"),
        "bounced":   ("outreach_status", "bounced",   None),
        "failed":    ("outreach_status", "failed",    None),
    }
    if event_type not in field_map:
        return False
    _, status, ts_col = field_map[event_type]
    fields = {"outreach_status": status}
    if ts_col:
        fields[ts_col] = _now()
    # increment counters via PostgREST is a separate RPC; for simplicity we
    # rely on engagement booleans + last-event timestamp for now.
    return update_outreach(email, **fields)
