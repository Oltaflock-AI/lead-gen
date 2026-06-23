"""App users: login + per-user identity for the dashboard.

Data access is SHARED — every user sees everything. Auth is identity only:
who is logged in drives which signature and default From address the composer
uses, and the display name stamped on a manual send.

Passwords come from the LEADGEN_USERS env var (JSON: {username: password}).
The login cookie is HMAC-signed with LEADGEN_SECRET (falls back to DASHBOARD_KEY)
so it cannot be forged client-side.
"""
import hashlib
import hmac
import json
import os

DOMAIN = os.environ.get("LEADGEN_MAIL_DOMAIN", "oltaflock.ai")
_TAGLINE = "Oltaflock | AI operations for growing businesses"


def _sigs(first: str, full: str) -> list[dict]:
    """Two selectable signature variants. No em/en dashes (house rule)."""
    return [
        {"label": "Short", "text": f"{first}\nOltaflock\n{DOMAIN}"},
        {"label": "Full",  "text": f"{full}\n{_TAGLINE}\n{DOMAIN}"},
    ]


# Ordered so the From dropdown and login list are stable.
USERS: dict[str, dict] = {
    "khush":  {"name": "Khush Mutha",   "email": f"khush@{DOMAIN}",  "signatures": _sigs("Khush", "Khush Mutha")},
    "vineet": {"name": "Vineet Patel",  "email": f"vineet@{DOMAIN}", "signatures": _sigs("Vineet", "Vineet Patel")},
    "amaan":  {"name": "Amaan Barmare", "email": f"amaan@{DOMAIN}",  "signatures": _sigs("Amaan", "Amaan Barmare")},
}

# Shared 'From' addresses every user can pick, in addition to their own.
SHARED_FROM: list[str] = [f"admin@{DOMAIN}"]

# 'From' addresses the composer offers (all @oltaflock.ai — domain is verified
# in Resend, so any local-part sends). Override with LEADGEN_FROM_OPTIONS (CSV).
FROM_OPTIONS: list[str] = [
    e.strip() for e in (os.environ.get("LEADGEN_FROM_OPTIONS") or "").split(",") if e.strip()
] or [u["email"] for u in USERS.values()] + SHARED_FROM


def _passwords() -> dict:
    raw = os.environ.get("LEADGEN_USERS")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def verify_login(username: str, password: str) -> bool:
    pw = _passwords().get((username or "").strip().lower())
    if not pw:
        return False
    return hmac.compare_digest(str(pw), password or "")


def _secret() -> bytes:
    return (os.environ.get("LEADGEN_SECRET") or os.environ.get("DASHBOARD_KEY") or "dev-secret").encode()


def sign(username: str) -> str:
    sig = hmac.new(_secret(), username.encode(), hashlib.sha256).hexdigest()
    return f"{username}.{sig}"


def verify(cookie_val: str | None) -> str | None:
    """Return the username if the cookie is valid and known, else None."""
    if not cookie_val or "." not in cookie_val:
        return None
    username, _, sig = cookie_val.rpartition(".")
    if username not in USERS:
        return None
    good = hmac.new(_secret(), username.encode(), hashlib.sha256).hexdigest()
    return username if hmac.compare_digest(good, sig) else None


def get(username: str) -> dict:
    u = USERS.get(username) or {}
    return {**u, "id": username}
