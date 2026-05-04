"""Per-lead email lookup + MX verification.

Wraps the helpers already in src/processors/enrich_leads.py so we don't
duplicate the DDG search + MX check logic.
"""
import csv
from pathlib import Path

from src.processors.enrich_leads import find_email, verify_mx


def verify_lead_email(business_name, current_email="", region="", country=""):
    """Return {email, verified, source}.

    source: 'existing'   — current_email was provided and its MX is valid
            'search'     — found via DDG search; verified holds MX result
            'none'       — nothing found
    """
    if current_email and "@" in current_email:
        domain = current_email.split("@")[1].strip().lower()
        if verify_mx(domain):
            return {"email": current_email, "verified": True, "source": "existing"}

    found = find_email(business_name, region, country) if business_name else ""
    if found:
        domain = found.split("@")[1]
        return {"email": found, "verified": verify_mx(domain), "source": "search"}

    return {"email": current_email or "", "verified": False, "source": "none"}


def update_csv_email(csv_path, business_name, new_email):
    """Find the row whose business_name matches and write `new_email` into the
    first email-like column. Returns True if updated, False if no row matched.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return False

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    name_col = None
    for c in ("business_name", "Business Name", "prospect_company_name"):
        if c in fieldnames:
            name_col = c
            break
    if name_col is None:
        return False

    email_col = None
    for c in ("email", "contact_emails", "contact_professions_email"):
        if c in fieldnames:
            email_col = c
            break
    if email_col is None:
        email_col = "email"
        fieldnames.insert(fieldnames.index(name_col) + 1, email_col)

    updated = False
    for r in rows:
        if (r.get(name_col) or "").strip() == (business_name or "").strip():
            r[email_col] = new_email
            updated = True

    if not updated:
        return False

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return True
