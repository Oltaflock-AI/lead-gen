"""Clay-style enrichment waterfall.

Inspired by YALC (the open-source GTM OS): instead of one hard-coded email
finder, we run an ordered chain of *providers*. Each provider knows how to
return one or more fields (email / phone / website / linkedin / title …).
The engine walks the chain in priority order and, in the default
"stop at first hit" mode, stops asking new providers for a field the moment
one returns a usable value — exactly how Clay conserves credits.

Design
------
* Every provider is a small adapter with a uniform interface (`fetch`).
* Free providers wrap the finders this repo already ships (website scrape,
  DuckDuckGo search, Google Places). Paid providers (Hunter, FullEnrich,
  Crustdata, Firecrawl) mirror YALC's provider set and are *key-gated*: when
  their API key is absent they report `available() == False` and the engine
  silently skips them, so the waterfall always degrades to the free path.
* The engine records provenance (which provider filled each field, its
  confidence and notional cost) and a step-by-step `trace` the dashboard can
  render.

Nothing here touches the database — `run_waterfall(lead, ...)` is pure and
returns a result dict. Callers (the API routes) own persistence, exactly like
`lib/enrich.py`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests

# Fields the waterfall can resolve. The engine treats these as the
# "targets" it is trying to fill.
TARGET_FIELDS = ("email", "phone", "website", "linkedin", "title")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ─────────────────────────── result types ───────────────────────────
@dataclass
class ProviderResult:
    """What a provider returns for one lead."""
    fields: dict = field(default_factory=dict)   # {"email": "...", ...}
    confidence: float = 0.0                       # 0..1
    cost: float = 0.0                             # notional credits spent
    note: str = ""                                # human-readable outcome


@dataclass
class Provider:
    """A single rung of the waterfall.

    Subclasses implement `fetch(lead) -> ProviderResult | None`. `provides`
    lists the target fields this rung can fill; `requires_env` lists the env
    vars that must be set for it to run (empty == always free/available).
    """
    key: str
    label: str
    provides: tuple
    cost: float = 0.0
    requires_env: tuple = ()

    def available(self) -> bool:
        return all(os.environ.get(v) for v in self.requires_env)

    def fetch(self, lead: dict) -> ProviderResult | None:  # pragma: no cover
        raise NotImplementedError


def _clean_email(value: str) -> str:
    v = (value or "").strip().lower().rstrip(".,;:)\"'")
    return v if _EMAIL_RE.match(v) else ""


def _lead_get(lead: dict, *keys) -> str:
    """Read the first non-empty value across a lead's normalized + raw keys."""
    norm = lead.get("_normalized") or {}
    for k in keys:
        for src in (norm, lead):
            val = src.get(k)
            if val:
                return str(val).strip()
    return ""


# ─────────────────────────── free providers ───────────────────────────
class WebsiteScrapeProvider(Provider):
    """Scrape the lead's own website (home/contact/about) for emails.

    Wraps `email_finder.emails_from_website`. Cheapest, highest-trust source
    for an address, so it sits at the top of the default chain.
    """

    def fetch(self, lead):
        from .email_finder import emails_from_website

        site = _lead_get(lead, "website", "Website")
        if not site:
            return None
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        hits = emails_from_website(site)
        for raw, _src in hits:
            email = _clean_email(raw)
            if email:
                return ProviderResult(
                    fields={"email": email},
                    confidence=0.9,
                    cost=self.cost,
                    note=f"found on {site}",
                )
        return ProviderResult(note="no email on site", cost=self.cost)


class WebSearchProvider(Provider):
    """DuckDuckGo deep search — snippets + top result pages + socials.

    Wraps `email_finder.deep_find_email` (returns the business website as a
    side effect via `find_business_website`).
    """

    def fetch(self, lead):
        from .email_finder import deep_find_email, find_business_website

        name = _lead_get(lead, "business_name", "Business Name", "name")
        if not name:
            return None
        region = _lead_get(lead, "city", "City", "region")
        country = _lead_get(lead, "country", "Country")

        out = {}
        if not _lead_get(lead, "website", "Website"):
            url, score = find_business_website(name, region, country)
            if url and score > 0:
                out["website"] = url
        email, src = deep_find_email(name, region, country)
        email = _clean_email(email)
        if email:
            out["email"] = email
        if not out:
            return ProviderResult(note="nothing found", cost=self.cost)
        return ProviderResult(
            fields=out, confidence=0.6, cost=self.cost,
            note=f"web search via {src or 'ddg'}",
        )


