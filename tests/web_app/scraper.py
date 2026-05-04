import requests
import time
import os
from dotenv import load_dotenv

load_dotenv()

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Pre-built city lists per country
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
        "Boise, ID", "Richmond, VA", "Birmingham, AL", "Anchorage, AK",
        "Newark, NJ", "Buffalo, NY", "Rochester, NY", "Hartford, CT",
        "Providence, RI", "Des Moines, IA", "Little Rock, AR", "Jackson, MS",
        "Charleston, SC", "Savannah, GA", "Bakersfield, CA", "Riverside, CA",
        "Corpus Christi, TX", "Lexington, KY", "Norfolk, VA", "Madison, WI",
        "Baton Rouge, LA", "Durham, NC", "Greensboro, NC", "Scottsdale, AZ",
        "Laredo, TX", "Lubbock, TX", "Amarillo, TX", "Waco, TX",
        "Springfield, MO", "Wichita, KS", "Tulsa, OK", "Knoxville, TN",
        "Chattanooga, TN", "Shreveport, LA", "Mobile, AL", "Montgomery, AL",
        "Huntsville, AL", "Fayetteville, NC", "Wilmington, NC", "Asheville, NC",
        "Columbia, SC", "Greenville, SC", "Augusta, GA", "Macon, GA",
        "Tallahassee, FL", "Pensacola, FL", "Fort Myers, FL", "Sarasota, FL",
        "West Palm Beach, FL", "Reno, NV", "Spokane, WA", "Tacoma, WA",
        "Eugene, OR", "Salem, OR", "Modesto, CA", "Oakland, CA",
        "Long Beach, CA", "Irvine, CA", "Anaheim, CA",
        "Akron, OH", "Toledo, OH", "Dayton, OH", "Fort Wayne, IN",
        "Grand Rapids, MI", "Lansing, MI", "Ann Arbor, MI",
        "Green Bay, WI", "Rockford, IL", "Naperville, IL", "Aurora, IL",
        "Springfield, IL", "St. Paul, MN", "Rochester, MN",
        "Sioux Falls, SD", "Fargo, ND", "Billings, MT",
        "Syracuse, NY", "Albany, NY", "Bridgeport, CT", "New Haven, CT",
        "Portland, ME", "Manchester, NH", "Burlington, VT",
        "Worcester, MA", "Springfield, MA", "Virginia Beach, VA",
        "Chesapeake, VA", "Arlington, VA", "Roanoke, VA",
        "Charleston, WV", "Provo, UT", "Santa Fe, NM", "Las Cruces, NM",
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
        "Newcastle, UK", "Southampton, UK", "Brighton, UK", "Plymouth, UK",
        "Oxford, UK", "Cambridge, UK", "York, UK", "Bath, UK",
        "Aberdeen, UK", "Dundee, UK", "Belfast, UK", "Swansea, UK",
        "Coventry, UK",
    ],
    "Canada": [
        "Toronto, ON", "Vancouver, BC", "Montreal, QC", "Calgary, AB",
        "Edmonton, AB", "Ottawa, ON", "Winnipeg, MB", "Quebec City, QC",
        "Hamilton, ON", "Kitchener, ON", "Halifax, NS", "Victoria, BC",
        "London, ON", "Oshawa, ON", "Windsor, ON", "Saskatoon, SK",
        "Regina, SK", "St. John's, NL", "Kelowna, BC", "Barrie, ON",
        "Sherbrooke, QC", "Guelph, ON", "Abbotsford, BC", "Kingston, ON",
        "Moncton, NB",
    ],
    "Australia": [
        "Sydney, NSW", "Melbourne, VIC", "Brisbane, QLD", "Perth, WA",
        "Adelaide, SA", "Gold Coast, QLD", "Canberra, ACT", "Newcastle, NSW",
        "Hobart, TAS", "Darwin, NT", "Wollongong, NSW", "Geelong, VIC",
        "Townsville, QLD", "Cairns, QLD", "Toowoomba, QLD",
        "Ballarat, VIC", "Bendigo, VIC", "Launceston, TAS",
        "Mackay, QLD", "Rockhampton, QLD",
    ],
}

