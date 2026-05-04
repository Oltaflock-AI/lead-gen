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
    "Mumbai, Maharashtra", "Delhi, India", "Bangalore, Karnataka",
    "Hyderabad, Telangana", "Chennai, Tamil Nadu", "Kolkata, West Bengal",
    "Pune, Maharashtra", "Ahmedabad, Gujarat", "Jaipur, Rajasthan",
    "Lucknow, Uttar Pradesh", "Chandigarh, India", "Indore, Madhya Pradesh",
    "Nagpur, Maharashtra", "Bhopal, Madhya Pradesh", "Coimbatore, Tamil Nadu",
    "Kochi, Kerala", "Surat, Gujarat", "Visakhapatnam, Andhra Pradesh",
    "Thiruvananthapuram, Kerala", "Noida, Uttar Pradesh",
    "Gurgaon, Haryana", "Vadodara, Gujarat", "Mysore, Karnataka",
    "Nashik, Maharashtra", "Patna, Bihar",
]

BUSINESS_TYPES = [
    "restaurant", "cafe", "bakery", "sweet shop", "biryani restaurant",
    "salon", "beauty parlor", "spa", "barber shop",
    "gym", "fitness center", "yoga studio",
    "hospital", "dental clinic", "eye clinic", "diagnostic center",
    "car service center", "car wash", "auto repair shop", "bike service center",
    "hotel", "lodge", "guest house",
    "electronics store", "mobile phone shop", "computer repair shop",
    "clothing store", "saree shop", "jewellery shop",
    "grocery store", "supermarket", "kirana store",
    "tuition center", "coaching institute", "play school", "preschool",
    "real estate agent", "property dealer",
    "interior designer", "architect", "home decor store",
    "plumber", "electrician", "AC repair service",
    "pest control service", "cleaning service", "packers and movers",
    "wedding planner", "event management", "catering service",
    "printing shop", "photography studio", "photo studio",
    "travel agency", "tour operator",
    "pet shop", "veterinary clinic",
    "hardware store", "paint shop", "building material supplier",
    "tailor shop", "laundry service", "dry cleaning service",
    "pharmacy", "medical store",
    "driving school", "dance class", "music class",
    "chartered accountant", "lawyer", "advocate",
]

MIN_REVIEWS = 500
MIN_RATING = 4.0
TARGET_LEADS = 100
OUTPUT_FILE = PROJECT_ROOT / "data" / "outputs" / "businesses_india.csv"
FIELDNAMES = ["Business Name", "City", "Address", "Phone", "Rating", "Reviews", "Business Type", "Google Types"]

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
    "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.businessStatus,places.types,places.rating,places.userRatingCount,nextPageToken",
}

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
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(lead_row)

def process_places(places, city, biz_type):
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
        print(f"  [{leads_count}/{TARGET_LEADS}] {name} | {rating}* | {reviews} reviews | {city}")
    return added

print(f"Target: {TARGET_LEADS} leads | Filters: no website, {MIN_REVIEWS}+ reviews, {MIN_RATING}+ rating")
print(f"Cities: {len(CITIES)} tier 1 Indian cities | Business types: {len(BUSINESS_TYPES)}")
print(f"Saving to: {OUTPUT_FILE}")
print("=" * 60)

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

print("=" * 60)
print(f"Done! {leads_count} leads saved to: {OUTPUT_FILE}")
print(f"Total API calls: {api_calls}")