class GooglePlacesProvider(Provider):
    """Google Places Text Search — phone, website, address, rating.

    Key-gated on GOOGLE_PLACES_API_KEY (already used by the scrapers).
    """

    _URL = "https://places.googleapis.com/v1/places:searchText"

    def fetch(self, lead):
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not api_key:
            return None
        name = _lead_get(lead, "business_name", "Business Name", "name")
        if not name:
            return None
        region = _lead_get(lead, "city", "City", "region")
        query = f"{name} {region}".strip()
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.internationalPhoneNumber,places.websiteUri,"
                "places.formattedAddress,places.rating"
            ),
        }
        try:
            r = requests.post(
                self._URL, headers=headers,
                json={"textQuery": query, "maxResultCount": 1}, timeout=12,
            )
            r.raise_for_status()
            places = (r.json() or {}).get("places") or []
        except Exception as exc:  # noqa: BLE001 — provider must never raise
            return ProviderResult(note=f"places error: {exc}", cost=self.cost)
        if not places:
            return ProviderResult(note="no place match", cost=self.cost)
        p = places[0]
        out = {}
        if p.get("internationalPhoneNumber"):
            out["phone"] = p["internationalPhoneNumber"]
        if p.get("websiteUri"):
            out["website"] = p["websiteUri"]
        if not out:
            return ProviderResult(note="place had no phone/site", cost=self.cost)
        return ProviderResult(
            fields=out, confidence=0.85, cost=self.cost, note="google places",
        )


# ─────────────────────── paid providers (key-gated) ───────────────────────
class HunterProvider(Provider):
    """Hunter.io domain search — role/personal emails for a company domain."""

    _URL = "https://api.hunter.io/v2/domain-search"

    def fetch(self, lead):
        api_key = os.environ.get("HUNTER_API_KEY")
        if not api_key:
            return None
        domain = _domain_of(_lead_get(lead, "website", "Website", "email", "Email"))
        if not domain:
            return ProviderResult(note="no domain to query", cost=0.0)
        try:
            r = requests.get(
                self._URL,
                params={"domain": domain, "api_key": api_key, "limit": 5},
                timeout=12,
            )
            r.raise_for_status()
            emails = ((r.json() or {}).get("data") or {}).get("emails") or []
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(note=f"hunter error: {exc}", cost=self.cost)
        # Prefer personal over generic addresses.
        emails.sort(key=lambda e: 0 if e.get("type") == "personal" else 1)
        for e in emails:
            email = _clean_email(e.get("value", ""))
            if email:
                out = {"email": email}
                if e.get("position"):
                    out["title"] = e["position"]
                if e.get("linkedin"):
                    out["linkedin"] = e["linkedin"]
                conf = (e.get("confidence") or 70) / 100.0
                return ProviderResult(
                    fields=out, confidence=conf, cost=self.cost,
                    note=f"hunter ({e.get('type', '?')})",
                )
        return ProviderResult(note="hunter: no emails", cost=self.cost)


class FullEnrichProvider(Provider):
    """FullEnrich waterfall API (YALC provider) — email + phone.

    FullEnrich is itself a waterfall over many vendors; here it is one rung of
    ours. Uses the synchronous single-contact enrich endpoint.
    """

    _URL = "https://app.fullenrich.com/api/v1/contact/enrich/bulk"

    def fetch(self, lead):
        api_key = os.environ.get("FULLENRICH_API_KEY")
        if not api_key:
            return None
        name = _lead_get(lead, "contact_name", "Contact Name", "full_name")
        company = _lead_get(lead, "business_name", "Business Name", "name")
        domain = _domain_of(_lead_get(lead, "website", "Website"))
        if not (name and (company or domain)):
            return ProviderResult(note="needs a contact name", cost=0.0)
        first, _, last = name.partition(" ")
        payload = {
            "name": "waterfall",
            "datas": [{
                "firstname": first, "lastname": last or first,
                "company_name": company, "domain": domain,
                "enrich_fields": ["contact.emails", "contact.phones"],
            }],
        }
        try:
            r = requests.post(
                self._URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload, timeout=20,
            )
            r.raise_for_status()
            data = r.json() or {}
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(note=f"fullenrich error: {exc}", cost=self.cost)
        contact = ((data.get("datas") or [{}])[0]) or {}
        out = {}
        emails = contact.get("emails") or []
        if emails:
            email = _clean_email(emails[0].get("email", "") if isinstance(emails[0], dict) else emails[0])
            if email:
                out["email"] = email
        phones = contact.get("phones") or []
        if phones:
            out["phone"] = phones[0].get("number", "") if isinstance(phones[0], dict) else phones[0]
        if not out:
            return ProviderResult(note="fullenrich: no hit", cost=self.cost)
        return ProviderResult(fields=out, confidence=0.8, cost=self.cost, note="fullenrich")


