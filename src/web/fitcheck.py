"""Per-lead AI-services fit check.

Hits Google Places with an extended fieldmask, derives signals (after-hours
gap, profile completeness, customer volume) and computes an AI-fit score
0–100. Optionally writes results back into the CSV.
"""
import csv
import math
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

EXTENDED_FIELDMASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.businessStatus",
    "places.types",
    "places.primaryType",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.regularOpeningHours",
    "places.googleMapsUri",
    "places.editorialSummary",
])

FIT_COLS = [
    "ai_fit_score",
    "after_hours_gap",
    "opens_weekend",
    "opening_hours_summary",
    "primary_type",
    "place_id",
]


def _places_lookup(business_name, region="", country=""):
    if not API_KEY:
        return None
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": EXTENDED_FIELDMASK,
    }
    query = f"{business_name} {region} {country}".strip()
    body = {"textQuery": query, "pageSize": 1}
    try:
        r = requests.post(SEARCH_URL, headers=headers, json=body, timeout=15)
        r.raise_for_status()
        places = r.json().get("places", [])
        return places[0] if places else None
    except requests.RequestException:
        return None


def _hours_signals(opening_hours):
    """Return (after_hours_gap_bool, opens_weekend_bool, summary_str)."""
    if not opening_hours:
        return True, False, ""
    periods = opening_hours.get("periods", [])
    if not periods:
        return True, False, ""

    after_hours_gap = False
    opens_weekend = False
    summary_parts = []
    days_seen = set()

    for p in periods:
        open_t = p.get("open", {})
        close_t = p.get("close")
        day = open_t.get("day")  # 0 = Sunday in Places v1
        if day is None:
            continue
        days_seen.add(day)
        if day in (0, 6):
            opens_weekend = True
        if close_t:
            close_hour = close_t.get("hour", 0)
            if close_hour < 20:
                after_hours_gap = True
        else:
            after_hours_gap = False  # 24/7

    if len(days_seen) < 7:
        after_hours_gap = True

    weekday_text = opening_hours.get("weekdayDescriptions", [])
    if weekday_text:
        summary_parts = weekday_text[:7]
    summary = " | ".join(summary_parts) if summary_parts else ("24/7" if not after_hours_gap else "")
    return after_hours_gap, opens_weekend, summary


def _profile_completeness(place):
    keys = ["nationalPhoneNumber", "websiteUri", "regularOpeningHours",
            "rating", "editorialSummary", "primaryType"]
    return sum(1 for k in keys if place.get(k)) / len(keys)


def compute_fit(business_name, region="", country=""):
    place = _places_lookup(business_name, region, country)
    if not place:
        return {
            "ai_fit_score": 0,
            "after_hours_gap": "",
            "opens_weekend": "",
            "opening_hours_summary": "",
            "primary_type": "",
            "place_id": "",
            "error": "place not found",
        }

    rating = place.get("rating", 0) or 0
    reviews = place.get("userRatingCount", 0) or 0
    after_hours_gap, opens_weekend, hours_summary = _hours_signals(place.get("regularOpeningHours"))
    completeness = _profile_completeness(place)

    rating_norm = max(0.0, min(1.0, (rating - 3) / 2))
    volume_norm = min(1.0, math.log10(reviews + 1) / 3) if reviews else 0
    after_hours_norm = 1.0 if after_hours_gap else 0.3  # still some value if 24/7

    fit = 100 * (
        0.30 * after_hours_norm
        + 0.25 * volume_norm
        + 0.20 * rating_norm
        + 0.15 * completeness
        + 0.10 * (0 if place.get("websiteUri") else 1)  # no-website upside (chatbot/voice agent)
    )

    return {
        "ai_fit_score": round(fit, 1),
        "after_hours_gap": "yes" if after_hours_gap else "no",
        "opens_weekend": "yes" if opens_weekend else "no",
        "opening_hours_summary": hours_summary,
        "primary_type": place.get("primaryType", ""),
        "place_id": place.get("id", ""),
    }


def update_csv_fit(csv_path, business_name, fit):
    """Write fit columns into the row matching business_name. Adds columns if
    missing. Returns True if updated.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return False
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    name_col = None
    for c in ("business_name", "Business Name", "prospect_company_name"):
        if c in fieldnames:
            name_col = c
            break
    if name_col is None:
        return False

    for col in FIT_COLS:
        if col not in fieldnames:
            fieldnames.append(col)

    updated = False
    for r in rows:
        if (r.get(name_col) or "").strip() == (business_name or "").strip():
            for col in FIT_COLS:
                r[col] = str(fit.get(col, ""))
            updated = True

    if not updated:
        return False
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return True
