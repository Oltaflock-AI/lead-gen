"""Prospeo REST client — person-level data for decision-maker mode.

Proven precedent: scripts/enrich_remaining.py successfully called
POST https://api.prospeo.io/v1/person/enrich with an
`Authorization: Bearer <PROSPEO_API_KEY>` header and got back
{"success": true, "data": {"found": true, "person": {"email": {"email": ...}}}}.
The flattened person schema proven by data/outputs/travel_agencies_75_leads.csv:
first_name, last_name, full_name, job_title, email, email_status ("VERIFIED"),
linkedin_url, industry, employee_count, revenue_range.

Only person/enrich is proven by that script; person/search, company/enrich and
account/info follow the same v1 pattern and are marked UNPROVEN below.

Defensive by design: every function returns {} / [] on 4xx/5xx, timeouts,
missing key or malformed JSON. Nothing here raises into the cron path.
PII policy: never log email addresses or names — paths + status codes only.

Credit budget: the enrich cron caps paid calls per tick via reset_budget(n).
Scripts that import this module directly get an unlimited budget (None).
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.prospeo.io/v1"
TIMEOUT = 30

# Per-invocation call budget. None = unlimited (manual/script use). The cron
# calls reset_budget(N) at the start of each tick so a warm serverless
# container can never burn more than N paid calls per invocation.
_budget: dict = {"remaining": None}


def _api_key() -> str:
    return os.environ.get("PROSPEO_API_KEY", "")


def enabled() -> bool:
    """True when a Prospeo key is configured."""
    return bool(_api_key())


def reset_budget(n: int | None) -> None:
    """Set the number of paid Prospeo calls allowed until the next reset."""
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


def _post(path: str, payload: dict, *, count_budget: bool = True) -> dict:
    """POST to Prospeo. Returns the parsed 'data' dict, or {} on any failure."""
    key = _api_key()
    if not key:
        return {}
    if count_budget and not _consume_budget():
        log.info("prospeo %s skipped: per-tick budget exhausted", path)
        return {}
    try:
        r = requests.post(
            f"{BASE_URL}/{path.lstrip('/')}",
            json=payload,
            headers={
                # scripts/enrich_remaining.py (proven working) authenticates
                # with Bearer; Prospeo's public docs use X-KEY. Send both.
                "Authorization": f"Bearer {key}",
                "X-KEY": key,
                "Content-Type": "application/json",
            },
            timeout=TIMEOUT,
        )
        if r.status_code >= 400:
            log.info("prospeo %s -> HTTP %s", path, r.status_code)
            return {}
        body = r.json()
        if not isinstance(body, dict) or not body.get("success", True):
            log.info("prospeo %s -> success=false", path)
            return {}
        data = body.get("data")
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.info("prospeo %s failed: %s", path, type(e).__name__)
        return {}


def _norm_person(obj: dict) -> dict:
    """Flatten a Prospeo person payload (nested or flat) into the CSV-proven
    schema. Drops empty fields. Returns {} for garbage."""
    if not isinstance(obj, dict):
        return {}
    person = obj.get("person") if isinstance(obj.get("person"), dict) else obj
    company: dict = {}
    for src in (obj.get("company"), person.get("company")):
        if isinstance(src, dict):
            company = src
            break

    email_field = person.get("email")
    if isinstance(email_field, dict):  # proven shape: {"email": ..., ...}
        email = email_field.get("email")
        email_status = email_field.get("email_status") or email_field.get("status")
    else:
        email = email_field if isinstance(email_field, str) else None
        email_status = person.get("email_status")

    first = person.get("first_name")
    last = person.get("last_name")
    full = person.get("full_name") or " ".join(x for x in (first, last) if x) or None

    out = {
        "person_id": person.get("person_id") or person.get("id"),
        "first_name": first,
        "last_name": last,
        "full_name": full,
        "job_title": person.get("job_title") or person.get("title"),
        "linkedin_url": person.get("linkedin_url") or person.get("linkedin"),
        "email": email,
        "email_status": email_status,
        "industry": company.get("industry") or person.get("industry"),
        "employee_count": company.get("employee_count") or person.get("employee_count"),
        "revenue_range": company.get("revenue_range") or person.get("revenue_range"),
        "company_name": company.get("company_name") or company.get("name"),
        "company_domain": company.get("company_domain") or company.get("domain")
                          or company.get("website"),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _best_hit(hits: list[dict]) -> dict:
    """Pick the most promising search result: verified email first, then any
    email preview, then the first hit. Deterministic (stable sort)."""
    def rank(h: dict) -> int:
        if str(h.get("email_status") or "").upper() == "VERIFIED":
            return 0
        if h.get("email"):
            return 1
        return 2
    return sorted(hits, key=rank)[0] if hits else {}


def enrich_person(company_domain: str | None = None, *, full_name: str | None = None,
                  first_name: str | None = None, last_name: str | None = None,
                  job_titles: list[str] | None = None, person_id: str | None = None,
                  linkedin_url: str | None = None, only_verified_email: bool = True) -> dict:
    """Find + enrich one person. Returns a flat person dict or {}.

    With an identity (person_id / linkedin_url / name), enriches directly
    (PROVEN endpoint). With only company_domain + job_titles, searches for a
    matching person first, then enriches the best hit (may cost 2 calls).
    """
    payload: dict = {"only_verified_email": bool(only_verified_email)}
    for k, v in (("person_id", person_id), ("linkedin_url", linkedin_url),
                 ("full_name", full_name), ("first_name", first_name),
                 ("last_name", last_name), ("company_website", company_domain)):
        if v:
            payload[k] = v

    if not any((person_id, linkedin_url, full_name, first_name)):
        if not (company_domain and job_titles):
            return {}
        best = _best_hit(search_person(company_domain, list(job_titles)))
        if best.get("person_id"):
            payload["person_id"] = best["person_id"]
        elif best.get("linkedin_url"):
            payload["linkedin_url"] = best["linkedin_url"]
        elif best.get("full_name"):
            payload["full_name"] = best["full_name"]
        else:
            return {}

    data = _post("person/enrich", payload)
    if not data or data.get("found") is False:
        return {}
    return _norm_person(data)


def search_person(company_domain: str, job_titles: list[str]) -> list[dict]:
    """Search Prospeo for people at a company domain matching job titles.

    UNPROVEN endpoint: only person/enrich is proven by scripts/enrich_remaining.py;
    the path + filter grammar here follow Prospeo's Search Person API. Fails
    soft to []. Emails in search results are obfuscated previews — enrich the
    person_id to reveal them.
    """
    if not company_domain:
        return []
    filters: dict = {"company": {"websites": {"include": [company_domain]}}}
    titles = [t.strip() for t in (job_titles or []) if isinstance(t, str) and t.strip()]
    if titles:
        filters["person_job_title"] = {"include": titles[:25], "match_mode": "CONTAINS"}
    data = _post("person/search", {"filters": filters, "page": 1})
    for key in ("results", "persons", "people", "items"):
        items = data.get(key)
        if isinstance(items, list):
            return [p for p in (_norm_person(i) for i in items if isinstance(i, dict)) if p]
    return []


def enrich_company(domain: str) -> dict:
    """Firmographics for a company domain. Returns a flat dict or {}.

    UNPROVEN endpoint (see search_person note). Fails soft to {}.
    """
    if not domain:
        return {}
    data = _post("company/enrich", {"company_website": domain})
    if not data or data.get("found") is False:
        return {}
    company = data.get("company") if isinstance(data.get("company"), dict) else data
    out = {
        "company_name": company.get("company_name") or company.get("name"),
        "company_domain": company.get("company_domain") or company.get("domain")
                          or company.get("website") or domain,
        "industry": company.get("industry"),
        "employee_count": company.get("employee_count"),
        "employee_range": company.get("employee_range"),
        "revenue_range": company.get("revenue_range"),
        "founded": company.get("founded"),
        "company_linkedin": company.get("company_linkedin") or company.get("linkedin_url"),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def account_info() -> dict:
    """Credit balance / plan info. UNPROVEN endpoint; free per Prospeo docs,
    so it does not consume the per-tick budget. Returns {} on any failure."""
    return _post("account/info", {}, count_budget=False)
