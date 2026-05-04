"""
Thorough lead enrichment pipeline.

For any lead CSV, this script:
1. Looks up each business on Google Places API
2. Adds phone number (if missing)
3. Verifies the business actually exists
4. Confirms no website (removes leads that have one)
5. Removes permanently closed businesses
6. Finds publicly available email via web search + MX verification
7. Adds enrichment columns: rating, review_count, full_address, google_maps_url, email
8. Re-indexes row numbers

Usage:
    python3 src/processors/enrich_leads.py <csv_path>
    python3 src/processors/enrich_leads.py <csv_path> --keep-with-website
    python3 src/processors/enrich_leads.py <csv_path> --skip-email  # skip email lookup
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import dns.resolver
import requests
from ddgs import DDGS
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_PLACES_API_KEY not found in .env")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.displayName",
    "places.nationalPhoneNumber",
    "places.formattedAddress",
    "places.websiteUri",
    "places.businessStatus",
    "places.rating",
    "places.userRatingCount",
    "places.googleMapsUri",
])
HEADERS = {
    "Content-Type": "application/json",
    "X-Goog-Api-Key": API_KEY,
    "X-Goog-FieldMask": FIELD_MASK,
}

# Enrichment columns added by this script
ENRICH_COLS = [
    "phone_number",
    "email",
    "verified_address",
    "rating",
    "review_count",
    "google_maps_url",
]

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# Domains that are never a business's own contact email
JUNK_DOMAINS = {
    "example.com", "test.com", "email.com", "sentry.io",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "mail.com", "protonmail.com",
    "yandex.com", "zoho.com",
    # listing/directory sites — these are the site's own addresses
    "yelp.com", "bbb.org", "yellowpages.com", "facebook.com",
    "linkedin.com", "twitter.com", "instagram.com",
    "google.com", "googleapis.com", "gstatic.com",
    "mapquest.com", "foursquare.com", "tripadvisor.com",
}

api_calls = 0
ddg = DDGS()


def verify_mx(domain):
    """Check if a domain has valid MX records."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False


def find_email(business_name, region="", country="united states"):
    """Search the web for a publicly listed email for a business.

    Strategy:
    1. DuckDuckGo search for "{business} {location} email contact"
    2. Extract all email addresses from result snippets + titles
    3. Filter out junk/generic domains
    4. Verify the best candidate's domain has MX records
    """
    query = f"{business_name} {region} {country} email contact".strip()

    try:
        results = ddg.text(query, max_results=5)
    except Exception:
        return ""

    if not results:
        return ""

    # Collect all emails from snippets and titles
    candidates = []
    for r in results:
        text = f"{r.get('title', '')} {r.get('body', '')} {r.get('href', '')}"
        found = EMAIL_RE.findall(text)
        candidates.extend(found)

    if not candidates:
        return ""

    # Filter and rank
    scored = []
    for email in candidates:
        email = email.lower().strip()
        domain = email.split("@")[1]

        # Skip junk domains
        if domain in JUNK_DOMAINS:
            continue

        # Skip very long or suspicious addresses
        if len(email) > 60:
            continue

        # Prefer addresses that look like business contacts
        local = email.split("@")[0]
        score = 0
        if any(w in local for w in ("info", "contact", "office", "admin", "hello", "sales")):
            score += 3
        if any(w in local for w in ("support", "service", "help", "team")):
            score += 2

        # Slight preference for shorter, cleaner addresses
        score += max(0, 3 - len(local) // 10)

        scored.append((score, email, domain))

    if not scored:
        return ""

    # Sort by score descending, take best
    scored.sort(key=lambda x: x[0], reverse=True)

    # Verify MX for top candidates (try up to 3)
    for _, email, domain in scored[:3]:
        if verify_mx(domain):
            return email

    return ""


def lookup_place(business_name, region="", country="united states"):
    """Search Google Places for a business. Returns place dict or None."""
    global api_calls
    query = f"{business_name} {region} {country}".strip()
    body = {"textQuery": query, "pageSize": 1}

    for attempt in range(2):
        try:
            resp = requests.post(SEARCH_URL, headers=HEADERS, json=body, timeout=15)
            api_calls += 1
            resp.raise_for_status()
            data = resp.json()
            places = data.get("places", [])
            return places[0] if places else None
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 503 and attempt == 0:
                time.sleep(2)
                continue
            print(f"  API error for '{business_name}': {e}")
            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"  Connection failed for '{business_name}'")
            return None

    return None


