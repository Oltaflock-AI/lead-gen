import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

COUNTRY_CITIES = {
    "US": [
        "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX",
        "Phoenix, AZ", "Philadelphia, PA", "San Antonio, TX", "San Diego, CA",
        "Dallas, TX", "Austin, TX", "Jacksonville, FL", "San Jose, CA",
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
    ],
    "India": [
        "Mumbai, Maharashtra", "Delhi, India", "Bangalore, Karnataka",
        "Hyderabad, Telangana", "Chennai, Tamil Nadu", "Kolkata, West Bengal",
        "Pune, Maharashtra", "Ahmedabad, Gujarat", "Jaipur, Rajasthan",
        "Lucknow, Uttar Pradesh", "Chandigarh, India", "Indore, Madhya Pradesh",
        "Nagpur, Maharashtra", "Bhopal, Madhya Pradesh", "Coimbatore, Tamil Nadu",
        "Kochi, Kerala", "Surat, Gujarat", "Visakhapatnam, Andhra Pradesh",
        "Thiruvananthapuram, Kerala", "Noida, Uttar Pradesh",
        "Gurgaon, Haryana", "Vadodara, Gujarat", "Mysore, Karnataka",
        "Nashik, Maharashtra", "Patna, Bihar",
    ],
    "UK": [
        "London, UK", "Birmingham, UK", "Manchester, UK", "Glasgow, UK",
        "Liverpool, UK", "Leeds, UK", "Sheffield, UK", "Edinburgh, UK",
        "Bristol, UK", "Cardiff, UK", "Leicester, UK", "Nottingham, UK",
        "Newcastle, UK", "Southampton, UK", "Brighton, UK",
    ],
    "Canada": [
        "Toronto, ON", "Vancouver, BC", "Montreal, QC", "Calgary, AB",
        "Edmonton, AB", "Ottawa, ON", "Winnipeg, MB", "Quebec City, QC",
        "Hamilton, ON", "Kitchener, ON", "Halifax, NS", "Victoria, BC",
    ],
    "Australia": [
        "Sydney, NSW", "Melbourne, VIC", "Brisbane, QLD", "Perth, WA",
        "Adelaide, SA", "Gold Coast, QLD", "Canberra, ACT", "Newcastle, NSW",
        "Hobart, TAS", "Darwin, NT",
    ],
    "New Zealand": [
        "Auckland", "Wellington", "Christchurch", "Hamilton", "Tauranga",
        "Dunedin", "Palmerston North", "Napier", "Hastings", "Nelson",
        "Rotorua", "Queenstown", "Invercargill", "New Plymouth", "Whangarei",
    ],
    "Germany": [
        "Berlin", "Hamburg", "Munich", "Cologne", "Frankfurt",
        "Stuttgart", "Düsseldorf", "Leipzig", "Dortmund", "Essen",
        "Bremen", "Dresden", "Hannover", "Nuremberg", "Bonn",
    ],
    "France": [
        "Paris", "Marseille", "Lyon", "Toulouse", "Nice",
        "Nantes", "Strasbourg", "Montpellier", "Bordeaux", "Lille",
        "Rennes", "Reims", "Toulon", "Saint-Étienne", "Grenoble",
    ],
    "Spain": [
        "Madrid", "Barcelona", "Valencia", "Seville", "Zaragoza",
        "Málaga", "Murcia", "Palma", "Las Palmas", "Bilbao",
        "Alicante", "Córdoba", "Valladolid", "Vigo", "Granada",
    ],
    "Italy": [
        "Rome", "Milan", "Naples", "Turin", "Palermo",
        "Genoa", "Bologna", "Florence", "Bari", "Catania",
        "Venice", "Verona", "Messina", "Padua", "Trieste",
    ],
    "Netherlands": [
        "Amsterdam", "Rotterdam", "The Hague", "Utrecht", "Eindhoven",
        "Groningen", "Tilburg", "Almere", "Breda", "Nijmegen",
    ],
    "Ireland": [
        "Dublin", "Cork", "Galway", "Limerick", "Waterford",
        "Drogheda", "Dundalk", "Bray", "Kilkenny", "Sligo",
    ],
    "Sweden": [
        "Stockholm", "Gothenburg", "Malmö", "Uppsala", "Västerås",
        "Örebro", "Linköping", "Helsingborg", "Jönköping", "Norrköping",
    ],
}