class CrustdataProvider(Provider):
    """Crustdata person enrichment (YALC provider) — email, title, linkedin."""

    _URL = "https://api.crustdata.com/screener/person/enrich"

    def fetch(self, lead):
        api_key = os.environ.get("CRUSTDATA_API_KEY")
        if not api_key:
            return None
        linkedin = _lead_get(lead, "linkedin", "LinkedIn")
        email = _lead_get(lead, "email", "Email")
        if not (linkedin or email):
            return ProviderResult(note="needs linkedin/email", cost=0.0)
        params = {"linkedin_profile_url": linkedin} if linkedin else {"email": email}
        try:
            r = requests.get(
                self._URL,
                headers={"Authorization": f"Token {api_key}"},
                params=params, timeout=20,
            )
            r.raise_for_status()
            rows = r.json() or []
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(note=f"crustdata error: {exc}", cost=self.cost)
        person = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        out = {}
        found = _clean_email((person.get("email") or (person.get("emails") or [""])[0]) if person else "")
        if found:
            out["email"] = found
        if person.get("title"):
            out["title"] = person["title"]
        if person.get("linkedin_profile_url"):
            out["linkedin"] = person["linkedin_profile_url"]
        if not out:
            return ProviderResult(note="crustdata: no hit", cost=self.cost)
        return ProviderResult(fields=out, confidence=0.8, cost=self.cost, note="crustdata")


class FirecrawlProvider(Provider):
    """Firecrawl scrape (YALC provider) — render a site and pull emails.

    Handy for JS-heavy sites the plain-requests scraper can't read.
    """

    _URL = "https://api.firecrawl.dev/v1/scrape"

    def fetch(self, lead):
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            return None
        site = _lead_get(lead, "website", "Website")
        if not site:
            return ProviderResult(note="no website to scrape", cost=0.0)
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        try:
            r = requests.post(
                self._URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"url": site, "formats": ["markdown"]}, timeout=30,
            )
            r.raise_for_status()
            md = ((r.json() or {}).get("data") or {}).get("markdown", "") or ""
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(note=f"firecrawl error: {exc}", cost=self.cost)
        for raw in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", md):
            email = _clean_email(raw)
            if email:
                return ProviderResult(
                    fields={"email": email}, confidence=0.75,
                    cost=self.cost, note="firecrawl scrape",
                )
        return ProviderResult(note="firecrawl: no email", cost=self.cost)


def _domain_of(url_or_email: str) -> str:
    s = (url_or_email or "").strip().lower()
    if not s:
        return ""
    if "@" in s:
        return s.split("@")[-1]
    s = re.sub(r"^https?://", "", s).split("/")[0]
    return s[4:] if s.startswith("www.") else s


# ─────────────────────────── registry ───────────────────────────
# Order here is the DEFAULT waterfall order (cheapest / highest-trust first).
_REGISTRY: list[Provider] = [
    WebsiteScrapeProvider("website_scrape", "Website scrape", ("email",), cost=0.0),
    GooglePlacesProvider("google_places", "Google Places", ("phone", "website"), cost=1.0,
                         requires_env=("GOOGLE_PLACES_API_KEY",)),
    WebSearchProvider("web_search", "Web search (DDG)", ("email", "website"), cost=0.0),
    HunterProvider("hunter", "Hunter.io", ("email", "title", "linkedin"), cost=1.0,
                   requires_env=("HUNTER_API_KEY",)),
    FullEnrichProvider("fullenrich", "FullEnrich", ("email", "phone"), cost=2.0,
                       requires_env=("FULLENRICH_API_KEY",)),
    CrustdataProvider("crustdata", "Crustdata", ("email", "title", "linkedin"), cost=2.0,
                      requires_env=("CRUSTDATA_API_KEY",)),
    FirecrawlProvider("firecrawl", "Firecrawl", ("email",), cost=1.0,
                      requires_env=("FIRECRAWL_API_KEY",)),
]

