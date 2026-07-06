"""App users: login + per-user identity for the dashboard.

Data access is SHARED — every user sees everything. Auth is identity only:
who is logged in drives which signature and default From address the composer
uses, and the display name stamped on a manual send.

Passwords come from the LEADGEN_USERS env var (JSON: {username: password}).
The login cookie is HMAC-signed with LEADGEN_SECRET (falls back to DASHBOARD_KEY)
so it cannot be forged client-side.
"""
import base64
import hashlib
import hmac
import json
import os

from lib import supabase as sb

DOMAIN = os.environ.get("LEADGEN_MAIL_DOMAIN", "oltaflock.ai")
_PBKDF2_ITERS = 120_000

# Shared Oltaflock discovery-call booking link. Override with BOOKING_LINK.
BOOKING_LINK = os.environ.get(
    "BOOKING_LINK", "https://cal.com/khush0030/oltaflock-ai-discovery-call")


def _sigs(name: str, title: str = "Oltaflock AI") -> list[dict]:
    """Selectable signature variants. No em/en dashes (house rule), and no
    booking link in any outbound email (removed per operator request)."""
    short = f"{name}\n{title}\n{DOMAIN}"
    return [
        {"label": "Standard", "text": short},
    ]


# Ordered so the From dropdown and login list are stable.
USERS: dict[str, dict] = {
    "khush":  {"name": "Khush Mutha",   "email": f"khush@{DOMAIN}",  "signatures": _sigs("Khush Mutha", "Founder, Oltaflock AI")},
    "vineet": {"name": "Vineet Patel",  "email": f"vineet@{DOMAIN}", "signatures": _sigs("Vineet Patel")},
    "amaan":  {"name": "Amaan Barmare", "email": f"amaan@{DOMAIN}",  "signatures": _sigs("Amaan Barmare")},
}


def by_email(email: str) -> dict | None:
    """Resolve the user identity for a selected From address (case-insensitive).
    Used so a manual send's signature + display name follow the chosen From,
    not just whoever is logged in. Returns None for shared/unknown addresses."""
    if not email:
        return None
    e = email.strip().lower()
    for u in USERS.values():
        if u["email"].lower() == e:
            return u
    return None

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


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt_b64, hash_b64 = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(base64.b64encode(dk).decode(), hash_b64)
    except Exception:
        return False


def _db_hash(username: str) -> str | None:
    """Custom password hash from the app_users table, or None if unset/missing
    (table not created yet, network error, etc. — caller falls back to env)."""
    try:
        rows = sb.select("app_users", {"select": "password_hash", "username": f"eq.{username}"}, limit=1)
        return rows[0]["password_hash"] if rows else None
    except Exception:
        return None


def verify_login(username: str, password: str) -> bool:
    username = (username or "").strip().lower()
    if username not in USERS:
        return False
    stored = _db_hash(username)
    if stored:
        return verify_password(password or "", stored)
    # No custom password yet — fall back to the env-provided default.
    pw = _passwords().get(username)
    return bool(pw) and hmac.compare_digest(str(pw), password or "")


def set_password(username: str, new_password: str) -> tuple[bool, str]:
    """Persist a user's new password hash. Returns (ok, error_message)."""
    username = (username or "").strip().lower()
    if username not in USERS:
        return False, "Unknown user."
    if len(new_password or "") < 8:
        return False, "Password must be at least 8 characters."
    try:
        sb.insert("app_users", {"username": username, "password_hash": hash_password(new_password)},
                  on_conflict="username")
        return True, ""
    except Exception:
        return False, ("Password store is not set up yet. Apply the app_users "
                       "table (supabase/migrations/003_app_users.sql), then retry.")


def _secret() -> bytes:
    s = os.environ.get("LEADGEN_SECRET") or os.environ.get("DASHBOARD_KEY")
    if not s:
        # Never sign login cookies with a guessable constant in production (F08) —
        # that would let anyone forge a valid session for a known username.
        if os.environ.get("VERCEL_ENV") == "production":
            raise RuntimeError(
                "LEADGEN_SECRET (or DASHBOARD_KEY) must be set in production — "
                "refusing to sign auth cookies with a default key."
            )
        s = "dev-secret"
    return s.encode()


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