COUNTRY_REGION_CODES = {
    "US": "US", "India": "IN", "UK": "GB", "Canada": "CA", "Australia": "AU",
    "New Zealand": "NZ", "Germany": "DE", "France": "FR", "Spain": "ES",
    "Italy": "IT", "Netherlands": "NL", "Ireland": "IE", "Sweden": "SE",
}

COUNTRY_STATES = {
    "US": [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    ],
    "India": [
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
        "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
        "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
        "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh",
        "Uttarakhand", "West Bengal",
        "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    ],
    "UK": ["England", "Scotland", "Wales", "Northern Ireland"],
    "Canada": [
        "Alberta", "British Columbia", "Manitoba", "New Brunswick",
        "Newfoundland and Labrador", "Nova Scotia", "Ontario",
        "Prince Edward Island", "Quebec", "Saskatchewan",
        "Northwest Territories", "Nunavut", "Yukon",
    ],
    "Australia": [
        "New South Wales", "Victoria", "Queensland", "Western Australia",
        "South Australia", "Tasmania", "Australian Capital Territory",
        "Northern Territory",
    ],
    "New Zealand": [
        "Auckland", "Bay of Plenty", "Canterbury", "Gisborne", "Hawke's Bay",
        "Manawatū-Whanganui", "Marlborough", "Nelson", "Northland", "Otago",
        "Southland", "Taranaki", "Tasman", "Waikato", "Wellington", "West Coast",
    ],
    "Germany": [
        "Baden-Württemberg", "Bavaria", "Berlin", "Brandenburg", "Bremen",
        "Hamburg", "Hesse", "Lower Saxony", "Mecklenburg-Vorpommern",
        "North Rhine-Westphalia", "Rhineland-Palatinate", "Saarland",
        "Saxony", "Saxony-Anhalt", "Schleswig-Holstein", "Thuringia",
    ],
    "France": [
        "Auvergne-Rhône-Alpes", "Bourgogne-Franche-Comté", "Brittany",
        "Centre-Val de Loire", "Corsica", "Grand Est", "Hauts-de-France",
        "Île-de-France", "Normandy", "Nouvelle-Aquitaine", "Occitanie",
        "Pays de la Loire", "Provence-Alpes-Côte d'Azur",
    ],
    "Spain": [
        "Andalusia", "Aragon", "Asturias", "Balearic Islands", "Basque Country",
        "Canary Islands", "Cantabria", "Castile and León", "Castilla-La Mancha",
        "Catalonia", "Extremadura", "Galicia", "La Rioja", "Madrid", "Murcia",
        "Navarre", "Valencia",
    ],
    "Italy": [
        "Abruzzo", "Aosta Valley", "Apulia", "Basilicata", "Calabria",
        "Campania", "Emilia-Romagna", "Friuli-Venezia Giulia", "Lazio",
        "Liguria", "Lombardy", "Marche", "Molise", "Piedmont", "Sardinia",
        "Sicily", "Trentino-Alto Adige", "Tuscany", "Umbria", "Veneto",
    ],
    "Netherlands": [
        "Drenthe", "Flevoland", "Friesland", "Gelderland", "Groningen",
        "Limburg", "North Brabant", "North Holland", "Overijssel", "South Holland",
        "Utrecht", "Zeeland",
    ],
    "Ireland": ["Connacht", "Leinster", "Munster", "Ulster"],
    "Sweden": [
        "Stockholm", "Västra Götaland", "Skåne", "Östergötland", "Uppsala",
        "Jönköping", "Halland", "Örebro", "Södermanland", "Dalarna",
        "Gävleborg", "Värmland", "Västerbotten", "Norrbotten", "Västmanland",
        "Kronoberg", "Kalmar", "Blekinge", "Jämtland", "Västernorrland", "Gotland",
    ],
}

NICHE_PRESETS = {
    "Law Firms": [
        "law firm", "attorney", "immigration lawyer", "criminal defense lawyer",
        "personal injury lawyer", "family law attorney", "divorce lawyer",
        "bankruptcy attorney", "real estate lawyer",
    ],
    "Real Estate": [
        "real estate agent", "realtor", "property management company",
        "real estate broker", "property dealer",
    ],
    "Restaurants & Food": [
        "restaurant", "cafe", "bakery", "pizza place", "burger restaurant",
        "sushi restaurant", "indian restaurant", "italian restaurant", "catering service",
    ],
    "Home Services": [
        "plumber", "electrician", "HVAC service", "roofing contractor",
        "landscaping company", "cleaning service", "pest control",
        "handyman service", "painting contractor", "flooring installer",
    ],
    "Health & Wellness": [
        "dentist", "chiropractor", "physical therapist", "dermatologist",
        "veterinarian", "gym", "yoga studio", "massage therapist",
    ],
    "Auto Services": [
        "auto repair shop", "car wash", "auto detailing", "tire shop",
        "auto body shop", "oil change service", "towing service",
    ],
    "Beauty & Personal Care": [
        "hair salon", "barber shop", "nail salon", "spa",
        "beauty salon", "tattoo shop", "skincare clinic",
    ],
    "Professional Services": [
        "accountant", "financial advisor", "insurance agent",
        "marketing agency", "web design agency", "photographer", "notary public",
    ],
}


def search_places(query, api_key, region_code="US", page_token=None, on_event=None):
    """Call Google Places Text Search.

    Returns the JSON dict on success, or `{"_error": {...}}` on failure so the
    caller can distinguish "API failed" from "API returned 0 places".
    Optional `on_event(kind, payload)` reports retries / errors upstream.
    """
    if not api_key:
        err = {"status": 0, "reason": "missing_api_key", "body": "GOOGLE_PLACES_API_KEY empty"}
        log.error("places search aborted — %s", err["body"])
        if on_event:
            on_event("query_error", {"query": query, **err})
        return {"_error": err}

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.nationalPhoneNumber,places.websiteUri,places.businessStatus,"
            "places.types,places.rating,places.userRatingCount,"
            "places.googleMapsUri,nextPageToken"
        ),
    }
    body = {"textQuery": query, "pageSize": 20, "regionCode": region_code}
    if page_token:
        body["pageToken"] = page_token

    last_err = None
    for attempt in range(5):
        try:
            resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=15)
            status = resp.status_code
            if 200 <= status < 300:
                return resp.json()
            # 4xx: don't retry auth/quota/bad-request — surface immediately
            if 400 <= status < 500 and status not in (408, 429):
                snippet = (resp.text or "")[:400]
                log.error("places search %s [%s] %s — %s", query, region_code, status, snippet)
                err = {"status": status, "reason": "http_error", "body": snippet}
                if on_event:
                    on_event("query_error", {"query": query, **err})
                return {"_error": err}
            # 408/429/5xx: retry with backoff
            log.warning(
                "places search %s [%s] transient %s (attempt %d/5)",
                query, region_code, status, attempt + 1,
            )
            last_err = {"status": status, "reason": "transient", "body": (resp.text or "")[:200]}
            time.sleep(2 * (attempt + 1))
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            log.warning(
                "places search %s [%s] network error (attempt %d/5): %s",
                query, region_code, attempt + 1, e,
            )
            last_err = {"status": 0, "reason": "network", "body": str(e)}
            time.sleep(2 * (attempt + 1))

    if on_event and last_err:
        on_event("query_error", {"query": query, **last_err})
    return {"_error": last_err or {"status": 0, "reason": "unknown", "body": "exhausted retries"}}


