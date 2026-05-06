import csv
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = PROJECT_ROOT / "data" / "imports"
OUTPUTS_DIR = PROJECT_ROOT / "data" / "outputs"


COLUMN_ALIASES = {
    "business_name": ["business_name", "Business Name", "prospect_company_name"],
    "email": ["email", "Email", "contact_emails", "contact_professions_email"],
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
    """Build the metrics shown on the dashboard."""
    csvs = list_csvs()
    per_csv = [csv_summary(c["path"]) for c in csvs]

    total_leads = sum(c["rows"] for c in per_csv)
    total_qualified = sum(c["qualified"] for c in per_csv)
    avg_score = round(
        sum(c["avg_score"] * c["rows"] for c in per_csv) / total_leads, 1
    ) if total_leads else 0

    return {
        "total_leads": total_leads,
        "total_qualified": total_qualified,
        "avg_score": avg_score,
        "emails_sent": outreach["sent"],
        "emails_failed": outreach["failed"],
        "replies": outreach["replied"],
        "per_csv": per_csv,
    }


# ─────────────────────────── dashboard redesign helpers ───────────────────────────
# Read-only aggregations layered on top of the existing summary helpers.


def daily_sends_last_n(n=14):
    """Return list of {date, count} for the last N days, oldest first.
    Counts merge one-shot outreach_log + sent sequence_messages so the
    sparkline matches the actual outbound volume the user sees."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    db_path = PROJECT_ROOT / "data" / "outreach.db"
    if not db_path.exists():
        return []
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)) for i in range(n - 1, -1, -1)]
    by_date = {d.isoformat(): 0 for d in days}
    start_iso = (datetime.combine(days[0], datetime.min.time(),
                                  tzinfo=timezone.utc)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for sql in (
            "SELECT substr(sent_at,1,10) AS d, COUNT(*) AS n "
            "FROM outreach_log WHERE sent_at >= ? "
            "GROUP BY d",
            "SELECT substr(sent_at,1,10) AS d, COUNT(*) AS n "
            "FROM sequence_messages "
            "WHERE status = 'sent' AND sent_at IS NOT NULL AND sent_at >= ? "
            "GROUP BY d",
        ):
            for row in conn.execute(sql, (start_iso,)).fetchall():
                if row["d"] in by_date:
                    by_date[row["d"]] += int(row["n"] or 0)
    finally:
        conn.close()
    return [{"date": d, "count": by_date[d]} for d in by_date]


def sparkline_points(daily, width=200, height=24, pad=2):
    """Convert daily_sends_last_n() output to an SVG polyline points string."""
    if not daily:
        return ""
    counts = [d["count"] for d in daily]
    n = len(counts)
    cmax = max(counts) or 1
    step = (width - pad * 2) / max(1, n - 1)
    inner = height - pad * 2
    pts = []
    for i, c in enumerate(counts):
        x = pad + i * step
        y = pad + (inner - (c / cmax) * inner)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def csv_health_score(summary):
    """Score a CSV 0–100 based on coverage + quality + freshness.
    `summary` is the dict from csv_summary(). Returns dict with `score`,
    `band` (good/ok/bad), and `label`."""
    rows = max(1, summary.get("rows") or 1)
    email_pct = (summary.get("with_email") or 0) / rows
    phone_pct = (summary.get("with_phone") or 0) / rows
    score_pct = (summary.get("avg_score") or 0) / 100.0
    fresh = 1.0  # freshness data not surfaced through csv_summary yet
    raw = 40 * email_pct + 25 * phone_pct + 25 * score_pct + 10 * fresh
    score = round(raw)
    if score >= 85:
        band, label = "good", "excellent"
    elif score >= 70:
        band, label = "ok", "fine"
    else:
        band, label = "bad", "needs review"
    return {"score": score, "band": band, "label": label,
            "email_pct": round(100 * email_pct),
            "phone_pct": round(100 * phone_pct),
            "score_pct": round(100 * score_pct)}


# ─────────────────────────── dashboard-redesign aggregations ───────────────────────────
# Append-only · pure compute over CSVs and the read helpers in db.py.


def total_leads_all_time():
    """Sum of `rows` across every CSV in data/outputs/. NEVER windowed —
    Total leads is an all-time KPI on the redesigned dashboard."""
    try:
        return sum(csv_summary(c["path"])["rows"] for c in list_csvs())
    except Exception:
        return 0


def delivery_rate(since_iso=None):
    """delivered / sent in the window. Returns 1.0 when sent == 0 so a
    quiet day reads as "no problem" instead of 0% delivery."""
    from . import db
    sent = db.count_outreach_since(since_iso)
    if not sent:
        return 1.0
    delivered = db.count_delivered_since(since_iso)
    return delivered / sent


def bounce_rate(since_iso=None):
    """bounced / sent in the window. Returns 0.0 when sent == 0."""
    from . import db
    sent = db.count_outreach_since(since_iso)
    if not sent:
        return 0.0
    return db.count_bounced_since(since_iso) / sent


def open_rate_on_delivered(since_iso=None):
    """unique_opens / delivered_count. Denominator is delivered, NOT sent
    — dividing by sent inflates the failure case as a low open rate.
    Returns 0.0 when delivered == 0."""
    from . import db
    delivered = db.count_delivered_since(since_iso)
    if not delivered:
        return 0.0
    return db.count_unique_opens_since(since_iso) / delivered
