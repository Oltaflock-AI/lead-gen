"""Niche-specific email playbook loader.

Currently supports the Home Services brief (`home-services-offer.md`).
Expand by adding a (matcher, file) entry to `_BRIEFS`.

The brief markdown is injected as a cached system block on cold-email and
sequence-step generation so Claude follows the country/business-type rules
without re-sending tokens on every call.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# (label, niche-name keywords, business-type keywords, filename)
_BRIEFS = [
    {
        "label": "home_services",
        "niche_keywords": {"home services", "home-services"},
        "biz_type_keywords": {
            "plumber", "plumbing", "electrician", "electrical",
            "hvac", "air conditioning", "heating",
            "roofing", "roofer", "landscap", "lawn",
            "cleaning", "cleaner", "pest control",
            "handyman", "painting", "painter",
            "flooring", "carpet", "tile",
        },
        "filename": "home-services-offer.md",
    },
    {
        "label": "real_estate",
        "niche_keywords": {"real estate", "real-estate", "realtor", "realty"},
        "biz_type_keywords": {
            "real estate agent", "realtor", "realty",
            "real estate broker", "real estate brokerage",
            "property management", "property manager",
            "property dealer", "estate agent",
            "real estate consultant", "land developer",
        },
        "filename": "real-estate-offer.md",
    },
]

_CACHE: dict[str, str] = {}

# Country keyword → ISO-ish code used by the brief (matches Section 4.x).
_COUNTRY_HINTS = [
    ("AU", ("australia", " nsw", " vic", " qld", " wa,", " sa,", " tas",
            " nt,", " act", "sydney", "melbourne", "brisbane", "perth",
            "adelaide", "canberra", "hobart", "darwin", "gold coast")),
    ("NZ", ("new zealand", " nz,", "auckland", "wellington", "christchurch",
            "dunedin")),
    ("UK", ("united kingdom", " uk,", "england", "scotland", "wales",
            "london", "manchester", "birmingham", "leeds", "glasgow",
            "edinburgh", "bristol", "liverpool")),
    ("IE", ("ireland", "dublin", "cork", "galway", "limerick")),
    ("CA", ("canada", " ontario", " quebec", " alberta", " bc,", "toronto",
            "vancouver", "montreal", "calgary", "ottawa")),
    ("IN", ("india", " mumbai", " delhi", " bengaluru", "bangalore",
            "chennai", "hyderabad", "pune", "kolkata", "ahmedabad")),
    ("DE", ("germany", "deutschland", "berlin", "munich", "münchen",
            "hamburg", "frankfurt", "köln", "cologne", "stuttgart")),
    ("FR", ("france", "paris", "lyon", "marseille", "toulouse", "bordeaux",
            "nice", "nantes")),
    ("ES", ("spain", "españa", "madrid", "barcelona", "valencia", "sevilla",
            "seville", "bilbao")),
    ("IT", ("italy", "italia", "rome", "roma", "milan", "milano", "naples",
            "napoli", "turin", "torino", "florence", "firenze")),
    ("NL", ("netherlands", "nederland", "amsterdam", "rotterdam", "the hague",
            "utrecht", "eindhoven")),
    ("SE", ("sweden", "sverige", "stockholm", "gothenburg", "göteborg",
            "malmö")),
    ("US", ("united states", " usa,", " us,", "new york", "los angeles",
            "chicago", "houston", "phoenix", "philadelphia", "dallas",
            "austin", "boston", "seattle", "denver", "atlanta", "miami")),
]


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _load_file(filename: str) -> str:
    """Load brief markdown, caching by (filename, mtime) so edits to the
    on-disk file are picked up without restarting Flask."""
    path = PROJECT_ROOT / filename
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        log.warning("brief file unreadable %s: %s", path, e)
        return ""
    cache_key = f"{filename}:{mtime}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("brief file unreadable %s: %s", path, e)
        return ""
    _CACHE.clear()
    _CACHE[cache_key] = text
    return text


def _match_brief(niche: str, business_type: str) -> Optional[dict]:
    n, b = _norm(niche), _norm(business_type)
    for spec in _BRIEFS:
        for kw in spec["niche_keywords"]:
            if kw in n:
                return spec
        for kw in spec["biz_type_keywords"]:
            if kw in b or kw in n:
                return spec
    return None


def infer_country(lead: dict) -> str:
    """Return ISO-ish country code (AU/US/UK/...) inferred from lead fields.
    Empty string when unknown — Claude will fall back to neutral phrasing.
    """
    explicit = _norm(lead.get("country", ""))
    if explicit:
        for code, _ in _COUNTRY_HINTS:
            if explicit in (code.lower(), code.lower() + ","):
                return code
        for code, hints in _COUNTRY_HINTS:
            if any(h.strip(", ") in explicit for h in hints):
                return code
    blob = " ".join(
        _norm(lead.get(k, ""))
        for k in ("address", "city", "country", "business_name")
    )
    if not blob:
        return ""
    for code, hints in _COUNTRY_HINTS:
        for h in hints:
            if h in blob:
                return code
    return ""


def get_brief_for_lead(lead: dict, niche: str = "") -> Optional[dict]:
    """Return {label, country, markdown} when a brief applies, else None.

    Priority: per-niche brief stored in DB > on-disk file mapped by matcher.
    """
    niche_eff = niche or lead.get("niche", "")
    md = _db_brief(niche_eff)
    label = niche_eff or "custom"

    if not md:
        spec = _match_brief(niche_eff, lead.get("business_type", ""))
        if spec:
            md = _load_file(spec["filename"])
            label = spec["label"]

    if not md:
        return None
    return {
        "label": label,
        "country": infer_country(lead),
        "markdown": md,
    }


def _db_brief(niche: str) -> str:
    if not niche:
        return ""
    try:
        from . import db
        rec = db.get_niche_offer(niche)
    except Exception as e:
        log.warning("db brief lookup failed for %r: %s", niche, e)
        return ""
    if not rec:
        return ""
    return (rec.get("brief_md") or "").strip()


def system_blocks(brief: dict) -> list[dict]:
    """Anthropic system blocks for a brief — cache the heavy markdown."""
    country = brief.get("country") or "unknown"
    header = (
        "You have a niche playbook below. Treat it as the single source of "
        "truth for tone, offer wording, currency, voice accent, CRM names, "
        "job-value ranges, localized stats, and country-specific phrases. "
        "Hyper-personalize using the prospect's exact city, business type, "
        "and rating. Pick the country section that matches "
        f"`{country}` (fall back to the closest market if unknown). Obey the "
        "Hard Rules in Section 2 — especially: no em dashes, no buzzwords, "
        "exactly one CTA. IMPORTANT OVERRIDE: do NOT include any signoff, "
        "name, or company name at the end of the body — the pipeline appends "
        "the structured signature from settings. Ignore any signoff "
        "instructions or example signoffs ('Khush, OltaFlock AI', etc.) in "
        "the playbook templates below."
    )
    return [
        {"type": "text", "text": header},
        {
            "type": "text",
            "text": brief["markdown"],
            "cache_control": {"type": "ephemeral"},
        },
    ]


def user_directive(brief: dict, lead: dict) -> str:
    """Tail text appended to the user message so Claude knows which slice."""
    country = brief.get("country") or "infer from address"
    biz = lead.get("business_type") or lead.get("niche") or "home services"
    return (
        f"\n\n--- niche playbook directive ---\n"
        f"Niche: home services. Country: {country}. Business type: {biz}.\n"
        "Use Section 4 for country-specific currency, voice accent, CRM, "
        "stat, and tone. Use Section 5 to choose the opener for the business "
        "type. Generate ONE email matching the requested step/length."
    )