REGISTRY: dict[str, Provider] = {p.key: p for p in _REGISTRY}
DEFAULT_ORDER: list[str] = [p.key for p in _REGISTRY]


def catalog() -> list[dict]:
    """Describe every provider for the dashboard config UI."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "provides": list(p.provides),
            "cost": p.cost,
            "available": p.available(),
            "requires_env": list(p.requires_env),
        }
        for p in _REGISTRY
    ]


def normalize_config(cfg: dict | None) -> dict:
    """Coerce a stored/posted config into {order:[...], enabled:{key:bool}, stop_at_first:bool}."""
    cfg = cfg or {}
    raw_order = [k for k in (cfg.get("order") or []) if k in REGISTRY]
    order = raw_order + [k for k in DEFAULT_ORDER if k not in raw_order]
    enabled = {k: True for k in order}
    for k, v in (cfg.get("enabled") or {}).items():
        if k in enabled:
            enabled[k] = bool(v)
    return {
        "order": order,
        "enabled": enabled,
        "stop_at_first": bool(cfg.get("stop_at_first", True)),
    }


# ─────────────────────────── the engine ───────────────────────────
def run_waterfall(lead: dict, config: dict | None = None,
                  targets: tuple = ("email",)) -> dict:
    """Run `lead` through the provider chain.

    Returns:
      {
        "fields":     {field: value}        — newly resolved values,
        "provenance": {field: {source, confidence, cost}},
        "trace":      [{provider, ran, filled, note, cost}],
        "cost":       float,                — total notional credits spent,
        "targets":    [...],                — fields we were hunting,
        "resolved":   [...],                — targets actually filled (pre-existing or new),
      }

    In `stop_at_first` mode (default) a provider is skipped once every target
    field it could contribute is already filled, and the whole chain stops the
    moment all `targets` are satisfied.
    """
    cfg = normalize_config(config)
    stop_at_first = cfg["stop_at_first"]
    targets = tuple(targets) or ("email",)

    resolved = {t: bool(_lead_get(lead, t, t.capitalize(), t.title())) for t in targets}
    working = dict(lead)
    fields: dict = {}
    provenance: dict = {}
    trace: list = []
    total_cost = 0.0

    for key in cfg["order"]:
        provider = REGISTRY[key]
        if not cfg["enabled"].get(key, True):
            continue

        # In stop-at-first mode, only call a provider if it can fill a target
        # we still need. In run-all mode we always call it.
        relevant = [t for t in provider.provides if t in targets]
        still_needed = [t for t in relevant if not resolved.get(t)]
        if stop_at_first and not still_needed:
            trace.append({"provider": provider.label, "ran": False,
                          "filled": [], "note": "skipped — targets already filled",
                          "cost": 0.0})
            continue
        if not provider.available():
            trace.append({"provider": provider.label, "ran": False,
                          "filled": [], "note": "unavailable — missing "
                          + ", ".join(provider.requires_env), "cost": 0.0})
            continue

        try:
            result = provider.fetch(working)
        except Exception as exc:  # noqa: BLE001 — a bad provider must not sink the chain
            trace.append({"provider": provider.label, "ran": True, "filled": [],
                          "note": f"error: {exc}", "cost": 0.0})
            continue
        if result is None:
            trace.append({"provider": provider.label, "ran": False, "filled": [],
                          "note": "no applicable input", "cost": 0.0})
            continue

        total_cost += result.cost
        newly = []
        for f, v in (result.fields or {}).items():
            v = str(v).strip()
            if not v:
                continue
            # Never overwrite a value another rung already resolved.
            if f in fields:
                continue
            # Only accept a target field if it wasn't pre-existing on the lead.
            if f in targets and resolved.get(f):
                continue
            fields[f] = v
            working[f] = v
            provenance[f] = {"source": provider.key, "label": provider.label,
                             "confidence": result.confidence, "cost": result.cost}
            if f in targets:
                resolved[f] = True
            newly.append(f)

        trace.append({"provider": provider.label, "ran": True, "filled": newly,
                      "note": result.note, "cost": result.cost})

        if stop_at_first and all(resolved.get(t) for t in targets):
            break

    return {
        "fields": fields,
        "provenance": provenance,
        "trace": trace,
        "cost": round(total_cost, 2),
        "targets": list(targets),
        "resolved": [t for t in targets if resolved.get(t)],
    }
