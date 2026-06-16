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


def search_places(text_query: str, *, max_pages: int = 3, sleep_between_pages: float = 1.5) -> list[dict]:
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


def scrape_for_campaign(campaign: dict, *, target: int | None = None) -> Iterable[dict]:
    """Yield normalized lead dicts for one campaign. Caller dedups + persists.

    Builds 1-3 search queries from niche × region to maximize unique hits.
    """
    niche = campaign["niche"].strip()
    region = campaign["region"].strip()
    target = target or campaign.get("daily_scrape_target") or 50

    queries = [
        f"{niche} in {region}",
        f"best {niche} {region}",
        f"top {niche} {region}",
    ]

    seen_place_ids: set[str] = set()
    yielded = 0
    for q in queries:
        if yielded >= target:
            break
        places = search_places(q, max_pages=2)
        for p in places:
            pid = p.get("id")
            if not pid or pid in seen_place_ids:
                continue
            seen_place_ids.add(pid)
            row = normalize(p, campaign_id=campaign["id"])
            if not row:
                continue
            yield row
            yielded += 1
            if yielded >= target:
                break
