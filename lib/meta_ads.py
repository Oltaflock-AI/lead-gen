"""Meta Ad Library — "is this business running paid Meta ads?" intent signal.

ACCESS-PATH DECISION (verified July 2026)
    The official Graph API endpoint (graph.facebook.com/ads_archive) still only
    returns political/issue ads worldwide plus EU/UK all-ads (DSA). US/CA/AU
    COMMERCIAL ads have no official API path; programmatic commercial coverage
    is researcher-gated (Meta Content Library via CASD). The public web UI at
    facebook.com/ads/library IS login-free for commercial ads in every country,
    and is served by facebook.com/api/graphql/ using a Relay doc_id.
    We therefore use the public web interface's own JSON endpoint,
    conservatively:
      1. GET  /ads/library/?q=...&country=..  (no login) -> lsd token + HTML
      2. POST /api/graphql/  (doc_id-pinned Relay query)  -> structured results
      3. Fallback: parse "search_results_connection" JSON embedded in the HTML.

FRAGILITY — READ BEFORE TRUSTING
    This is an UNOFFICIAL endpoint. The pinned doc_id values rot when Meta
    ships a new Ad Library build (historically ~monthly). When that happens
    every call fails soft to {} and the signal simply stops updating — nothing
    crashes. Refresh the doc ids from browser devtools (request named
    "AdLibraryMobileFocusedStateProviderRefetchQuery") or set the
    LEADGEN_ADLIB_DOC_ID / LEADGEN_ADLIB_DOC_ID_NEXT env overrides.

CACHING / SIGNAL SHAPE (module itself is stateless)
    The caller stores the result on the lead row:
        leads.signals.meta_ads = {
            "has_active_ads": bool,      # active ads from a matching page
            "ad_count": int | None,      # ads matched to the page, None=unknown
            "checked_at": "<iso8601 utc>",
            "source": "adlibrary_graphql:page-match" | ":no-page-match"
                      | ":no-results" | "adlibrary_embedded:..."
        }
    refresh_engaged_signals() below is that caller for the research cron.

POLITENESS / SAFETY
    OFF by default (LEADGEN_ADLIB_ENABLED=0). Per-tick budget via
    LEADGEN_ADLIB_PER_TICK (default 5 checks; ~2 web requests each), the same
    reset_budget() pattern as lib/prospeo.py, plus a >=2s gap between any two
    requests and 10s HTTP timeouts. Every failure returns {} — nothing raises
    into the cron path. No PII logging: status codes and counts only, never
    query strings or lead fields.
"""
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

ADLIB_URL = "https://www.facebook.com/ads/library/"
GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
FRIENDLY_NAME = "AdLibraryMobileFocusedStateProviderRefetchQuery"
# Relay doc ids observed working mid-2026 (see module docstring: these rot).
DOC_ID_FIRST = os.environ.get("LEADGEN_ADLIB_DOC_ID", "24456302960624351")
DOC_ID_NEXT = os.environ.get("LEADGEN_ADLIB_DOC_ID_NEXT", "24394279933540792")

TIMEOUT = 10
POLITE_GAP_S = 2.0        # minimum spacing between any two requests to Meta
SIGNAL_TTL_DAYS = 30      # re-check a lead's meta_ads signal after this long
CRON_DEADLINE_S = 60      # wall-clock cap inside refresh_engaged_signals()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Per-invocation check budget — identical pattern to lib/prospeo.py. None =
# unlimited (manual/script use). The cron calls reset_budget(N) per tick.
_budget: dict = {"remaining": None}
_last_request = {"t": 0.0}


def enabled() -> bool:
    """Operator kill-switch. Default OFF until LEADGEN_ADLIB_ENABLED=1."""
    return os.environ.get("LEADGEN_ADLIB_ENABLED", "0") == "1"


def per_tick() -> int:
    return int(os.environ.get("LEADGEN_ADLIB_PER_TICK", "5"))


def reset_budget(n: int | None) -> None:
    """Set the number of Ad Library checks allowed until the next reset."""
    _budget["remaining"] = n


def budget_remaining() -> int | None:
    return _budget["remaining"]


def _consume_budget() -> bool:
    rem = _budget["remaining"]
    if rem is None:
        return True
    if rem <= 0:
        return False
    _budget["remaining"] = rem - 1
    return True


