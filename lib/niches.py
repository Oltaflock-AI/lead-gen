"""Niche presets + geo data for the scrape form.

NICHE_PRESETS: niche label -> list of Google Places search terms (business types).
Pick a niche, optionally narrow to specific types, scrape runs one query per type.
"""

NICHE_PRESETS = {
    "Real Estate": [
        "real estate agency", "realtor", "real estate broker",
        "property management company", "property developer", "buyers agent",
    ],
    "Home Services": [
        "plumber", "electrician", "HVAC service", "roofing contractor",
        "landscaping company", "cleaning service", "pest control",
        "handyman service", "painting contractor", "flooring installer",
    ],
    "Construction & Trades": [
        "general contractor", "home builder", "civil engineering company",
        "architecture firm", "concrete contractor", "fencing contractor",
        "garage door supplier", "solar energy company",
    ],
    "Manufacturing & Industrial": [
        "manufacturer", "factory", "metal fabricator", "machine shop",
        "plastics manufacturer", "packaging supplier", "industrial equipment supplier",
        "CNC machining service", "wholesale supplier",
    ],
    "D2C & Retail Brands": [
        "boutique", "clothing store", "cosmetics store", "furniture store",
        "specialty food store", "home goods store", "jewelry store",
        "sporting goods store", "pet supply store",
    ],
    "Restaurants & Food": [
        "restaurant", "cafe", "bakery", "pizza restaurant", "coffee shop",
        "catering service", "food truck", "juice bar",
    ],
    "Health & Wellness": [
        "dentist", "chiropractor", "physical therapist", "dermatologist",
        "veterinarian", "optometrist", "medical clinic", "cosmetic clinic",
    ],
    "Fitness": [
        "gym", "fitness center", "crossfit box", "yoga studio",
        "pilates studio", "martial arts school", "personal trainer",
    ],
    "Beauty & Personal Care": [
        "hair salon", "barber shop", "nail salon", "day spa",
        "beauty salon", "tattoo shop", "med spa", "lash studio",
    ],
    "Auto Services": [
        "auto repair shop", "car dealership", "auto detailing", "tire shop",
        "auto body shop", "car wash", "towing service",
    ],
    "Professional Services": [
        "accountant", "bookkeeping service", "financial advisor", "insurance agency",
        "mortgage broker", "notary public", "business consultant",
    ],
    "Law Firms": [
        "law firm", "attorney", "immigration lawyer", "personal injury lawyer",
        "family law attorney", "criminal defense lawyer", "corporate law firm",
    ],
    "Marketing & Creative": [
        "marketing agency", "web design agency", "branding studio",
        "video production company", "advertising agency", "SEO agency",
        "photography studio", "PR agency",
    ],
    "Tech & SaaS": [
        "software company", "IT services company", "web development company",
        "app development company", "managed IT services", "cybersecurity company",
    ],
    "Hospitality & Events": [
        "hotel", "event venue", "wedding planner", "boutique hotel",
        "conference center", "event management company",
    ],
    "Logistics & Transport": [
        "freight company", "courier service", "moving company",
        "warehouse", "logistics company", "trucking company",
    ],
    "Education & Training": [
        "tutoring center", "driving school", "language school",
        "coaching center", "vocational school", "music school",
    ],
    "Trades — Specialty": [
        "solar installer", "pool service", "garage door company",
        "fencing company", "window installer", "kitchen remodeler",
        "deck builder", "irrigation company",
    ],
}

# Country -> Google Places region code (biases results to that country).
COUNTRY_REGION_CODES = {
    "United States": "US", "Canada": "CA", "United Kingdom": "GB",
    "Australia": "AU", "New Zealand": "NZ", "Ireland": "IE",
    "India": "IN", "Singapore": "SG", "United Arab Emirates": "AE",
    "Germany": "DE", "France": "FR", "Spain": "ES", "Italy": "IT",
    "Netherlands": "NL", "Sweden": "SE", "South Africa": "ZA",
}

WEBSITE_FILTERS = {
    "any": "Any (with or without a website)",
    "has": "Has a website only",
    "none": "No website only",
}


def types_for(niche: str) -> list[str]:
    return NICHE_PRESETS.get(niche, [])
