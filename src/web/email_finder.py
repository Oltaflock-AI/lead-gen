"""Deep email enrichment.

Difference vs `processors.enrich_leads.find_email`: that one only scans
DDG result snippets. This one fetches the top result URLs and the
business's Facebook page (when surfaced) and regex-scans the HTML —
catches addresses buried in contact pages and `mailto:` links that
never appear in snippets. Costs more time per lead but lifts hit-rate
on no-website businesses by ~2-3×.
"""
import logging
import re
import time
from urllib.parse import urlparse, urljoin

import requests
from ddgs import DDGS

from src.processors.enrich_leads import JUNK_DOMAINS, verify_mx
from .email_quality import is_disposable, score_email

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)
MAILTO_RE = re.compile(r"mailto:([^\"'<>\s?]+)", re.IGNORECASE)
TAGS_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_STRIP_RE = re.compile(r"<[^>]+>")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
FETCH_TIMEOUT = 8
MAX_BYTES = 500_000
PER_LEAD_BUDGET_SEC = 18  # walk away once a lead has eaten this much wall-time

# Directory / social pages tend to carry contact emails. Prefer these in
# the URL fetch order so the budget is spent on high-yield pages first.
PREFERRED_DOMAINS = (
    "facebook.com", "yelp.com", "yellowpages.com", "bbb.org",
    "thumbtack.com", "manta.com", "foursquare.com", "tripadvisor.com",
    "instagram.com",
)


def _fetch(url):
    """Return text body (≤ MAX_BYTES, decoded) or None on failure."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html,*/*"},
            timeout=FETCH_TIMEOUT,
            stream=True,
            allow_redirects=True,
        )
        r.raise_for_status()
        ct = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ct and "text" not in ct:
            return None
        body = r.raw.read(MAX_BYTES, decode_content=True) or b""
        return body.decode(r.encoding or "utf-8", errors="replace")
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
        return None


def _extract_emails(html):
    """Extract emails from raw HTML — both visible text and mailto: hrefs."""
    if not html:
        return []
    out = set(MAILTO_RE.findall(html))
    no_scripts = TAGS_RE.sub(" ", html)
    text = TAG_STRIP_RE.sub(" ", no_scripts)
    out.update(EMAIL_RE.findall(text))
    cleaned = []
    for e in out:
        e = e.lower().strip().rstrip(".,;:)\"'")
        if "@" not in e or len(e) > 80:
            continue
        # Drop obvious noise (image filenames, sentry DSNs, asset URLs).
        if any(x in e for x in ("sentry.io", "wixpress.com", ".png", ".jpg", ".gif")):
            continue
        cleaned.append(e)
    return cleaned


def _ddg_results(query, max_results=8):
    try:
        with DDGS() as d:
            return list(d.text(query, max_results=max_results) or [])
    except Exception as e:
        log.debug("ddg failed %r: %s", query, e)
        return []


def _candidate_urls(business_name, region, country):
    """Score URL candidates: preferred-domain hits first, then any results.
    Returns ordered list of (url, source_label) tuples."""
    name = (business_name or "").strip()
    region = (region or "").strip()
    country = (country or "").strip()

    queries = [
        (f"{name} {region} contact", "contact"),
        (f"{name} email", "email"),
        (f"{name} {region}", "plain"),
        (f"{name} facebook", "facebook"),
    ]

    seen = set()
    preferred = []
    other = []
    for q, label in queries:
        if not q.strip() or q.strip() == name and not region:
            continue
        for r in _ddg_results(q):
            href = r.get("href") or r.get("url") or ""
            if not href or href in seen:
                continue
            seen.add(href)
            try:
                host = urlparse(href).hostname or ""
            except Exception:
                host = ""
            host = host.lower()
            if not host:
                continue
            tag = (host, label)
            if any(host.endswith(p) for p in PREFERRED_DOMAINS):
                preferred.append((href, tag))
            else:
                other.append((href, tag))
    return preferred + other


def _score_pick(candidates, business_name):
    """Score, dedupe, and MX-verify the candidate list. Returns (email, src)
    or ('', '')."""
    seen = set()
    scored = []
    for email, src in candidates:
        if email in seen:
            continue
        seen.add(email)
        if "@" not in email:
            continue
        domain = email.split("@", 1)[1].lower()
        if domain in JUNK_DOMAINS:
            continue
        if is_disposable(domain):
            continue
        s, kind, _ = score_email(email, business_name=business_name)
        scored.append((s, kind, email, src))
    if not scored:
        return "", ""
    scored.sort(key=lambda x: (x[0], x[1] == "personal"), reverse=True)
    for _, _, email, src in scored[:10]:
        domain = email.split("@", 1)[1].lower()
        if verify_mx(domain):
            return email, src
    return "", ""


def deep_find_email(business_name, region="", country=""):
    """Two-pass enrichment:
      1. DDG snippets (cheap, in-line with existing find_email).
      2. Fetch top result URLs + Facebook About — scan HTML for emails.

    Returns (email, source_label). source_label is the host or 'snippet'
    so the worker can log where the address came from.
    """
    name = (business_name or "").strip()
    if not name:
        return "", ""

    started = time.time()
    candidates = []  # list of (email, source_label)

    # Pass 1 — snippets (fast).
    for q in (f"{name} {region} contact email",
              f"\"{name}\" email",
              f"{name} {region} contact"):
        for r in _ddg_results(q.strip()):
            text = f"{r.get('title','')} {r.get('body','')} {r.get('href','')}"
            for e in EMAIL_RE.findall(text):
                candidates.append((e.lower().strip().rstrip(".,;:)\"'"), "snippet"))
        if any(c[0] for c in candidates):
            break

    # Pass 2 — fetch top URLs.
    urls = _candidate_urls(name, region, country)
    for href, (host, _label) in urls[:6]:
        if time.time() - started > PER_LEAD_BUDGET_SEC:
            log.debug("deep_find budget hit for %s — stopping at %s", name, host)
            break
        html = _fetch(href)
        if not html:
            continue
        for e in _extract_emails(html):
            candidates.append((e, host))
        # Facebook: also try /about which often holds the contact email.
        if host.endswith("facebook.com") and "/about" not in href:
            about_url = href.rstrip("/") + "/about"
            html = _fetch(about_url)
            if html:
                for e in _extract_emails(html):
                    candidates.append((e, host + "/about"))

    if not candidates:
        return "", ""

    return _score_pick(candidates, name)