def _polite_wait() -> None:
    gap = POLITE_GAP_S - (time.monotonic() - _last_request["t"])
    if gap > 0:
        time.sleep(gap)
    _last_request["t"] = time.monotonic()


# ─────────── Web session: bootstrap page + lsd token ───────────
def _bootstrap(session: requests.Session, query: str, country: str):
    """GET the public Ad Library search page. Returns (lsd, html) or (None, "").
    Treats a redirect to a login page as an explicit block: we stop, never
    work around it."""
    _polite_wait()
    r = session.get(
        ADLIB_URL,
        params={"active_status": "active", "ad_type": "all", "country": country,
                "q": query, "search_type": "keyword_unordered", "media_type": "all"},
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200 or "/login" in r.url:
        log.info("adlibrary bootstrap blocked: HTTP %s login=%s",
                 r.status_code, "/login" in r.url)
        return None, ""
    for pat in (r'"LSD",\[\],\{"token":"([^"]+)"', r'name="lsd" value="([^"]+)"'):
        m = re.search(pat, r.text)
        if m:
            return m.group(1), r.text
    log.info("adlibrary bootstrap: no lsd token in page (layout changed?)")
    return None, r.text


def _variables(query: str, country: str, cursor: str | None, first: int = 30) -> dict:
    # Unknown/extra variables are ignored by GraphQL, so we send a superset to
    # survive small schema drifts ("country" vs "countries").
    return {
        "activeStatus": "ACTIVE", "adType": "ALL", "bylines": [],
        "collationToken": None, "contentLanguages": [], "country": country,
        "countries": [country], "cursor": cursor, "excludedIDs": [],
        "first": first, "location": None, "mediaType": "ALL",
        "multiCountryFilterMode": None, "pageIDs": [], "potentialReachInput": [],
        "publisherPlatforms": [], "queryString": query, "regions": [],
        "searchType": "KEYWORD_UNORDERED", "sessionID": str(uuid.uuid4()),
        "sortData": None, "source": None, "startDate": None, "viewAllPageID": "0",
    }


def _graphql(session: requests.Session, lsd: str, variables: dict, doc_id: str) -> dict:
    """POST one Relay query. Returns parsed JSON dict or {}."""
    _polite_wait()
    r = session.post(
        GRAPHQL_URL,
        data={"av": "0", "__user": "0", "__a": "1", "__req": "1", "dpr": "1",
              "lsd": lsd, "fb_api_caller_class": "RelayModern",
              "fb_api_req_friendly_name": FRIENDLY_NAME,
              "variables": json.dumps(variables, separators=(",", ":")),
              "server_timestamps": "true", "doc_id": doc_id},
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": UA, "X-FB-LSD": lsd, "X-ASBD-ID": "359341",
                 "X-FB-Friendly-Name": FRIENDLY_NAME,
                 "Origin": "https://www.facebook.com", "Referer": ADLIB_URL},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        log.info("adlibrary graphql -> HTTP %s", r.status_code)
        return {}
    text = r.text
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    try:
        body = json.loads(text.split("\n", 1)[0])
    except Exception:
        log.info("adlibrary graphql: non-JSON response (doc_id rotted?)")
        return {}
    if isinstance(body, dict) and body.get("errors"):
        log.info("adlibrary graphql: errors in response (doc_id rotted?)")
        return {}
    return body if isinstance(body, dict) else {}


# ─────────── Result extraction (tolerant of schema drift) ───────────
def _find_connection(obj):
    """Recursively locate the search_results_connection dict anywhere in the
    payload, so parent-path renames don't break us."""
    if isinstance(obj, dict):
        conn = obj.get("search_results_connection")
        if isinstance(conn, dict):
            return conn
        for v in obj.values():
            found = _find_connection(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_connection(v)
            if found is not None:
                return found
    return None


def _balanced_json(text: str, open_idx: int) -> dict | None:
    """Parse the {...} object starting at text[open_idx] via brace matching."""
    depth, in_str, esc = 0, False, False
    for i in range(open_idx, min(len(text), open_idx + 2_000_000)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[open_idx:i + 1])
                except Exception:
                    return None
    return None


def _embedded_connection(html: str) -> dict | None:
    """Fallback: pull search_results_connection out of the server-rendered
    page HTML when the GraphQL doc_id has rotted."""
    for m in re.finditer(r'"search_results_connection"\s*:\s*\{', html):
        conn = _balanced_json(html, m.end() - 1)
        if isinstance(conn, dict) and ("edges" in conn or "count" in conn):
            return conn
    return None


def _advertisers_from(conn: dict) -> dict:
    """Aggregate {key: {page_id, page_name, ad_count}} from connection edges.
    collation_count folds near-duplicate creatives back into the ad count."""
    out: dict = {}
    for edge in conn.get("edges") or []:
        node = edge.get("node") if isinstance(edge, dict) else None
        for res in (node or {}).get("collated_results") or []:
            if not isinstance(res, dict):
                continue
            pid = str(res.get("page_id") or "") or None
            pname = res.get("page_name")
            key = pid or pname
            if not key:
                continue
            a = out.setdefault(key, {"page_id": pid, "page_name": pname, "ad_count": 0})
            try:
                a["ad_count"] += max(1, int(res.get("collation_count") or 1))
            except Exception:
                a["ad_count"] += 1
    return out


def _fetch_first_page(query: str, country: str):
    """One conservative lookup: bootstrap + graphql, embedded-HTML fallback.
    Returns (connection_dict, source_str) or (None, "")."""
    session = requests.Session()
    lsd, html = _bootstrap(session, query, country)
    if lsd:
        body = _graphql(session, lsd, _variables(query, country, None), DOC_ID_FIRST)
        conn = _find_connection(body.get("data")) if body else None
        if isinstance(conn, dict):
            return conn, "adlibrary_graphql"
    conn = _embedded_connection(html) if html else None
    if isinstance(conn, dict):
        return conn, "adlibrary_embedded"
    return None, ""


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").casefold())


# ─────────── Public API ───────────
def check_ad_activity(business_name: str, country: str = "US") -> dict:
    """Is this business currently running Meta ads in `country`?

    Returns {"has_active_ads", "ad_count", "checked_at", "source"} (shape in
    module docstring) or {} on any failure / disabled / budget exhausted.
    Keyword search can match ads that merely MENTION the name, so we only
    count ads whose page name overlaps the business name; other results give
    has_active_ads=False with source ":no-page-match" (honest negative)."""
    name = (business_name or "").strip()
    if not name or not enabled():
        return {}
    if not _consume_budget():
        log.info("adlibrary check skipped: per-tick budget exhausted")
        return {}
    try:
        conn, source = _fetch_first_page(name, (country or "US").upper())
    except Exception as e:
        log.info("adlibrary check failed: %s", type(e).__name__)
        return {}
    if conn is None:
        return {}
    count = conn.get("count") if isinstance(conn.get("count"), int) else None
    advertisers = _advertisers_from(conn)
    target = _norm(name)
    matched = [a for a in advertisers.values()
               if _norm(a.get("page_name")) and target
               and (target in _norm(a.get("page_name")) or _norm(a.get("page_name")) in target)]
    if matched:
        ad_count: int | None = sum(a["ad_count"] for a in matched)
        has, suffix = True, ":page-match"
    elif count == 0 or (not advertisers and count is None):
        ad_count, has, suffix = 0, False, ":no-results"
    else:  # ads mention the name but none belong to a matching page
        ad_count, has, suffix = 0, False, ":no-page-match"
    log.info("adlibrary check ok: source=%s%s ads=%s", source, suffix, ad_count)
    return {"has_active_ads": has, "ad_count": ad_count,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": source + suffix}


def search_advertisers(keyword: str, country: str = "US", *,
                       max_advertisers: int = 50, max_pages: int = 10) -> list[dict]:
    """Keyword discovery: advertisers currently running ads matching `keyword`.

    Returns [{page_id, page_name, ad_count}] sorted by ad_count desc, [] on
    failure. Paginates politely (>=2s between requests). Used by
    scripts/discover_d2c.py; NOT gated on LEADGEN_ADLIB_ENABLED because it is
    only ever run by hand by the operator — the env gate protects the cron."""
    kw = (keyword or "").strip()
    if not kw:
        return []
    country = (country or "US").upper()
    session = requests.Session()
    try:
        lsd, html = _bootstrap(session, kw, country)
    except Exception as e:
        log.info("adlibrary discover bootstrap failed: %s", type(e).__name__)
        return []
    advertisers: dict = {}
    cursor, pages = None, 0
    while pages < max_pages and len(advertisers) < max_advertisers:
        conn = None
        if lsd:
            try:
                doc_id = DOC_ID_FIRST if pages == 0 else DOC_ID_NEXT
                body = _graphql(session, lsd, _variables(kw, country, cursor), doc_id)
                conn = _find_connection(body.get("data")) if body else None
            except Exception as e:
                log.info("adlibrary discover page failed: %s", type(e).__name__)
        if conn is None and pages == 0 and html:
            conn = _embedded_connection(html)  # degraded: first page only
            if conn is not None:
                lsd = None  # no pagination possible on the embedded path
        if not isinstance(conn, dict):
            break
        for key, a in _advertisers_from(conn).items():
            merged = advertisers.setdefault(key, dict(a, ad_count=0))
            merged["ad_count"] += a["ad_count"]
        pages += 1
        info = conn.get("page_info") or {}
        cursor = info.get("end_cursor")
        if not info.get("has_next_page") or not cursor or not lsd:
            break
    out = sorted(advertisers.values(), key=lambda a: -a["ad_count"])[:max_advertisers]
    log.info("adlibrary discover: pages=%s advertisers=%s", pages, len(out))
    return out


# ─────────── Cron-facing caller (stores the signal; see docstring) ───────────
def _is_fresh(sig) -> bool:
    if not isinstance(sig, dict) or not sig.get("checked_at"):
        return False
    try:
        ts = datetime.fromisoformat(str(sig["checked_at"]).replace("Z", "+00:00"))
    except Exception:
        return False
    return (datetime.now(timezone.utc) - ts) < timedelta(days=SIGNAL_TTL_DAYS)


def _country_code(freetext) -> str:
    """Map a lead's free-text country ('Australia') to an ISO code. US default."""
    s = str(freetext or "").strip()
    if re.fullmatch(r"[A-Za-z]{2}", s):
        return s.upper()
    try:
        from lib.niches import COUNTRY_REGION_CODES
        for name, code in COUNTRY_REGION_CODES.items():
            if name.lower() in s.lower():
                return code
    except Exception:
        pass
    return "US"


def refresh_engaged_signals() -> dict:
    """Stamp signals.meta_ads onto engaged leads (active sequence, opens>0)
    missing a fresh signal. Bounded: LEADGEN_ADLIB_PER_TICK checks and a
    CRON_DEADLINE_S wall clock. Called by api/cron/research_tick."""
    if not enabled():
        return {"ok": True, "enabled": False, "checked": 0, "updated": 0}
    from lib import supabase as sb  # lazy: keeps module importable without env
    reset_budget(per_tick())
    start = time.monotonic()
    seqs = sb.select("sequences", {"select": "lead_id", "status": "eq.active",
                                   "opens": "gt.0", "order": "updated_at.desc"},
                     limit=2000)
    lead_ids = list({s["lead_id"] for s in seqs if s.get("lead_id")})
    checked = updated = 0
    for i in range(0, len(lead_ids), 200):
        ids = ",".join(str(x) for x in lead_ids[i:i + 200])
        leads = sb.select("leads", {"select": "id,business,country,signals",
                                    "id": f"in.({ids})"}, limit=200)
        for lead in leads:
            if budget_remaining() == 0 or (time.monotonic() - start) > CRON_DEADLINE_S:
                return {"ok": True, "enabled": True, "engaged": len(lead_ids),
                        "checked": checked, "updated": updated, "stopped": "budget"}
            sig = lead.get("signals") if isinstance(lead.get("signals"), dict) else {}
            if _is_fresh(sig.get("meta_ads")) or not lead.get("business"):
                continue
            res = check_ad_activity(lead["business"], _country_code(lead.get("country")))
            checked += 1
            if res:
                try:
                    sb.update("leads", {"id": lead["id"]},
                              {"signals": {**sig, "meta_ads": res}})
                    updated += 1
                except Exception:
                    log.info("adlibrary signal store failed for one lead")
    return {"ok": True, "enabled": True, "engaged": len(lead_ids),
            "checked": checked, "updated": updated, "stopped": "drained"}
