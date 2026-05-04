import csv
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = PROJECT_ROOT / "data" / "outputs" / "law_firms_us.csv"

load_dotenv(PROJECT_ROOT / ".env")
API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_PLACES_API_KEY not found in .env")

# Expanded list: 150+ US cities
CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX",
    "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA",
    "Dallas, TX", "San Jose, CA", "Austin, TX", "Jacksonville, FL",
    "Fort Worth, TX", "Columbus, OH", "Charlotte, NC", "Indianapolis, IN",
    "San Francisco, CA", "Seattle, WA", "Denver, CO", "Washington, DC",
    "Nashville, TN", "Oklahoma City, OK", "El Paso, TX", "Boston, MA",
    "Portland, OR", "Las Vegas, NV", "Memphis, TN", "Louisville, KY",
    "Baltimore, MD", "Milwaukee, WI", "Albuquerque, NM", "Tucson, AZ",
    "Fresno, CA", "Mesa, AZ", "Sacramento, CA", "Atlanta, GA",
    "Kansas City, MO", "Colorado Springs, CO", "Omaha, NE", "Raleigh, NC",
    "Miami, FL", "Tampa, FL", "Orlando, FL", "Cleveland, OH",
    "Pittsburgh, PA", "Cincinnati, OH", "St. Louis, MO", "Minneapolis, MN",
    "Detroit, MI", "New Orleans, LA", "Salt Lake City, UT", "Honolulu, HI",
    "Boise, ID", "Richmond, VA", "Birmingham, AL", "Anchorage, AK",
    "Newark, NJ", "Buffalo, NY", "Rochester, NY", "Hartford, CT",
    "Providence, RI", "Des Moines, IA", "Little Rock, AR", "Jackson, MS",
    "Wilmington, DE", "Charleston, SC", "Savannah, GA", "Bakersfield, CA",
    "Riverside, CA", "Stockton, CA", "Corpus Christi, TX", "Lexington, KY",
    "Norfolk, VA", "Madison, WI", "Baton Rouge, LA", "Durham, NC",
    "Greensboro, NC", "Jersey City, NJ", "Chandler, AZ", "Scottsdale, AZ",
    # Additional cities
    "Laredo, TX", "McAllen, TX", "Brownsville, TX", "Lubbock, TX",
    "Amarillo, TX", "Waco, TX", "Midland, TX", "Odessa, TX",
    "Springfield, MO", "Wichita, KS", "Tulsa, OK", "Knoxville, TN",
    "Chattanooga, TN", "Clarksville, TN", "Murfreesboro, TN",
    "Shreveport, LA", "Lafayette, LA", "Lake Charles, LA",
    "Mobile, AL", "Montgomery, AL", "Huntsville, AL",
    "Fayetteville, NC", "Wilmington, NC", "Asheville, NC", "Winston-Salem, NC",
    "Columbia, SC", "Greenville, SC", "Myrtle Beach, SC",
    "Augusta, GA", "Macon, GA", "Athens, GA", "Columbus, GA",
    "Tallahassee, FL", "Pensacola, FL", "Fort Myers, FL", "Sarasota, FL",
    "West Palm Beach, FL", "Daytona Beach, FL", "Gainesville, FL",
    "Lakeland, FL", "Port St. Lucie, FL", "Cape Coral, FL",
    "Reno, NV", "Henderson, NV", "Sparks, NV",
    "Spokane, WA", "Tacoma, WA", "Vancouver, WA",
    "Eugene, OR", "Salem, OR", "Bend, OR",
    "Modesto, CA", "Visalia, CA", "Santa Rosa, CA", "Oxnard, CA",
    "Fontana, CA", "Moreno Valley, CA", "Ontario, CA", "Rancho Cucamonga, CA",
    "Long Beach, CA", "Oakland, CA", "Irvine, CA", "Anaheim, CA",
    "Santa Ana, CA", "Chula Vista, CA", "Escondido, CA",
    "Peoria, AZ", "Tempe, AZ", "Glendale, AZ", "Gilbert, AZ", "Surprise, AZ",
    "Akron, OH", "Toledo, OH", "Dayton, OH", "Canton, OH", "Youngstown, OH",
    "Fort Wayne, IN", "South Bend, IN", "Evansville, IN",
    "Grand Rapids, MI", "Lansing, MI", "Flint, MI", "Ann Arbor, MI",
    "Green Bay, WI", "Kenosha, WI", "Racine, WI",
    "Rockford, IL", "Naperville, IL", "Joliet, IL", "Aurora, IL", "Peoria, IL",
    "Springfield, IL", "Champaign, IL",
    "St. Paul, MN", "Rochester, MN", "Duluth, MN",
    "Sioux Falls, SD", "Fargo, ND", "Billings, MT", "Cheyenne, WY",
    "Missoula, MT", "Great Falls, MT",
    "Syracuse, NY", "Albany, NY", "Yonkers, NY", "White Plains, NY",
    "Paterson, NJ", "Elizabeth, NJ", "Trenton, NJ", "Camden, NJ",
    "Bridgeport, CT", "New Haven, CT", "Stamford, CT",
    "Portland, ME", "Manchester, NH", "Burlington, VT",
    "Worcester, MA", "Springfield, MA", "Lowell, MA", "Brockton, MA",
    "Virginia Beach, VA", "Chesapeake, VA", "Arlington, VA", "Alexandria, VA",
    "Roanoke, VA", "Lynchburg, VA",
    "Charleston, WV", "Huntington, WV",
    "Lexington, KY", "Bowling Green, KY", "Covington, KY",
    "Provo, UT", "Ogden, UT", "St. George, UT",
    "Colorado Springs, CO", "Aurora, CO", "Lakewood, CO", "Pueblo, CO",
    "Santa Fe, NM", "Las Cruces, NM", "Rio Rancho, NM",
]

