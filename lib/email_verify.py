"""MillionVerifier PAYG client + pattern-guess email discovery.

Zero-subscription email finding for US/CA/AU leads: generate likely address
patterns from a decision-maker's name + the company domain, then verify each
candidate against MillionVerifier's single-verify API (~$0.0037/check) until
one comes back mailbox-verified ("ok").

Endpoint (verified against developer.millionverifier.com, Jul 2026):
  GET https://api.millionverifier.com/api/v3/?api=<KEY>&email=<addr>&timeout=<2-60>
  → {"email", "quality", "result", "resultcode", "subresult", "free", "role",
     "didyoumean", "credits", "executiontime", "error", "livemode"}
  result ∈ ok | catch_all | unknown | error | disposable | invalid
  ("error" is folded into "unknown" here — both mean "no verdict").

Defensive by design (mirrors lib/prospeo.py): every function returns {} on
4xx/5xx, timeouts, missing key or malformed JSON. Nothing raises into the
cron path. PII policy: never log email addresses or names — results/status
codes only.

Credit budget: the enrich cron caps paid checks per tick via reset_budget(n)
(LEADGEN_VERIFY_PER_TICK, default 20). Scripts importing this module directly
get an unlimited budget (None). Catch-all domains are memoised per warm
container so we never burn repeat credits on domains that accept anything.
"""
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.millionverifier.com/api/v3/"
TIMEOUT = 15          # our HTTP timeout (seconds)
MV_TIMEOUT_S = 10     # MillionVerifier server-side check timeout (param, 2-60)

VALID_RESULTS = {"ok", "catch_all", "unknown", "disposable", "invalid"}

_LOCAL_RE = re.compile(r"^[a-z0-9][a-z0-9._%+\-]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}$")

# Per-invocation check budget. None = unlimited (manual/script use). The cron
# calls reset_budget(N) each tick so a warm serverless container can never
# burn more than N paid checks per invocation.
_budget: dict = {"remaining": None}

# Domains MillionVerifier reported as catch-all — pattern-verification is
# meaningless there (every address "exists"), so we bail instead of burning
# credits. Memoised for the life of the warm container; size-capped.
_catch_all_domains: set = set()
_CATCH_ALL_CAP = 2000


def _api_key() -> str:
    return os.environ.get("MILLIONVERIFIER_API_KEY", "")


def enabled() -> bool:
    """True when a MillionVerifier key is configured."""
    return bool(_api_key())


def reset_budget(n: int | None) -> None:
    """Set the number of paid verification checks allowed until next reset."""
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


def is_catch_all_domain(domain: str) -> bool:
    return (domain or "").strip().lower() in _catch_all_domains


def _mark_catch_all(domain: str) -> None:
    if len(_catch_all_domains) < _CATCH_ALL_CAP:
        _catch_all_domains.add(domain)


# ─────────── Single verify ───────────
def verify(email: str) -> dict:
    """One MillionVerifier check. Consumes one budget unit.

    Returns {"result": "ok"|"catch_all"|"unknown"|"disposable"|"invalid",
    "quality": ..., "free": ..., "role": ..., "subresult": ...} or {} on any
    failure / missing key / exhausted budget. Never raises.
    """
    email = (email or "").strip().lower()
    key = _api_key()
    if not key or "@" not in email:
        return {}
    if not _consume_budget():
        log.info("millionverifier check skipped: per-tick budget exhausted")
        return {}
    try:
        r = requests.get(
            BASE_URL,
            params={"api": key, "email": email, "timeout": MV_TIMEOUT_S},
            timeout=TIMEOUT,
        )
        if r.status_code >= 400:
            log.info("millionverifier -> HTTP %s", r.status_code)
            return {}
        body = r.json()
        if not isinstance(body, dict):
            return {}
        if body.get("error"):
            # API-level error (bad key, no credits, ...). Message is not PII.
            log.info("millionverifier -> error: %s", str(body.get("error"))[:80])
            return {}
        result = str(body.get("result") or "").strip().lower()
        if result not in VALID_RESULTS:
            result = "unknown"   # includes MV's "error" verdict
        log.info("millionverifier -> %s", result)
        return {
            "result": result,
            "quality": body.get("quality"),
            "subresult": body.get("subresult"),
            "free": body.get("free"),
            "role": body.get("role"),
            "credits": body.get("credits"),
        }
    except Exception as e:
        log.info("millionverifier check failed: %s", type(e).__name__)
        return {}


