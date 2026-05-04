import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

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


def search_places(query, api_key, region_code="US", page_token=None):
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

    for attempt in range(5):
        try:
            resp = requests.post(SEARCH_URL, headers=headers, json=body, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError:
            return {}
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(10 * (attempt + 1))
    return {}


def filter_place(place, min_reviews, min_rating, seen_ids, require_no_website=True):
    pid = place.get("id")
    if not pid or pid in seen_ids:
        return None
    if require_no_website and place.get("websiteUri", ""):
        return None
    status = place.get("businessStatus", "N/A")
    if status not in ("OPERATIONAL", "N/A"):
        return None
    rating = place.get("rating", 0)
    if rating < min_rating:
        return None
    reviews = place.get("userRatingCount", 0)
    if reviews < min_reviews:
        return None
    return {
        "Business Name": place.get("displayName", {}).get("text", "N/A"),
        "Address": place.get("formattedAddress", "N/A"),
        "Phone": place.get("nationalPhoneNumber", "N/A"),
        "Rating": rating,
        "Reviews": reviews,
        "Business Type": "",
        "Google Maps URL": place.get("googleMapsUri", ""),
    }


def run_search(cities, business_types, api_key, region_code="US",
               min_reviews=50, min_rating=4.0, target_leads=100,
               require_no_website=True):
    seen_ids = set()
    leads_found = 0
    api_calls = 0

    for city in cities:
        if leads_found >= target_leads:
            break
        for biz_type in business_types:
            if leads_found >= target_leads:
                break
            query = f"{biz_type} in {city}"
            data = search_places(query, api_key, region_code)
            api_calls += 1

            for place in data.get("places", []):
                if leads_found >= target_leads:
                    break
                lead = filter_place(place, min_reviews, min_rating, seen_ids, require_no_website)
                if lead:
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

            next_token = data.get("nextPageToken")
            while next_token and leads_found < target_leads:
                time.sleep(0.1)
                data = search_places(query, api_key, region_code, page_token=next_token)
                api_calls += 1
                next_token = data.get("nextPageToken")
                for place in data.get("places", []):
                    if leads_found >= target_leads:
                        break
                    lead = filter_place(place, min_reviews, min_rating, seen_ids, require_no_website)
                    if lead:
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
            time.sleep(0.1)
