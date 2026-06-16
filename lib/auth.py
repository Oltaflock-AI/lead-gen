"""Cron auth. Vercel sends Authorization: Bearer <CRON_SECRET> on cron hits."""
import os


def is_cron_authorized(headers) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return True  # not configured = open (preview only)
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth == f"Bearer {secret}"
