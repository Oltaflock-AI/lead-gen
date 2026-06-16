"""Minimal Google Places (Text Search) scraper for the autopilot.

Self-contained — no dependency on the legacy src/web/scraper.py tangle.
"""
import os
import time
from typing import Iterable

import requests

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.websiteUri",
    "places.formattedAddress",
    "places.internationalPhoneNumber",
    "places.rating",
    "places.userRatingCount",
    "places.types",
    "nextPageToken",
])


def _api_key() -> str:
    k = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not k:
        raise RuntimeError("GOOGLE_PLACES_API_KEY missing")
    return k


def search_places(text_query: str, *, max_pages: int = 3, sleep_between_pages: float = 1.5,
                  region_code: str | None = None) -> list[dict]:
    """Run a single search query, paginate up to max_pages * 20 results."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _api_key(),
        "X-Goog-FieldMask": FIELD_MASK,
    }

    out: list[dict] = []
    page_token: str | None = None
    for _ in range(max_pages):
        body: dict = {"textQuery": text_query, "pageSize": 20}
        if region_code:
            body["regionCode"] = region_code
        if page_token:
            body["pageToken"] = page_token
        r = requests.post(PLACES_URL, headers=headers, json=body, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"places api {r.status_code}: {r.text[:200]}")
        data = r.json()
        out.extend(data.get("places") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(sleep_between_pages)
    return out


def normalize(place: dict, campaign_id: int, source: str = "google_places") -> dict | None:
    """Convert a Google Places hit into a lead row. None if unusable."""
    name = (place.get("displayName") or {}).get("text")
    if not name:
        return None
    return {
        "campaign_id": campaign_id,
        "business": name,
        "website": place.get("websiteUri"),
        "phone": place.get("internationalPhoneNumber"),
        "address": place.get("formattedAddress"),
        "source": source,
        "enrichment_status": "pending",
        "signals": {
            "place_id": place.get("id"),
            "rating": place.get("rating"),
            "user_rating_count": place.get("userRatingCount"),
            "types": place.get("types"),
        },
    }


def _region_code(region: str) -> str | None:
    """Best-effort Google Places regionCode from a free-text region string."""
    from lib.niches import COUNTRY_REGION_CODES
    rl = (region or "").lower()
    for country, code in COUNTRY_REGION_CODES.items():
        if country.lower() in rl or code.lower() == rl.strip():
            return code
    return None


def _passes_filters(place: dict, min_rating: float, min_reviews: int, website_filter: str) -> bool:
    if min_rating and (place.get("rating") or 0) < min_rating:
        return False
    if min_reviews and (place.get("userRatingCount") or 0) < min_reviews:
        return False
    has_site = bool(place.get("websiteUri"))
    if website_filter == "has" and not has_site:
        return False
    if website_filter == "none" and has_site:
        return False
    return True


def scrape_for_campaign(campaign: dict, *, target: int | None = None) -> Iterable[dict]:
    """Yield normalized lead dicts for one campaign. Caller dedups + persists.

    Uses campaign.search_config (business_types + quality filters) when present;
    otherwise falls back to niche × region queries.
    """
    niche = campaign["niche"].strip()
    region = campaign["region"].strip()
    target = target or campaign.get("daily_scrape_target") or 50
    cfg = campaign.get("search_config")
    if not cfg:  # config stored as JSON in `notes` (no dedicated column)
        try:
            parsed = __import__("json").loads(campaign.get("notes") or "{}")
            cfg = parsed if isinstance(parsed, dict) else {}
        except Exception:
            cfg = {}

    types = [t for t in (cfg.get("business_types") or []) if t]
    min_rating = float(cfg.get("min_rating") or 0)
    min_reviews = int(cfg.get("min_reviews") or 0)
    website_filter = cfg.get("website_filter") or "any"
    region_code = _region_code(region)

    if types:
        queries = [f"{t} in {region}" for t in types]
    else:
        queries = [f"{niche} in {region}", f"best {niche} {region}", f"top {niche} {region}"]

    seen_place_ids: set[str] = set()
    yielded = 0
    for q in queries:
        if yielded >= target:
            break
        places = search_places(q, max_pages=2, region_code=region_code)
        for p in places:
            pid = p.get("id")
            if not pid or pid in seen_place_ids:
                continue
            seen_place_ids.add(pid)
            if not _passes_filters(p, min_rating, min_reviews, website_filter):
                continue
            row = normalize(p, campaign_id=campaign["id"])
            if not row:
                continue
            yield row
            yielded += 1
            if yielded >= target:
                break
