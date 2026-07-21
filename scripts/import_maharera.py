"""Import MahaRERA-registered real-estate agents into the leads table.

Reads the govt-registry scrape (data/outputs/maharera_agents.csv, produced by
scripts/maharera_scrape.py) and upserts leads into a PAUSED campaign
"maharera-agents". The registry already provides a contact email, so leads are
stamped enrichment_status='enriched' directly — no OpenAI enrichment spend on
10.7k rows.

Safety (non-negotiable):
  - DRY-RUN BY DEFAULT. Nothing is written unless --apply is passed.
  - The campaign is created with active=false and this script never activates
    it. Nothing sends until the owner flips it on in the dashboard.
  - Never imports lib.sequence and never sends anything.

Idempotent: leads upsert on UNIQUE(campaign_id, business); campaigns are
reused by name. Re-runs are safe.

Usage:
  python3 scripts/import_maharera.py                    # dry-run, first 500
  python3 scripts/import_maharera.py --limit 2000       # dry-run, first 2000
  python3 scripts/import_maharera.py --apply --limit 500
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "outputs", "maharera_agents.csv")
CAMPAIGN_NAME = "maharera-agents"
INSERT_CHUNK = 200

# Columns from scripts/maharera_scrape.py FIELDS.
ADDRESS_PARTS = ("buildingName", "unitNumber", "streetName", "locality",
                 "landmark", "villageName", "pincode")


def require_env() -> None:
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing env: {', '.join(missing)}. Set them (or add to .env) "
                 "before running this importer.")


def first_name(agent_name: str) -> str | None:
    """First token of the agent's name, normalized for a greeting. Registry
    names are often ALL CAPS ('RAHUL YADAV') — capitalize those so the email
    doesn't shout. Skip bare initials ('M S Realty')."""
    tok = (agent_name.split() or [""])[0].strip(".,")
    if len(tok) < 2 or not tok.isalpha():
        return None
    return tok.capitalize() if (tok.isupper() or tok.islower()) else tok


def row_to_lead(row: dict, campaign_id: int | None) -> dict | None:
    """Mirror api/index.py _csv_row_to_lead's shape for the leads table.
    Returns None when the row has no usable email (no '@')."""
    business = (row.get("agentName") or "").strip()
    email = (row.get("emailId") or "").strip().lower()
    if not business or "@" not in email:
        return None
    address = ", ".join(p for p in ((row.get(k) or "").strip(" ,") for k in ADDRESS_PARTS) if p)
    city = (row.get("districtName") or "").strip() or (row.get("talukaName") or "").strip()
    signals: dict = {"email_source": "maharera-registry", "email_confidence": "unverified"}
    fname = first_name(business)
    if fname:
        signals["contact_first_name"] = fname
    return {
        "campaign_id": campaign_id,
        "business": business,
        "website": (row.get("websiteURL") or "").strip() or None,
        "phone": (row.get("mobileNo") or "").strip() or None,
        "address": address or None,
        "city": city or None,
        "country": "India",
        "source": "maharera-registry",
        "email": email,
        "email_status": "provided",
        # Email comes straight from the registry — skip the enrichment queue
        # so 10.7k leads never hit the OpenAI enrichment worker.
        "enrichment_status": "enriched",
        "signals": signals,
        "decision_maker": {"name": business, "title": "RERA registered agent",
                           "source": "maharera"},
    }


def parse_csv(path: str, limit: int) -> tuple[list[dict], dict]:
    """Parse + validate + dedupe. Returns (leads capped at limit, counters)."""
    n = {"parsed": 0, "valid_email": 0, "skip_bad_email": 0,
         "skip_dup_email": 0, "skip_dup_business": 0, "eligible": 0}
    leads: list[dict] = []
    seen_email: set[str] = set()
    seen_business: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            n["parsed"] += 1
            lead = row_to_lead(row, None)
            if lead is None:
                n["skip_bad_email"] += 1
                continue
            n["valid_email"] += 1
            if lead["email"] in seen_email:
                n["skip_dup_email"] += 1
                continue
            seen_email.add(lead["email"])
            # UNIQUE(campaign_id, business) — a duplicate name in one batch
            # would make the upsert hit the same row twice and fail.
            if lead["business"].lower() in seen_business:
                n["skip_dup_business"] += 1
                continue
            seen_business.add(lead["business"].lower())
            n["eligible"] += 1
            if len(leads) < limit:
                leads.append(lead)
    return leads, n


def ensure_campaign(sb) -> dict:
    """Reuse the campaign by name, else create it PAUSED (active=false is
    explicit — the campaigns table defaults active to true)."""
    rows = sb.select("campaigns", {"name": f"eq.{CAMPAIGN_NAME}"}, limit=1)
    if rows:
        camp = rows[0]
        if camp.get("active"):
            print(f"WARNING: campaign '{CAMPAIGN_NAME}' is ACTIVE — new leads "
                  "will enter the live sequencer. Pause it first if unintended.")
        return camp
    return sb.insert("campaigns", {
        "name": CAMPAIGN_NAME,
        "niche": "real-estate",
        "region": "IN",
        "active": False,
        "daily_scrape_target": 0,
        "notes": "MahaRERA public registry import (scripts/import_maharera.py)",
    })[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Import MahaRERA agents as leads (dry-run by default).")
    ap.add_argument("--csv", default=CSV_PATH, help="path to maharera_agents.csv")
    ap.add_argument("--limit", type=int, default=500, help="max leads to import per run (default 500)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write to Supabase (default is dry-run)")
    args = ap.parse_args()
    if args.limit <= 0:
        sys.exit("--limit must be a positive integer")
    if not os.path.isfile(args.csv):
        sys.exit(f"CSV not found: {args.csv}")
    require_env()

    leads, n = parse_csv(args.csv, args.limit)
    mode = "APPLY" if args.apply else "DRY RUN (no writes — pass --apply to import)"
    print(f"MahaRERA import — {mode}")
    print(f"  csv                     {args.csv}")
    print(f"  rows parsed             {n['parsed']}")
    print(f"  valid email             {n['valid_email']}")
    print(f"  skipped: no/bad email   {n['skip_bad_email']}")
    print(f"  skipped: dup email      {n['skip_dup_email']}")
    print(f"  skipped: dup business   {n['skip_dup_business']}")
    print(f"  eligible after dedupe   {n['eligible']}")
    print(f"  limit this run          {args.limit}")

    if not args.apply:
        print(f"  would insert            {len(leads)}")
        print(f"Campaign '{CAMPAIGN_NAME}' would be created/reused with active=false. "
              "Nothing sends until the owner activates it.")
        return

    from lib import supabase as sb  # after env check — module reads env on import
    camp = ensure_campaign(sb)
    for lead in leads:
        lead["campaign_id"] = camp["id"]
    inserted = 0
    for i in range(0, len(leads), INSERT_CHUNK):
        chunk = leads[i:i + INSERT_CHUNK]
        sb.insert("leads", chunk, on_conflict="campaign_id,business")
        inserted += len(chunk)
        print(f"  upserted {inserted}/{len(leads)}")
    print(f"  inserted (upserted)     {inserted}")
    print(f"Campaign '{CAMPAIGN_NAME}' (id={camp['id']}) is "
          f"{'ACTIVE' if camp.get('active') else 'PAUSED'} — it was not activated by this "
          "script; activate it from the dashboard when ready.")


if __name__ == "__main__":
    main()
