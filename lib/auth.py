"""Cron auth. Vercel sends Authorization: Bearer <CRON_SECRET> on cron hits."""
import hmac
import os


def is_cron_authorized(headers) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        # Fail CLOSED in production (F08): a missing secret must never silently
        # open the cron surface. Only allow open in non-prod (preview/local),
        # where the secret may legitimately be unset.
        return os.environ.get("VERCEL_ENV", "development") != "production"
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return hmac.compare_digest(auth, f"Bearer {secret}")