FILTER_REASONS = (
    "duplicate", "has_website", "not_operational", "below_rating", "below_reviews",
)


def filter_place(place, min_reviews, min_rating, seen_ids, require_no_website=True):
    """Return (lead_dict, None) if kept, or (None, reason) if filtered out."""
    pid = place.get("id")
    if not pid or pid in seen_ids:
        return None, "duplicate"
    if require_no_website and place.get("websiteUri", ""):
        return None, "has_website"
    status = place.get("businessStatus", "N/A")
    if status not in ("OPERATIONAL", "N/A"):
        return None, "not_operational"
    rating = place.get("rating", 0)
    if rating < min_rating:
        return None, "below_rating"
    reviews = place.get("userRatingCount", 0)
    if reviews < min_reviews:
        return None, "below_reviews"
    has_site = bool(place.get("websiteUri", ""))
    return {
        "Business Name": place.get("displayName", {}).get("text", "N/A"),
        "Address": place.get("formattedAddress", "N/A"),
        "Phone": place.get("nationalPhoneNumber", "N/A"),
        "Rating": rating,
        "Reviews": reviews,
        "Business Type": "",
        "Has Website": "yes" if has_site else "no",
        "Website": place.get("websiteUri", "") if has_site else "",
        "Google Maps URL": place.get("googleMapsUri", ""),
    }, None