# Multiple query types to surface different firms
QUERY_TEMPLATES = [
    "law firm in {}",
    "attorney in {}",
    "immigration lawyer in {}",
    "criminal defense lawyer in {}",
    "personal injury lawyer in {}",
    "family law attorney in {}",
    "divorce lawyer in {}",
    "traffic ticket lawyer in {}",
    "bankruptcy attorney in {}",
    "real estate lawyer in {}",
]

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

seen_place_ids = set()
results = []
stats = {"candidates": 0, "with_website": 0}


def search_places(query, page_token=None):
    """Text Search (New) for places."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.businessStatus,nextPageToken",
    }
    body = {
        "textQuery": query,
        "languageCode": "en",
        "regionCode": "US",
        "pageSize": 20,
    }
    if page_token:
        body["pageToken"] = page_token

    resp = requests.post(TEXT_SEARCH_URL, headers=headers, json=body)
    return resp.json()


def process_search_results(data):
    """Filter and collect qualifying firms from search results."""
    if "error" in data:
        err = data["error"]
        print(f"  API error: {err.get('code')} - {err.get('message', '')}")
        return False

    places = data.get("places", [])
    if not places:
        return True

    for place in places:
        place_id = place.get("id", "")
        if place_id in seen_place_ids:
            continue
        seen_place_ids.add(place_id)

        rating = place.get("rating", 0)
        total_reviews = place.get("userRatingCount", 0)
        name = place.get("displayName", {}).get("text", "")

        # Must have 50+ reviews and 4.0+ rating
        if total_reviews < 50 or rating < 4.0:
            continue

        stats["candidates"] += 1
        website = place.get("websiteUri", "")

        if website:
            stats["with_website"] += 1
            continue

        # No website — match!
        firm = {
            "business_name": name,
            "address": place.get("formattedAddress", ""),
            "phone": place.get("nationalPhoneNumber", ""),
            "email": "",
            "rating": rating,
            "total_reviews": total_reviews,
            "google_maps_url": place.get("googleMapsUri", ""),
        }
        results.append(firm)
        print(f"  ✓ MATCH #{len(results)}: {name} - {rating}★ ({total_reviews} reviews) | {firm['address']}")

    return True


def main():
    print("=" * 70)
    print("Law Firm Lead Generator — Expanded Search")
    print("Criteria: 50+ reviews, 4.0+ rating, NO website")
    print(f"Cities: {len(CITIES)} | Query types: {len(QUERY_TEMPLATES)}")
    print("=" * 70)

    for qi, query_template in enumerate(QUERY_TEMPLATES):
        if len(results) >= 100:
            break

        print(f"\n--- Query type {qi+1}/{len(QUERY_TEMPLATES)}: \"{query_template.format('...')}\" ---")

        for city in CITIES:
            if len(results) >= 100:
                break

            query = query_template.format(city)
            data = search_places(query)
            ok = process_search_results(data)
            if not ok:
                # Rate limit or error — pause and retry once
                print(f"  Pausing 5s for {city}...")
                time.sleep(5)
                data = search_places(query)
                ok = process_search_results(data)
                if not ok:
                    print(f"  Skipping {city}")
                    continue

            # Follow pagination (up to 2 more pages)
            for _ in range(2):
                if len(results) >= 100:
                    break
                next_token = data.get("nextPageToken")
                if not next_token:
                    break
                time.sleep(1)
                data = search_places(query, page_token=next_token)
                process_search_results(data)

        print(f"  Subtotal: {len(results)} matches so far ({stats['candidates']} candidates, {stats['with_website']} had websites)")

    # Write CSV
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "business_name", "address", "phone", "email",
            "rating", "total_reviews", "google_maps_url"
        ])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'=' * 70}")
    print(f"DONE. Found {len(results)} law firms without websites.")
    print(f"Total candidates checked: {stats['candidates']}")
    print(f"Had websites: {stats['with_website']}")
    print(f"CSV: {OUTPUT_FILE}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
