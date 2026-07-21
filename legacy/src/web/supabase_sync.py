"""Pulls Resend webhook events captured by the Supabase Edge Function and
dispatches them through the same `sequencer.record_event` handler the local
`/webhook/resend` route uses, so the local SQLite analytics stay in sync.

Why a poll instead of a push:
  - Laptop may be off / behind NAT, so Supabase cannot push to us.
  - Resend already pushed the event to Supabase 24/7. We catch up on resume.

Configure with two env vars (Project Settings → API in Supabase dashboard):

    SUPABASE_URL=https://<project-ref>.supabase.co
    SUPABASE_SERVICE_KEY=<service_role JWT>

The service-role key bypasses RLS and is server-only — never ship to a browser.
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
POLL_BATCH_SIZE = int(os.getenv("LEADGEN_SUPABASE_BATCH", "200"))


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _fetch_unprocessed(limit: int = POLL_BATCH_SIZE) -> list[dict]:
    url = f"{SUPABASE_URL}/rest/v1/email_events_raw"
    params = {
        "processed_at": "is.null",
        "select": "id,resend_id,event_type,payload,received_at",
        "order": "received_at.asc",
        "limit": str(limit),
    }
    r = requests.get(url, headers=_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _mark_processed(ids: list[int]) -> None:
    if not ids:
        return
    in_list = ",".join(str(i) for i in ids)
    url = f"{SUPABASE_URL}/rest/v1/email_events_raw"
    params = {"id": f"in.({in_list})"}
    headers = {**_headers(), "Prefer": "return=minimal"}
    r = requests.patch(
        url, headers=headers, params=params,
        json={"processed_at": "now()"}, timeout=10,
    )
    r.raise_for_status()


def sync_once() -> dict:
    """One poll cycle. Safe to call from a background scheduler tick."""
    if not is_configured():
        return {"configured": False}

    from . import sequencer  # local import — sequencer imports db, avoid cycle

    try:
        rows = _fetch_unprocessed()
    except Exception as e:
        log.warning("supabase fetch failed: %s", e)
        return {"error": str(e)}

    if not rows:
        return {"pulled": 0, "applied": 0}

    applied_ids: list[int] = []
    for row in rows:
        try:
            payload = row.get("payload") or {}
            sequencer.record_event(payload)
            applied_ids.append(row["id"])
        except Exception:
            log.exception("apply supabase event failed id=%s", row.get("id"))

    if applied_ids:
        try:
            _mark_processed(applied_ids)
        except Exception:
            log.exception("mark_processed failed")

    return {"pulled": len(rows), "applied": len(applied_ids)}


def reconcile_processed(limit: int = 1000) -> dict:
    """Re-apply already-processed Supabase events to local outreach_log.

    Recovers from the case where an event was poll-pulled BEFORE the
    legacy `resend_id` backfill ran, so `update_outreach_event` could not
    find the matching row. Now that the backfill landed, walk every
    processed event whose resend_id matches an outreach_log row and bring
    the row up to date.

    Idempotent — re-running this is safe; it short-circuits on rows that
    already reflect the latest event.
    """
    if not is_configured():
        return {"configured": False}

    from . import db

    url = f"{SUPABASE_URL}/rest/v1/email_events_raw"
    params = {
        "processed_at": "not.is.null",
        "select": "id,resend_id,event_type,payload,received_at",
        "order": "received_at.asc",
        "limit": str(limit),
    }
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=10)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        return {"error": f"fetch failed: {e}"}

    applied = 0
    skipped = 0
    no_match = 0
    for row in rows:
        rid = row.get("resend_id") or ""
        event = row.get("event_type") or ""
        if not rid:
            skipped += 1
            continue
        # Does outreach_log have a row that needs this event?
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as c:
            c.row_factory = sqlite3.Row
            existing = c.execute(
                "SELECT id, status, opens, clicks, last_event_type "
                "FROM outreach_log WHERE resend_id = ?", (rid,),
            ).fetchone()
        if not existing:
            no_match += 1
            continue
        # Already at this event's status? Skip.
        if existing["last_event_type"] == event:
            skipped += 1
            continue
        if db.update_outreach_event(rid, event):
            applied += 1
            log.info("reconcile applied %s to resend_id=%s", event, rid)
    return {"scanned": len(rows), "applied": applied,
            "skipped": skipped, "no_match": no_match}


def status() -> dict:
    """For the Settings UI — quick reachability check + queue depth."""
    out = {"configured": is_configured(), "url": SUPABASE_URL or None}
    if not out["configured"]:
        return out
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_events_raw"
        # HEAD with Prefer:count=exact returns Content-Range for total rows.
        headers = {**_headers(), "Prefer": "count=exact",
                   "Range-Unit": "items", "Range": "0-0"}
        r = requests.get(
            url, headers=headers,
            params={"select": "id", "processed_at": "is.null"},
            timeout=5,
        )
        cr = r.headers.get("Content-Range", "")
        unprocessed = int(cr.split("/")[-1]) if "/" in cr else None
        out["reachable"] = r.ok
        out["unprocessed_count"] = unprocessed
    except Exception as e:
        out["reachable"] = False
        out["error"] = str(e)
    return out