def run_search(cities, business_types, api_key, region_code="US",
               min_reviews=50, min_rating=4.0, target_leads=100,
               require_no_website=True, on_event=None):
    """Yield (lead, progress) tuples. Optional `on_event(kind, payload)` reports
    per-query diagnostics so callers can show real-time progress even when no
    lead survives filtering.
    """
    seen_ids = set()
    leads_found = 0
    api_calls = 0
    total_raw = 0
    total_filtered = 0
    filter_counts = {r: 0 for r in FILTER_REASONS}

    log.info(
        "run_search start: cities=%d types=%d target=%d region=%s "
        "min_reviews=%d min_rating=%.1f require_no_website=%s",
        len(cities), len(business_types), target_leads, region_code,
        min_reviews, min_rating, require_no_website,
    )
    if on_event:
        on_event("search_start", {
            "cities": len(cities), "types": len(business_types),
            "target": target_leads, "region": region_code,
        })

    def _process(places, query, city, biz_type):
        nonlocal leads_found, total_raw, total_filtered
        for place in places:
            total_raw += 1
            if leads_found >= target_leads:
                return
            lead, reason = filter_place(
                place, min_reviews, min_rating, seen_ids, require_no_website,
            )
            if lead is None:
                total_filtered += 1
                if reason in filter_counts:
                    filter_counts[reason] += 1
                continue
            seen_ids.add(place["id"])
            lead["Business Type"] = biz_type
            lead["City"] = city
            leads_found += 1
            yield lead, {
                "leads_found": leads_found,
                "total_target": target_leads,
                "current_city": city,
                "current_type": biz_type,
                "api_calls": api_calls,
            }

    for city in cities:
        if leads_found >= target_leads:
            break
        for biz_type in business_types:
            if leads_found >= target_leads:
                break
            query = f"{biz_type} in {city}"
            log.info("query: %s", query)
            if on_event:
                on_event("query_start", {
                    "city": city, "type": biz_type, "query": query,
                    "leads_so_far": leads_found,
                })

            data = search_places(query, api_key, region_code, on_event=on_event)
            api_calls += 1
            if "_error" in data:
                # Error already logged + reported via on_event by search_places.
                continue

            places = data.get("places", []) or []
            kept_before = leads_found
            yield from _process(places, query, city, biz_type)

            # Pagination — Google returns up to 60 results across 3 pages.
            next_token = data.get("nextPageToken")
            page = 1
            while next_token and leads_found < target_leads and page < 3:
                time.sleep(0.1)
                data = search_places(
                    query, api_key, region_code,
                    page_token=next_token, on_event=on_event,
                )
                api_calls += 1
                if "_error" in data:
                    break
                next_token = data.get("nextPageToken")
                page_places = data.get("places", []) or []
                places.extend(page_places)
                yield from _process(page_places, query, city, biz_type)
                page += 1

            kept_this_query = leads_found - kept_before
            log.info(
                "query done: %s — raw=%d kept=%d total_kept=%d",
                query, len(places), kept_this_query, leads_found,
            )
            if on_event:
                on_event("query_done", {
                    "city": city, "type": biz_type,
                    "raw": len(places), "kept": kept_this_query,
                    "total_kept": leads_found,
                })
            time.sleep(0.1)

    log.info(
        "run_search end: kept=%d raw=%d filtered=%d api_calls=%d filters=%s",
        leads_found, total_raw, total_filtered, api_calls, filter_counts,
    )
    if on_event:
        on_event("search_end", {
            "kept": leads_found, "raw": total_raw,
            "filtered": total_filtered, "api_calls": api_calls,
            "filter_counts": filter_counts,
        })