def enrich_csv(csv_path, keep_with_website=False, skip_email=False):
    """Run full enrichment pipeline on a CSV file."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    # Read input
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        original_fields = list(reader.fieldnames)
        rows = list(reader)

    # Build output fieldnames — insert enrichment cols after business_name
    fieldnames = list(original_fields)
    insert_at = fieldnames.index("business_name") + 1
    for col in ENRICH_COLS:
        if col not in fieldnames:
            fieldnames.insert(insert_at, col)
            insert_at += 1

    total = len(rows)
    clean = []
    emails_found = 0
    removed = {"has_website": [], "not_found": [], "closed": []}

    print(f"Enriching {total} leads...\n")

    for i, row in enumerate(rows, 1):
        name = row.get("business_name", "")
        region = row.get("business_region", "")
        country = row.get("business_country_name", "united states")

        place = lookup_place(name, region, country)

        if place is None:
            removed["not_found"].append(name)
            print(f"  [{i}/{total}] {name} -> REMOVED (not found)")
            time.sleep(0.1)
            continue

        status = place.get("businessStatus", "")
        website = place.get("websiteUri", "")
        phone = place.get("nationalPhoneNumber", "")
        address = place.get("formattedAddress", "")
        rating = place.get("rating", "")
        reviews = place.get("userRatingCount", "")
        maps_url = place.get("googleMapsUri", "")

        # Filter: permanently closed
        if status == "CLOSED_PERMANENTLY":
            removed["closed"].append(name)
            print(f"  [{i}/{total}] {name} -> REMOVED (permanently closed)")
            time.sleep(0.1)
            continue

        # Filter: has website (unless --keep-with-website)
        if website and not keep_with_website:
            removed["has_website"].append(name)
            print(f"  [{i}/{total}] {name} -> REMOVED (has website: {website})")
            time.sleep(0.1)
            continue

        # Filter: no phone found
        if not phone:
            removed["not_found"].append(name)
            print(f"  [{i}/{total}] {name} -> REMOVED (no phone number)")
            time.sleep(0.1)
            continue

        # Email lookup
        email = ""
        if not skip_email:
            email = find_email(name, region, country)
            if email:
                emails_found += 1
            time.sleep(0.3)  # rate-limit DDG

        # Enrich
        row["phone_number"] = phone
        row["email"] = email
        row["verified_address"] = address
        row["rating"] = str(rating) if rating else ""
        row["review_count"] = str(reviews) if reviews else ""
        row["google_maps_url"] = maps_url

        email_status = email if email else "no email"
        clean.append(row)
        print(f"  [{i}/{total}] {name} -> VERIFIED ({phone} | {email_status})")
        time.sleep(0.1)

    # Re-index and write
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for idx, row in enumerate(clean, 1):
            row["row_num"] = str(idx)
            writer.writerow(row)

    # Summary
    print(f"\n{'='*40}")
    print(f"ENRICHMENT SUMMARY")
    print(f"{'='*40}")
    print(f"Input:              {total} leads")
    print(f"Verified & kept:    {len(clean)}")
    print(f"Emails found:       {emails_found}/{len(clean)}")
    print(f"Removed (website):  {len(removed['has_website'])}")
    print(f"Removed (not found/no phone): {len(removed['not_found'])}")
    print(f"Removed (closed):   {len(removed['closed'])}")
    print(f"API calls:          {api_calls}")
    print(f"Output:             {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich and verify a lead CSV")
    parser.add_argument("csv_path", help="Path to the CSV file to enrich")
    parser.add_argument(
        "--keep-with-website",
        action="store_true",
        help="Don't remove leads that have a website",
    )
    parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip email lookup (faster, phone + verification only)",
    )
    args = parser.parse_args()
    enrich_csv(args.csv_path, keep_with_website=args.keep_with_website, skip_email=args.skip_email)
