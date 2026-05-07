"""Deep email + website enrichment.

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


DIRECTORY_DOMAINS = {
    # social
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "pinterest.com", "tumblr.com", "tiktok.com",
    # search / generic
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
    "wikipedia.org",
    # local-business directories
    "yelp.com", "yellowpages.com", "bbb.org", "manta.com", "thumbtack.com",
    "tripadvisor.com", "foursquare.com", "mapquest.com", "yellowbot.com",
    "expertise.com", "angi.com", "homeadvisor.com", "houzz.com",
    "merchantcircle.com", "superpages.com", "citysearch.com",
    "judysbook.com", "kudzu.com", "chamberofcommerce.com",
    # real-estate listings (not the agent's site)
    "zillow.com", "trulia.com", "homes.com", "redfin.com", "realtor.com",
    "compass.com", "movoto.com", "loopnet.com",
    # employment
    "indeed.com", "glassdoor.com", "ziprecruiter.com",
    # blog platforms (rarely the official biz site)
    "wordpress.com", "blogspot.com", "medium.com", "wixsite.com",
    "weebly.com", "godaddysites.com",
    # gov / misc
    "irs.gov", "sec.gov",
}

# Industry/junk path tokens we never treat as the official site.
JUNK_PATH_TOKENS = ("/jobs/", "/news/", "/blog/", "/article", "/wiki/")

# Common stopwords stripped from a business name when matching the domain.
_NAME_STOPWORDS = {
    "the", "and", "of", "co", "inc", "llc", "ltd", "corp", "group",
    "services", "service", "company", "agency", "agent", "agents",
    "real", "estate", "&",
}


def _name_tokens(name):
    """Lowercased, alpha-only tokens stripped of trade-junk stopwords."""
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    parts = [p for p in s.split() if p and p not in _NAME_STOPWORDS]
    return parts


def _is_directory(host):
    host = (host or "").lower().lstrip(".")
    return any(host == d or host.endswith("." + d) for d in DIRECTORY_DOMAINS)


def _website_score(href, title, body, name_tokens):
    """Score a search result on how likely it is to be the business's
    official website. Higher is better; ≥5 means high-confidence."""
    if not href or not name_tokens:
        return 0, ""
    try:
        parsed = urlparse(href)
    except Exception:
        return 0, ""
    host = (parsed.hostname or "").lower()
    if not host or _is_directory(host):
        return 0, host
    if any(t in (parsed.path or "").lower() for t in JUNK_PATH_TOKENS):
        return 0, host
    bare = host.removeprefix("www.")
    sld = bare.split(".")[0] if bare else ""
    title_l = (title or "").lower()
    body_l = (body or "").lower()
    score = 0
    matched = 0
    for tok in name_tokens:
        if not tok or len(tok) < 2:
            continue
        if tok in sld:
            score += 6
            matched += 1
        if tok in title_l:
            score += 2
            matched += 1
        if tok in body_l:
            score += 1
    # Reward when SLD is a clean compose of multiple business tokens.
    if matched >= 2:
        score += 2
    # Slight boost when path is the root.
    if parsed.path in ("", "/"):
        score += 1
    return score, host


def find_business_website(name, region="", country=""):
    """Locate the business's official website via DDG search.

    Returns (url, confidence). confidence ≥ 5 = trust it.
    """
    name = (name or "").strip()
    if not name:
        return "", 0
    tokens = _name_tokens(name)
    if not tokens:
        return "", 0
    queries = []
    if region:
        queries.append(f"{name} {region} official website")
        queries.append(f"{name} {region}")
    queries.append(f"{name} official website")
    queries.append(f'"{name}"')
    seen = set()
    best_score, best_url, best_host = 0, "", ""
    for q in queries:
        for r in _ddg_results(q.strip(), max_results=10):
            href = r.get("href") or r.get("url") or ""
            if not href or href in seen:
                continue
            seen.add(href)
            score, host = _website_score(
                href, r.get("title") or "", r.get("body") or "", tokens,
            )
            if score > best_score:
                best_score, best_url, best_host = score, href, host
        if best_score >= 9:
            break  # confident match — stop spending DDG queries
    if best_score < 5:
        return "", best_score
    return best_url, best_score


def emails_from_website(home_url):
    """Fetch the home page + likely contact/about subpages and return all
    extracted emails. Used after `find_business_website` returns a hit."""
    if not home_url:
        return []
    out = []
    seen_urls = set()
    pages = [home_url]
    # Heuristic subpaths every business site uses.
    for sub in ("/contact", "/contact-us", "/about", "/about-us", "/team"):
        pages.append(urljoin(home_url, sub))
    for url in pages:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        html = _fetch(url)
        if not html:
            continue
        out.extend((e, "website") for e in _extract_emails(html))
    return out


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


def deep_enrich_lead(business_name, region="", country=""):
    """Full enrichment for one lead: discover the business website (if any),
    pull contact-page emails from it, plus the existing snippet + directory
    fallbacks. Returns dict:

      {
        website:       discovered URL (or ''),
        website_score: confidence int (>=5 means trust it),
        email:         best-scoring email,
        email_source:  host or 'snippet' or 'website' or '',
      }
    """
    name = (business_name or "").strip()
    out = {"website": "", "website_score": 0, "email": "", "email_source": ""}
    if not name:
        return out

    started = time.time()
    candidates = []  # list of (email, source_label)

    # 1. Discover the business's official site first — it's the best
    #    source for emails AND it tells us has_website is "yes".
    site_url, site_score = find_business_website(name, region, country)
    if site_url:
        out["website"] = site_url
        out["website_score"] = site_score
        for e, src in emails_from_website(site_url):
            candidates.append((e, src))

    # 2. Snippet pass (cheap, often catches role addresses).
    if time.time() - started < PER_LEAD_BUDGET_SEC:
        for q in (f"{name} {region} contact email",
                  f"\"{name}\" email"):
            for r in _ddg_results(q.strip()):
                text = f"{r.get('title','')} {r.get('body','')} {r.get('href','')}"
                for e in EMAIL_RE.findall(text):
                    candidates.append(
                        (e.lower().strip().rstrip(".,;:)\"'"), "snippet"),
                    )
            if any(c[0] for c in candidates):
                break

    # 3. Directory + Facebook pages — only if we haven't blown the budget.
    if time.time() - started < PER_LEAD_BUDGET_SEC:
        urls = _candidate_urls(name, region, country)
        for href, (host, _label) in urls[:5]:
            if time.time() - started > PER_LEAD_BUDGET_SEC:
                break
            html = _fetch(href)
            if not html:
                continue
            for e in _extract_emails(html):
                candidates.append((e, host))
            if host.endswith("facebook.com") and "/about" not in href:
                html2 = _fetch(href.rstrip("/") + "/about")
                if html2:
                    for e in _extract_emails(html2):
                        candidates.append((e, host + "/about"))

    if candidates:
        email, src = _score_pick(candidates, name)
        out["email"] = email
        out["email_source"] = src
    return out
