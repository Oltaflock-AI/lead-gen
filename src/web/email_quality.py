"""Email-quality scoring + best-effort SMTP existence probe.

Two independent checks:
  • `classify_local(local)` → 'role' | 'personal' | 'unknown'
  • `smtp_probe(email)`     → 'ok' | 'bad' | 'unknown' (catch-all/blocked/timeout)

Used by verify.py to filter out generic-looking addresses and to mark which
ones the SMTP server actually accepted.
"""
import logging
import os
import re
import smtplib
import socket
from functools import lru_cache

import dns.resolver

log = logging.getLogger(__name__)

# Role / generic locals — anything matching these is "generic as fuck"
ROLE_LOCALS = {
    "info", "contact", "contacts", "hello", "hi", "office", "admin",
    "administrator", "sales", "support", "service", "services", "help",
    "helpdesk", "team", "enquiries", "enquiry", "inquiries", "inquiry",
    "general", "mail", "email", "noreply", "no-reply", "donotreply",
    "marketing", "press", "media", "careers", "jobs", "hr", "billing",
    "accounts", "accounting", "ar", "ap", "bookings", "reservations",
    "feedback", "webmaster", "postmaster", "abuse", "privacy", "legal",
    "wecare", "ask", "letschat", "talk", "newcustomer", "newclient",
    "newpatients", "appointments", "bookkeeping",
}

# A "personal" local looks like a real human: first.last, jdoe, john_smith,
# initials etc. Heuristics rather than a list.
_PERSON_RX = re.compile(
    r"^(?:[a-z]{2,}\.[a-z]{2,}|[a-z]{1,3}[-_.]?[a-z]{3,}|"
    r"[a-z]{2,}[-_.][a-z]{2,})\d{0,3}$",
    re.IGNORECASE,
)

# Free-mail domains — these can never be the business's own address but a real
# tradesperson might still use one. Caller decides whether to accept.
FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "msn.com", "ymail.com",
    "googlemail.com", "protonmail.com", "proton.me", "pm.me",
    "gmx.com", "gmx.net", "yandex.com", "yandex.ru", "mail.com", "zoho.com",
    "rediffmail.com", "fastmail.com",
}

# Throwaway / disposable domains — never trust
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "getnada.com",
    "sharklasers.com", "maildrop.cc", "mintemail.com",
}


def classify_local(local):
    """role / personal / unknown."""
    l = (local or "").lower().strip()
    if not l:
        return "unknown"
    # Strip trailing digits ("info123" still role)
    base = re.sub(r"\d+$", "", l)
    if base in ROLE_LOCALS or l in ROLE_LOCALS:
        return "role"
    if _PERSON_RX.match(l):
        return "personal"
    # Single short word, no separators — probably a first-name alias
    if len(l) <= 12 and l.isalpha():
        return "personal"
    return "unknown"


def is_disposable(domain):
    return (domain or "").lower() in DISPOSABLE_DOMAINS


def is_freemail(domain):
    return (domain or "").lower() in FREEMAIL_DOMAINS


@lru_cache(maxsize=512)
def _mx_host(domain):
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=5)
        recs = sorted(ans, key=lambda r: r.preference)
        return str(recs[0].exchange).rstrip(".")
    except Exception:
        return None


def smtp_probe(email, sender="postmaster@example.com", timeout=8):
    """RCPT-TO probe. Returns 'ok' / 'bad' / 'unknown'.

    'unknown' means the server is a catch-all, timed out, blocked port 25,
    or returned a soft error (4xx). Don't treat 'unknown' as failure — many
    networks block outbound 25.
    """
    if "@" not in email:
        return "bad"
    local, domain = email.rsplit("@", 1)
    if is_disposable(domain):
        return "bad"
    host = _mx_host(domain)
    if not host:
        return "bad"

    try:
        with smtplib.SMTP(host, 25, timeout=timeout) as s:
            s.ehlo_or_helo_if_needed()
            s.mail(sender)
            code, _ = s.rcpt(email)
    except (smtplib.SMTPException, socket.error, socket.timeout, ConnectionError) as e:
        log.debug("smtp_probe %s -> unknown (%s)", email, e)
        return "unknown"

    if 200 <= code < 300:
        # Catch-all check: probe a guaranteed-bogus address. If server also
        # accepts that, the original 'ok' is meaningless.
        if _is_catch_all(host, domain, sender, timeout):
            return "unknown"
        return "ok"
    if 500 <= code < 600:
        return "bad"
    return "unknown"


@lru_cache(maxsize=256)
def _is_catch_all(host, domain, sender, timeout):
    bogus = f"definitely-not-real-{os.urandom(4).hex()}@{domain}"
    try:
        with smtplib.SMTP(host, 25, timeout=timeout) as s:
            s.ehlo_or_helo_if_needed()
            s.mail(sender)
            code, _ = s.rcpt(bogus)
        return 200 <= code < 300
    except Exception:
        return False


def score_email(email, business_name="", website_domain=""):
    """Higher = better. Returns (score, kind, reasons[])."""
    if not email or "@" not in email:
        return -100, "unknown", ["no_at_sign"]
    local, domain = email.rsplit("@", 1)
    local = local.lower()
    domain = domain.lower()
    reasons = []
    score = 0
    kind = classify_local(local)

    if is_disposable(domain):
        return -100, kind, ["disposable_domain"]

    if kind == "role":
        score -= 10
        reasons.append("role_local")
    elif kind == "personal":
        score += 8
        reasons.append("personal_local")

    if is_freemail(domain):
        score -= 4
        reasons.append("freemail_domain")
    else:
        score += 2
        reasons.append("business_domain")

    # Bonus when the local part looks like part of the business name
    if business_name:
        bn = re.sub(r"[^a-z]", "", business_name.lower())
        if bn and len(bn) >= 4 and bn[:6] in local:
            score += 3
            reasons.append("matches_business_name")

    # Bonus when domain matches the business website
    if website_domain:
        wd = website_domain.lower().lstrip("www.")
        if domain == wd:
            score += 4
            reasons.append("matches_website_domain")

    if len(local) > 30:
        score -= 2
        reasons.append("long_local")
    if any(c in local for c in "+="):
        score -= 2
        reasons.append("plus_alias")

    return score, kind, reasons