# ─────────── Pattern generation ───────────
def _clean_name_part(s: str) -> str:
    """'O'Brien' → 'obrien', ' De La Cruz ' → 'delacruz'. '' for garbage."""
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def candidates(first: str, last: str, domain: str) -> list[str]:
    """Ordered likely-address candidates for a person at a domain.

    Order (most→least common in small US/CA/AU businesses): first,
    first.last, flast, firstl, first_last, last.first. Lowercase, deduped,
    valid-format only. Degrades gracefully when last is missing.
    """
    f = _clean_name_part(first)
    l = _clean_name_part(last)
    d = (domain or "").strip().lower().removeprefix("www.")
    if not f or not _DOMAIN_RE.match(d):
        return []
    locals_ = [f]
    if l:
        locals_ += [f"{f}.{l}", f"{f[0]}{l}", f"{f}{l[0]}", f"{f}_{l}", f"{l}.{f}"]
    out: list[str] = []
    for loc in locals_:
        if _LOCAL_RE.match(loc) and len(loc) + len(d) < 79:
            addr = f"{loc}@{d}"
            if addr not in out:
                out.append(addr)
    return out


# ─────────── Pattern-verify waterfall ───────────
def find_email_by_pattern(first: str, last: str, domain: str,
                          budget: int | None = None,
                          extra_candidates: list[str] | None = None) -> dict:
    """Guess-and-verify a personal address for first/last @ domain.

    Checks candidates in order and returns on the first "ok":
      {"email", "email_status": "valid", "email_confidence": "verified",
       "email_source": "pattern-verify"}
    Catch-all guard: the moment any candidate returns "catch_all" the domain
    is memoised as catch-all and we bail with {} — verification is
    meaningless there and we refuse to burn further credits.

    `budget` optionally caps checks for THIS call (on top of the module
    per-tick budget). `extra_candidates` (e.g. a scraped personal address on
    the same domain) are verified first. Returns {} on miss. Never raises.
    """
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not enabled() or not domain or is_catch_all_domain(domain):
        return {}
    cands: list[str] = []
    for e in (extra_candidates or []):
        e = (e or "").strip().lower()
        if "@" in e and e.split("@")[-1] == domain and e not in cands:
            cands.append(e)
    for c in candidates(first, last, domain):
        if c not in cands:
            cands.append(c)
    if not cands:
        return {}

    checks = len(cands) if budget is None else max(0, min(budget, len(cands)))
    for cand in cands[:checks]:
        if budget_remaining() == 0:
            break
        res = verify(cand)
        result = res.get("result")
        if result == "ok":
            return {"email": cand, "email_status": "valid",
                    "email_confidence": "verified",
                    "email_source": "pattern-verify"}
        if result == "catch_all":
            _mark_catch_all(domain)
            log.info("millionverifier: domain is catch-all, bailing")
            return {}
        # invalid / disposable / unknown / {} → try the next pattern.
    return {}


def verify_existing(email: str) -> dict:
    """Single-check an already-found address and report honest confidence.

    For registry/CSV imports where an email arrived unverified. Consumes one
    budget unit. Returns {"email", "email_confidence"} with confidence one of
    "verified" (ok), "catch_all", "invalid", "disposable" or "unknown" — or
    {} on API failure / missing key / exhausted budget (caller keeps
    whatever confidence it already had). Never raises.
    """
    email = (email or "").strip().lower()
    if not enabled() or "@" not in email:
        return {}
    res = verify(email)
    result = res.get("result")
    if not result:
        return {}
    confidence = "verified" if result == "ok" else result
    out = {"email": email, "email_confidence": confidence}
    if result == "catch_all":
        _mark_catch_all(email.split("@")[-1])
    return out
