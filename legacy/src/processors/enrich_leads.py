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

# Domains we never want to surface as a lead's contact email.
# NOTE: freemail (gmail/yahoo/hotmail/etc) is intentionally NOT here — many
# small businesses (real-estate agents, HVAC, accountants) publish a Gmail
# as their only contact address. score_email() penalises freemail so a
# matching business-domain still beats it on ties.
JUNK_DOMAINS = {
    "example.com", "test.com", "email.com", "sentry.io",
    # listing / directory sites — these surface the site's own addresses
    "yelp.com", "bbb.org", "yellowpages.com", "facebook.com",
    "linkedin.com", "twitter.com", "instagram.com",
    "google.com", "googleapis.com", "gstatic.com",
    "mapquest.com", "foursquare.com", "tripadvisor.com",
    "wixsite.com", "wordpress.com", "squarespace.com",
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
    1. DuckDuckGo — try multiple query phrasings (operators rarely write
       "email contact" near their address; "contact" or just "{biz} {city}"
       can surface mailtos the original query missed).
    2. Extract all email addresses from result snippets, titles, and hrefs.
    3. Drop directory junk; keep freemail (small businesses use Gmail).
    4. Score, then MX-verify the top 10 candidates.
    """
    name = (business_name or "").strip()
    if not name:
        return ""
    region = (region or "").strip()
    country = (country or "").strip()

    queries = [
        f"{name} {region} {country} email contact",
        f"{name} contact email",
        f"{name} {region} contact",
        f"\"{name}\" email",
    ]
    # Strip empties produced by missing region/country.
    queries = [q.strip() for q in queries if q.strip() and q.strip() != name]

    candidates = []
    for q in queries:
        try:
            results = ddg.text(q, max_results=12) or []
        except Exception:
            results = []
        for r in results:
            text = f"{r.get('title', '')} {r.get('body', '')} {r.get('href', '')}"
            candidates.extend(EMAIL_RE.findall(text))
        if candidates:
            # Stop early once we have enough material to rank — additional
            # queries cost time and DDG sometimes rate-limits on rapid runs.
            if len(candidates) >= 8:
                break

    if not candidates:
        return ""

    from src.web.email_quality import score_email  # local import: avoid cycle

    seen = set()
    scored = []
    for email in candidates:
        email = email.lower().strip().rstrip(".,;:)")
        if email in seen or "@" not in email:
            continue
        seen.add(email)
        domain = email.split("@", 1)[1]
        if domain in JUNK_DOMAINS:
            continue
        if len(email) > 60:
            continue
        s, kind, _ = score_email(email, business_name=name)
        scored.append((s, kind, email, domain))

    if not scored:
        return ""

    # Highest score first; personal beats role on ties.
    scored.sort(key=lambda x: (x[0], x[1] == "personal"), reverse=True)

    # MX-verify deeper into the candidate list.
    for _, _, email, domain in scored[:10]:
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
