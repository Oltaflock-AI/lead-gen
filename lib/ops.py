"""Operational logging, heartbeats + alerting (plan.md Phase 2.2).

Every function here is best-effort and NEVER raises: observability must not be
able to break the pipeline it observes. Alert emails go straight to the Resend
API (no test-guard/suppression — this is ops mail to the operator, not
outreach) and are rate-limited to one email per source per 6h.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from lib import supabase as sb

ALERT_TO = os.environ.get("LEADGEN_ALERT_TO", "admin@oltaflock.ai")
ALERT_WINDOW_H = int(os.environ.get("LEADGEN_ALERT_WINDOW_H", "6"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def log_event(level: str, source: str, message: str, meta: dict | None = None) -> None:
    """Write a system_events row. Falls back to print if the table is missing
    (pre-migration-013) or unreachable."""
    try:
        sb.insert("system_events", {
            "level": level, "source": source,
            "message": str(message)[:2000], "meta": meta or {},
        })
    except Exception as e:
        print(f"[ops:{level}] {source}: {message} (log_event failed: {e})")


def heartbeat(job: str, status: str, body: str = "") -> None:
    """Upsert this job's cron_heartbeats row. last_ok only advances on success,
    so the watchdog sees exactly how long a job has been failing or silent."""
    try:
        row = {
            "job": job, "last_status": status, "last_body": str(body)[:500],
            "updated_at": _now().isoformat(),
        }
        if status == "ok":
            row["last_ok"] = _now().isoformat()
        sb.insert("cron_heartbeats", row, on_conflict="job")
    except Exception as e:
        print(f"[ops] heartbeat({job}) failed: {e}")


def alert(source: str, message: str, meta: dict | None = None) -> None:
    """log_event + email to LEADGEN_ALERT_TO, max one email per source per 6h.
    The 'alert' row is always written; the 'alert-sent' row marks an actual
    email so a failed send never suppresses the next attempt's email."""
    log_event("alert", source, message, meta)
    try:
        cutoff = (_now() - timedelta(hours=ALERT_WINDOW_H)).isoformat()
        recent = sb.select("system_events", {
            "select": "id", "level": "eq.alert-sent",
            "source": f"eq.{source}", "ts": f"gte.{cutoff}",
        }, limit=1)
        if recent:
            return
        key = os.environ.get("RESEND_API_KEY", "")
        if not key:
            print(f"[ops:alert] {source}: {message} (no RESEND_API_KEY, email skipped)")
            return
        frm = os.environ.get("LEADGEN_ALERT_FROM") or os.environ.get("RESEND_FROM", "outreach@oltaflock.ai")
        body = message + ("\n\nmeta:\n" + json.dumps(meta, indent=2, default=str) if meta else "")
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": frm, "to": [ALERT_TO],
                  "subject": f"[lead-gen alert] {source}", "text": body},
            timeout=15,
        )
        if r.status_code < 400:
            sb.insert("system_events", {
                "level": "alert-sent", "source": source, "message": str(message)[:500],
            })
        else:
            print(f"[ops] alert email failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[ops] alert({source}) failed: {e}")