# Region codes for Google Places API
COUNTRY_REGION_CODES = {
    "US": "US",
    "India": "IN",
    "UK": "GB",
    "Canada": "CA",
    "Australia": "AU",
}

# Pre-built niche query templates
NICHE_PRESETS = {
    "Law Firms": [
        "law firm", "attorney", "immigration lawyer", "criminal defense lawyer",
        "personal injury lawyer", "family law attorney", "divorce lawyer",
        "traffic ticket lawyer", "bankruptcy attorney", "real estate lawyer",
    ],
    "Real Estate": [
        "real estate agent", "realtor", "property management company",
        "real estate broker", "commercial real estate agent",
        "property dealer", "real estate consultant",
    ],
    "Restaurants & Food": [
        "restaurant", "cafe", "bakery", "pizza place", "burger restaurant",
        "sushi restaurant", "indian restaurant", "mexican restaurant",
        "italian restaurant", "food truck", "catering service",
    ],
    "Home Services": [
        "plumber", "electrician", "HVAC service", "roofing contractor",
        "landscaping company", "cleaning service", "pest control",
        "handyman service", "painting contractor", "flooring installer",
    ],
    "Health & Wellness": [
        "dentist", "chiropractor", "physical therapist", "dermatologist",
        "optometrist", "veterinarian", "gym", "yoga studio",
        "massage therapist", "nutritionist",
    ],
    "Auto Services": [
        "auto repair shop", "car wash", "auto detailing", "tire shop",
        "auto body shop", "oil change service", "car dealer",
        "motorcycle repair", "towing service", "auto glass repair",
    ],
    "Beauty & Personal Care": [
        "hair salon", "barber shop", "nail salon", "spa",
        "beauty salon", "tattoo shop", "waxing salon",
        "lash extensions", "makeup artist", "skincare clinic",
    ],
    "Education & Tutoring": [
        "tutoring center", "driving school", "music school",
        "dance studio", "martial arts school", "preschool",
        "language school", "art class", "coding bootcamp",
    ],
    "Professional Services": [
        "accountant", "financial advisor", "insurance agent",
        "marketing agency", "web design agency", "photographer",
        "videographer", "printing shop", "notary public",
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
            wait = 10 * (attempt + 1)
            time.sleep(wait)
    return {}


def filter_place(place, min_reviews, min_rating, seen_ids):
    pid = place.get("id")
    if not pid or pid in seen_ids:
        return None

    website = place.get("websiteUri", "")
    if website:
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

    name = place.get("displayName", {}).get("text", "N/A")
    return {
        "Business Name": name,
        "Address": place.get("formattedAddress", "N/A"),
        "Phone": place.get("nationalPhoneNumber", "N/A"),
        "Rating": rating,
        "Reviews": reviews,
        "Business Type": "",
        "Google Maps URL": place.get("googleMapsUri", ""),
    }


def run_search(cities, business_types, api_key, region_code="US",
               min_reviews=50, min_rating=4.0, target_leads=100):
    """Generator that yields (lead_dict, progress_dict) tuples.

    progress_dict has: leads_found, total_target, current_city, current_type, api_calls
    """
    seen_ids = set()
    leads_found = 0
    api_calls = 0

    for ci, city in enumerate(cities):
        if leads_found >= target_leads:
            break

        for biz_type in business_types:
            if leads_found >= target_leads:
                break

            query = f"{biz_type} in {city}"
            data = search_places(query, api_key, region_code)
            api_calls += 1

            places = data.get("places", [])
            for place in places:
                if leads_found >= target_leads:
                    break
                lead = filter_place(place, min_reviews, min_rating, seen_ids)
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

            # Follow pagination
            next_token = data.get("nextPageToken")
            while next_token and leads_found < target_leads:
                time.sleep(0.1)
                data = search_places(query, api_key, region_code, page_token=next_token)
                api_calls += 1
                places = data.get("places", [])
                next_token = data.get("nextPageToken")

                for place in places:
                    if leads_found >= target_leads:
                        break
                    lead = filter_place(place, min_reviews, min_rating, seen_ids)
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
