"""Email discovery for the autopilot — Google Places gives no email, so we hunt.

Order of attack (cheapest first):
  1. Scrape the lead's website + common contact/about subpages for mailto: and
     text emails.
  2. Fall back to a DuckDuckGo web search for the business's contact page, then
     scrape the top hits.
  3. Verify the winner has a valid MX record (best-effort).

Pure-ish: find_email(lead) returns {email, email_status, email_source} or {}.
"""
import os
import re

from lib import net_guard
from urllib.parse import urlparse

import requests

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\"'<>\s?]+)", re.IGNORECASE)
TAGS_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SUBPAGES = ("", "/contact", "/contact-us", "/about", "/about-us", "/team", "/get-in-touch")
ROLE_PREFIX = ("info", "contact", "hello", "admin", "office", "enquiries", "inquiries",
               "sales", "team", "reception", "bookings", "hi")
NOISE = ("noreply", "no-reply", "donotreply", "example.com", "sentry", "wixpress",
         "@2x", ".png", ".jpg", ".gif", "yourdomain", "domain.com", "email.com")

# Obfuscated emails — REQUIRE brackets/parens around at/dot so we don't match
# normal prose like "...information based...". e.g. "info [at] acme [dot] com".
DEOBFUSCATE = re.compile(r"([A-Za-z0-9._%+\-]+)\s*[\[(]\s*(?:at|@)\s*[\])]\s*([A-Za-z0-9.\-]+)\s*[\[(]\s*(?:dot|\.)\s*[\])]\s*([A-Za-z]{2,})", re.IGNORECASE)


def _domain(url_or_email: str) -> str:
    s = (url_or_email or "").strip()
    if "@" in s:
        return s.split("@")[-1].lower()
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    return (urlparse(s).netloc or "").lower().replace("www.", "")


def _fetch(url: str) -> str:
    try:
        r = net_guard.safe_get(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"},
                               timeout=8)
        ct = (r.headers.get("Content-Type") or "").lower()
        if r.status_code >= 400 or ("html" not in ct and "text" not in ct):
            return ""
        return r.text[:500_000]
    except Exception:
        return ""


def _extract(html: str) -> list[str]:
    if not html:
        return []
    out = set(MAILTO_RE.findall(html))
    text = TAG_STRIP_RE.sub(" ", TAGS_RE.sub(" ", html))
    out.update(EMAIL_RE.findall(text))
    # Catch obfuscated forms like "info [at] acme [dot] com".
    for a, b, c in DEOBFUSCATE.findall(text):
        out.add(f"{a}@{b}.{c}")
    cleaned = []
    for e in out:
        e = e.lower().strip().rstrip(".,;:)\"'")
        if "@" not in e or len(e) > 80:
            continue
        if any(x in e for x in NOISE):
            continue
        cleaned.append(e)
    return list(dict.fromkeys(cleaned))  # dedupe, keep order


def emails_from_website(website: str) -> list[str]:
    if not website:
        return []
    base = website.strip()
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    base = base.rstrip("/")
    found: list[str] = []
    for sub in SUBPAGES:
        html = _fetch(base + sub)
        if html:
            found.extend(_extract(html))
        if len(found) >= 5:
            break
    return list(dict.fromkeys(found))


def _best(emails: list[str], domain: str) -> str | None:
    if not emails:
        return None
    # Prefer addresses on the business's own domain.
    own = [e for e in emails if domain and domain in _domain(e)]
    pool = own or emails

    def rank(e: str) -> int:
        local = e.split("@")[0]
        if local in ROLE_PREFIX:
            return 0
        if any(local.startswith(p) for p in ROLE_PREFIX):
            return 1
        if "." in local or len(local) > 2:  # looks like a person (first.last)
            return 2
        return 3
    return sorted(pool, key=rank)[0]


def _ddg_email_search(business: str, city: str, country: str, domain: str) -> list[str]:
    """Search the web for a contact page, fetch the top hits, scrape emails."""
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception:
            return []
    loc = " ".join([x for x in [city, country] if x])
    queries = [f"{business} {loc} contact email", f"{business} {loc} email"]
    urls: list[str] = []
    try:
        with DDGS() as d:
            for q in queries:
                for r in (d.text(q, max_results=5) or []):
                    href = r.get("href") or r.get("url")
                    if href:
                        urls.append(href)
                if urls:
                    break
    except Exception:
        return []

    found: list[str] = []
    for u in urls[:5]:
        found.extend(_extract(_fetch(u)))
        if found:
            break
    # Prefer ones on the business's own domain.
    return [e for e in found if not domain or domain in _domain(e)] or found


def _has_mx(domain: str) -> bool:
    if not domain:
        return False
    try:
        import dns.resolver
        return bool(dns.resolver.resolve(domain, "MX"))
    except Exception:
        return False


def find_email(lead: dict) -> dict:
    """Hunt for an email. Returns {email, email_status, email_source} or {}."""
    website = lead.get("website") or ""
    domain = _domain(website)

    # 1. website scrape
    emails = emails_from_website(website) if website else []
    pick = _best(emails, domain)
    source = "website"

    # 2. Web-search fallback (DuckDuckGo → contact page scrape)
    if not pick:
        emails = _ddg_email_search(lead.get("business") or "", lead.get("city") or "",
                                   lead.get("country") or "", domain)
        pick = _best(emails, domain)
        source = "web-search"

    if not pick:
        return {}

    # Hard gate: a real business email's domain must have an MX record. This
    # kills junk like "info@ion.based" that slips through text extraction.
    if not _has_mx(_domain(pick)):
        # If the website-domain email lacks MX, try the next-best candidate.
        alt = [e for e in emails if e != pick and _has_mx(_domain(e))]
        if not alt:
            return {}
        pick = _best(alt, domain)

    return {"email": pick, "email_status": "valid", "email_source": source}
