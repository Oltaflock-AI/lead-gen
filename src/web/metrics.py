import csv
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = PROJECT_ROOT / "data" / "imports"
OUTPUTS_DIR = PROJECT_ROOT / "data" / "outputs"


COLUMN_ALIASES = {
    "business_name": ["business_name", "Business Name", "prospect_company_name"],
    "email": ["email", "contact_emails", "contact_professions_email"],
    "phone": ["phone_number", "phone", "Phone", "contact_mobile_phone", "contact_phone_numbers"],
    "address": ["verified_address", "address", "Address"],
    "rating": ["rating", "Rating"],
    "review_count": ["review_count", "Reviews", "total_reviews"],
    "website": ["business_website", "website", "Website", "prospect_company_website"],
    "city": ["City", "business_region", "prospect_country_name"],
    "business_type": ["Business Type", "business_naics_description", "prospect_job_title"],
    "google_maps_url": ["google_maps_url", "Google Maps URL"],
}


def _pick(row, key):
    for col in COLUMN_ALIASES.get(key, [key]):
        v = row.get(col)
        if v not in (None, "", "N/A"):
            return v
    return ""


def normalize_lead(row):
    rating = _pick(row, "rating") or 0
    reviews = _pick(row, "review_count") or 0
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0.0
    try:
        reviews = int(float(reviews))
    except (TypeError, ValueError):
        reviews = 0
    return {
        "business_name": _pick(row, "business_name"),
        "email": _pick(row, "email"),
        "phone": _pick(row, "phone"),
        "address": _pick(row, "address"),
        "rating": rating,
        "review_count": reviews,
        "website": _pick(row, "website"),
        "city": _pick(row, "city"),
        "business_type": _pick(row, "business_type"),
        "google_maps_url": _pick(row, "google_maps_url"),
    }


def quality_score(lead):
    """0–100 weighted score. See plan for formula."""
    norm = normalize_lead(lead) if "rating" not in lead or isinstance(lead.get("rating"), str) else lead
    rating_norm = max(0.0, (norm["rating"] - 3.0) / 2.0) if norm["rating"] else 0.0
    rating_norm = min(1.0, rating_norm)
    reviews_norm = min(1.0, math.log10(norm["review_count"] + 1) / 3.0) if norm["review_count"] else 0.0
    has_email = 1.0 if norm["email"] else 0.0
    has_phone = 1.0 if norm["phone"] else 0.0
    no_website = 1.0 if not norm["website"] else 0.0
    score = 100 * (
        0.40 * rating_norm
        + 0.20 * reviews_norm
        + 0.15 * has_email
        + 0.15 * has_phone
        + 0.10 * no_website
    )
    return round(score, 1)


def list_csvs():
    """Return list of {path, name, dir, rows} for all CSVs in imports/ + outputs/."""
    out = []
    for d, label in [(IMPORTS_DIR, "imports"), (OUTPUTS_DIR, "outputs")]:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.csv")):
            try:
                with open(p, newline="", encoding="utf-8") as f:
                    n = sum(1 for _ in csv.reader(f)) - 1
                    n = max(0, n)
            except Exception:
                n = 0
            out.append({"path": str(p), "name": p.name, "dir": label, "rows": n})
    return out


def read_csv_with_scores(csv_path):
    """Return (fieldnames, rows_with_score). rows are dicts with extra '_score'."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return [], []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            norm = normalize_lead(row)
            row["_score"] = quality_score(norm)
            row["_normalized"] = norm
            rows.append(row)
    return fieldnames, rows


def csv_summary(csv_path):
    """Aggregate stats for one CSV."""
    _, rows = read_csv_with_scores(csv_path)
    n = len(rows)
    if n == 0:
        return {"path": str(csv_path), "name": Path(csv_path).name,
                "rows": 0, "avg_score": 0, "qualified": 0,
                "with_email": 0, "with_phone": 0,
                "fit_checked": 0, "avg_fit": 0}
    scores = [r["_score"] for r in rows]
    qualified = sum(1 for s in scores if s >= 60)
    with_email = sum(1 for r in rows if r["_normalized"]["email"])
    with_phone = sum(1 for r in rows if r["_normalized"]["phone"])
    fits = [float(r["ai_fit_score"]) for r in rows
            if r.get("ai_fit_score") not in (None, "", "0")]
    return {
        "path": str(csv_path),
        "name": Path(csv_path).name,
        "rows": n,
        "avg_score": round(sum(scores) / n, 1),
        "qualified": qualified,
        "with_email": with_email,
        "with_phone": with_phone,
        "fit_checked": len(fits),
        "avg_fit": round(sum(fits) / len(fits), 1) if fits else 0,
    }


def dashboard_summary(settings, outreach):
    """Build the metrics shown on the dashboard.

    close_rate / avg_deal_value default to 0 when unset so the pipeline
    tiles read $0 (and the UI shows a hint to configure them) instead of
    pretending we have a real revenue projection.
    """
    try:
        close_rate = float(settings.get("close_rate") or 0)
    except (TypeError, ValueError):
        close_rate = 0.0
    try:
        avg_deal = float(settings.get("avg_deal_value") or 0)
    except (TypeError, ValueError):
        avg_deal = 0.0
    revenue_configured = close_rate > 0 and avg_deal > 0

    csvs = list_csvs()
    per_csv = [csv_summary(c["path"]) for c in csvs]

    total_leads = sum(c["rows"] for c in per_csv)
    total_qualified = sum(c["qualified"] for c in per_csv)
    avg_score = round(
        sum(c["avg_score"] * c["rows"] for c in per_csv) / total_leads, 1
    ) if total_leads else 0

    sent = outreach["sent"]
    replied = outreach["replied"]

    projected_revenue = total_qualified * close_rate * avg_deal
    contacted_revenue = sent * close_rate * avg_deal
    replied_revenue = replied * (close_rate * 5) * avg_deal

    return {
        "total_leads": total_leads,
        "total_qualified": total_qualified,
        "avg_score": avg_score,
        "emails_sent": sent,
        "emails_failed": outreach["failed"],
        "replies": replied,
        "projected_revenue": round(projected_revenue, 2),
        "contacted_revenue": round(contacted_revenue, 2),
        "replied_revenue": round(replied_revenue, 2),
        "close_rate": close_rate,
        "avg_deal_value": avg_deal,
        "revenue_configured": revenue_configured,
        "per_csv": per_csv,
    }
