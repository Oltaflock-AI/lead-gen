import csv
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_PLACES_API_KEY not found in .env")

CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX",
    "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA",
    "Dallas, TX", "Austin, TX", "Jacksonville, FL", "San Jose, CA",
    "Fort Worth, TX", "Columbus, OH", "Charlotte, NC", "Indianapolis, IN",
    "San Francisco, CA", "Seattle, WA", "Denver, CO", "Nashville, TN",
    "Oklahoma City, OK", "Portland, OR", "Las Vegas, NV", "Memphis, TN",
    "Louisville, KY", "Baltimore, MD", "Milwaukee, WI", "Albuquerque, NM",
    "Tucson, AZ", "Fresno, CA", "Sacramento, CA", "Kansas City, MO",
    "Atlanta, GA", "Miami, FL", "Tampa, FL", "Orlando, FL",
    "Minneapolis, MN", "Cleveland, OH", "Raleigh, NC", "St. Louis, MO",
    "Pittsburgh, PA", "Cincinnati, OH", "New Orleans, LA", "Salt Lake City, UT",
    "Detroit, MI", "Honolulu, HI", "Boise, ID", "Richmond, VA",
    "Birmingham, AL", "Omaha, NE",
]

BUSINESS_TYPES = [
    "landscaping company", "lawn care service", "pool cleaning service",
    "pool installation company", "pool repair service", "sprinkler system installer",
    "irrigation service", "tree trimming service", "garden center", "plant nursery",
    "pressure washing service", "exterior painting contractor", "pest control service",
    "mosquito control service", "termite treatment service", "HVAC installation service",
    "air conditioning repair", "roofing contractor", "gutter cleaning service",
    "gutter installation service", "deck builder", "fence contractor",
    "patio installation", "outdoor kitchen builder", "concrete contractor",
    "paving company", "driveway sealing service", "window cleaning service",
    "solar panel installer", "ice cream shop", "frozen yogurt shop", "smoothie shop",
    "food truck", "outdoor dining restaurant", "car wash", "auto detailing service",
    "boat dealer", "boat repair service", "marina", "jet ski rental",
    "kayak rental", "paddleboard rental", "RV dealer", "RV repair service",
    "moving company", "junk removal service", "wedding florist", "wedding photographer",
    "event tent rental", "party equipment rental", "bounce house rental",
    "summer camp", "tutoring center", "swim lesson instructor", "sporting goods store",
    "bicycle shop", "personal trainer", "outdoor fitness bootcamp", "golf course",
    "mini golf", "water park", "amusement park", "go kart track", "batting cage",
    "paintball field", "zip line tour", "fishing charter", "camping supply store",
    "lawn mower repair", "outdoor furniture store", "hot tub dealer",
    "screen enclosure installer", "awning installer",
]

MIN_REVIEWS = 50
MIN_RATING = 4.0
TARGET_LEADS = 100
OUTPUT_FILE = PROJECT_ROOT / "data" / "outputs" / "seasonal_us.csv"
FIELDNAMES = ["Business Name", "City", "Address", "Phone", "Rating", "Reviews", "Business Type", "Google Types"]

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
    "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.businessStatus,places.types,places.rating,places.userRatingCount,nextPageToken",
}

# Load existing leads if resuming
seen_ids = set()
leads_count = 0
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads_count += 1
    print(f"Resuming — {leads_count} leads already in CSV")
else:
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

api_calls = 0


def search_places(query, page_token=None):
    global api_calls
    body = {"textQuery": query, "pageSize": 20}
    if page_token:
        body["pageToken"] = page_token

    for attempt in range(5):
        try:
            resp = requests.post(SEARCH_URL, headers=HEADERS, json=body, timeout=15)
            api_calls += 1
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"  API error: {e}")
            return {}
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            wait = 10 * (attempt + 1)
            print(f"  Network error, retry in {wait}s... ({attempt+1}/5)")
            time.sleep(wait)
    print("  Giving up on this query")
    return {}


def save_lead(lead_row):
    """Append one lead to CSV immediately."""
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(lead_row)


def process_places(places, city, biz_type):
    """Process places, save qualifying ones. Returns number of new leads."""
    global leads_count
    added = 0
    for place in places:
        if leads_count >= TARGET_LEADS:
            break
        pid = place.get("id")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        website = place.get("websiteUri", "")
        if website:
            continue
        status = place.get("businessStatus", "N/A")
        if status not in ("OPERATIONAL", "N/A"):
            continue
        rating = place.get("rating", 0)
        if rating < MIN_RATING:
            continue
        reviews = place.get("userRatingCount", 0)
        if reviews < MIN_REVIEWS:
            continue

        name = place.get("displayName", {}).get("text", "N/A")
        row = {
            "Business Name": name,
            "City": city,
            "Address": place.get("formattedAddress", "N/A"),
            "Phone": place.get("nationalPhoneNumber", "N/A"),
            "Rating": rating,
            "Reviews": reviews,
            "Business Type": biz_type,
            "Google Types": ", ".join(place.get("types", [])),
        }
        save_lead(row)
        leads_count += 1
        added += 1
        print(f"  [{leads_count}/{TARGET_LEADS}] {name} | {rating}* | {reviews} reviews")
    return added


print(f"Target: {TARGET_LEADS} leads | Filters: no website, {MIN_REVIEWS}+ reviews, {MIN_RATING}+ rating")
print(f"Saving incrementally to: {OUTPUT_FILE}")
print(f"{'='*60}\n")

for ci, city in enumerate(CITIES, 1):
    if leads_count >= TARGET_LEADS:
        break
    print(f"\n[{ci}/{len(CITIES)}] {city}")

    for biz_type in BUSINESS_TYPES:
        if leads_count >= TARGET_LEADS:
            break

        data = search_places(f"{biz_type} in {city}")
        places = data.get("places", [])
        next_token = data.get("nextPageToken")
        process_places(places, city, biz_type)

        while next_token and leads_count < TARGET_LEADS:
            time.sleep(0.1)
            data = search_places(f"{biz_type} in {city}", page_token=next_token)
            places = data.get("places", [])
            next_token = data.get("nextPageToken")
            process_places(places, city, biz_type)

        time.sleep(0.1)

    print(f"  Total so far: {leads_count} leads | {api_calls} API calls")

print(f"\n{'='*60}")
print(f"Done! {leads_count} leads saved to: {OUTPUT_FILE}")
print(f"Total API calls: {api_calls}")
