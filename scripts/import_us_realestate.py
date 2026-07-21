"""Import US state licensee registries (TREC / DBPR) into the leads table.

Sources (--source):
  trec              Texas TREC "Broker and Sales Agent License Holder Information"
                    High Value Data Set, published on the Texas Open Data Portal
                    (dataset s7ft-44qi, updated daily, 300k+ rows, has header row).
                    https://data.texas.gov/api/views/s7ft-44qi/rows.csv?accessType=DOWNLOAD
  dbpr              Florida DBPR "Real Estate Sales Associates and Brokers" weekly
                    extract (quote/comma CSV, NO header row, ~450k rows).
                    https://www2.myfloridalicense.com/sto/file_download/extracts//REALESTATE2501LICENSE_1.csv
  dbpr-contractors  Florida DBPR construction licensee weekly extract, filtered to
                    roofing (CCC) + pool (CPC/RP) contractors (NO header row).
                    https://www2.myfloridalicense.com/sto/file_download/extracts//CONSTRUCTIONLICENSE_1.csv

None of these registries publish email or phone, so by default every row is
SKIPPED (importing them as enrichment_status='pending' would push tens of
thousands of leads into the OpenAI enrichment worker). Pass --include-no-email
to import them as 'pending' anyway; rows that do carry an email are imported
as 'enriched' (mirrors scripts/import_maharera.py).

Safety (non-negotiable):
  - DRY-RUN BY DEFAULT. Nothing is written unless --apply is passed.
  - Campaigns are created with active=false and this script never activates
    them. Nothing sends until the owner flips it on in the dashboard.
  - Never imports lib.sequence and never sends anything.

Idempotent: leads upsert on UNIQUE(campaign_id, business); campaigns are
reused by name. Re-runs are safe.

Usage:
  python3 scripts/import_us_realestate.py --source trec                # dry-run
  python3 scripts/import_us_realestate.py --source dbpr --brokers-only
  python3 scripts/import_us_realestate.py --source trec --include-no-email --apply --limit 500
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

REGISTRY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "registry")
INSERT_CHUNK = 200

# Company suffixes to keep upper-cased after title-casing a brokerage name.
_UPPER_SUFFIXES = {"llc", "inc", "pllc", "llp", "lp", "pa", "pc", "co"}
# Generational suffixes that trail the given names in "LAST, FIRST M JR" rows.
_GEN_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def require_env() -> None:
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing env: {', '.join(missing)}. Set them (or add to .env) "
                 "before running this importer.")


def _cap(tok: str) -> str:
    """Title-case one ALL-CAPS/lower token; leave mixed case alone (McDonald)."""
    return tok.title() if (tok.isupper() or tok.islower()) else tok


def proper_name(raw: str) -> str:
    """'ASHISH GOOLLA' -> 'Ashish Goolla'. Registry names shout; greetings must not."""
    return " ".join(_cap(t) for t in raw.split())


def company_name(raw: str) -> str | None:
    """Title-case a brokerage/company, keeping LLC/INC style suffixes upper."""
    raw = (raw or "").strip(" ,.")
    if not raw:
        return None
    toks = []
    for t in raw.split():
        toks.append(t.upper() if t.strip(".,").lower() in _UPPER_SUFFIXES else _cap(t))
    return " ".join(toks)


def person_from_last_first(raw: str) -> str | None:
    """DBPR names are 'LAST, FIRST MIDDLE [JR]'. Returns 'First Middle Last Jr'
    or None when there is no comma (usually a company record, not a person)."""
    raw = (raw or "").strip()
    if "," not in raw:
        return None
    last, _, first = raw.partition(",")
    first_toks = first.split()
    if not first_toks or not last.strip():
        return None
    gen = ""
    if first_toks[-1].strip(".").lower() in _GEN_SUFFIXES:
        gen = " " + _cap(first_toks.pop().strip("."))
        if not first_toks:
            return None
    return proper_name(" ".join(first_toks) + " " + last.strip()) + gen


def first_name(person: str) -> str | None:
    """First token of the person's name, for the greeting. Skip bare initials."""
    tok = (person.split() or [""])[0].strip(".,")
    if len(tok) < 2 or not tok.isalpha():
        return None
    return tok.capitalize() if (tok.isupper() or tok.islower()) else tok


# ---------------------------------------------------------------------------
# Per-source row parsers. Each returns (record, None) or (None, skip_reason).
# record: {name, title, company, license_number, license_status, address,
#          city, state, email, phone, is_broker}
# ---------------------------------------------------------------------------

def parse_trec_row(row: dict) -> tuple[dict | None, str | None]:
    status = (row.get("Status") or "").strip()
    if status != "Active":
        return None, "status"
    ltype = (row.get("License Type") or "").strip()
    if ltype == "Broker Company":            # a firm, not a named person
        return None, "not_person"
    if ltype not in ("Sales Agent", "Broker Individual"):
        return None, "status"                # inspectors/ERW etc, if ever present
    person_raw = " ".join(t for t in ((row.get("First Name") or "").strip(),
                                      (row.get("Last Name") or "").strip()) if t) \
        or (row.get("Full Name") or "").strip()
    person = proper_name(person_raw)
    if not person or first_name(person) is None:
        return None, "not_person"
    # Related license = sponsoring broker (often the brokerage) for sales agents.
    sponsor_raw = (row.get("Related License Full Name") or "").strip()
    company = person_from_last_first(sponsor_raw) or company_name(sponsor_raw)
    return {
        "name": person,
        "title": "broker" if ltype == "Broker Individual" else "sales agent",
        "company": company,
        "license_number": (row.get("License Number") or "").strip() or None,
        "license_status": status,
        "address": None,                     # TREC HVDS carries no street address
        "city": None,                        # County column is empty file-wide
        "state": "TX",
        "email": None,                       # registry publishes no email
        "phone": None,                       # registry publishes no phone
        "is_broker": ltype == "Broker Individual",
    }, None


# DBPR real-estate extract columns (no header; verified against the live file):
# 0 board, 1 licensee name, 2 dba, 3 rank, 4-6 address 1-3, 7 city, 8 state,
# 9 zip, 10 county, 11 license number, 12 primary status, 13 secondary status,
# 14 original date, 15 status effective, 16 expiration, 17 alternate license,
# 18 self proprietor, 19 employer (brokerage), 20 employer license number.
_DBPR_RE_TITLES = {"SL": "sales agent", "BK": "broker", "BL": "broker"}


def parse_dbpr_re_row(row: list) -> tuple[dict | None, str | None]:
    if len(row) < 21:
        return None, "not_person"
    if row[12].strip() != "Current" or row[13].strip() != "Active":
        return None, "status"
    rank = (row[3].split() or [""])[0].upper()
    title = _DBPR_RE_TITLES.get(rank)
    if title is None:
        return None, "status"
    person = person_from_last_first(row[1])
    if not person or first_name(person) is None:
        return None, "not_person"
    address = ", ".join(p for p in (row[4].strip(), row[5].strip(), row[6].strip(),
                                    row[9].strip()) if p)
    return {
        "name": person,
        "title": title,
        "company": company_name(row[19]) or company_name(row[2]),
        "license_number": row[17].strip() or row[11].strip() or None,
        "license_status": "Current/Active",
        "address": address or None,
        "city": _cap(row[7].strip()) if row[7].strip() else None,
        "state": row[8].strip() or "FL",
        "email": None,                       # extract has no email column
        "phone": None,                       # extract has no phone column
        "is_broker": rank in ("BK", "BL"),
    }, None


# DBPR construction extract columns (no header; verified against the live file):
# 0 board '06', 1 occupation code, 2 licensee name, 3 dba, 4 class code,
# 5-7 address 1-3, 8 city, 9 state, 10 zip, 11 county code, 12 seq license
# number, 13 primary status (C/P/S), 14 secondary status (A/I/blank),
# 15 original date, 16 effective, 17 expiration, 18-19 blank/renewal,
# 20 public license number (e.g. CCC056901), 21 blank.
_DBPR_CONTRACTOR_TITLES = {"CCC": "roofing contractor",
                           "CPC": "pool contractor", "RP": "pool contractor"}


def parse_dbpr_contractor_row(row: list) -> tuple[dict | None, str | None]:
    if len(row) < 21:
        return None, "not_person"
    title = _DBPR_CONTRACTOR_TITLES.get(row[1].strip().upper())
    if title is None:
        return None, "filtered_trade"       # other trades are out of scope
    if row[13].strip() != "C" or row[14].strip() != "A":
        return None, "status"
    person = person_from_last_first(row[2])
    if not person or first_name(person) is None:
        return None, "not_person"
    dba = row[3].strip()
    company = None if dba.upper() == "INDIVIDUAL" else company_name(dba)
    address = ", ".join(p for p in (row[5].strip(), row[6].strip(), row[7].strip(),
                                    row[10].strip()) if p)
    return {
        "name": person,
        "title": title,
        "company": company,
        "license_number": row[20].strip() or row[12].strip() or None,
        "license_status": "Current/Active",
        "address": address or None,
        "city": _cap(row[8].strip()) if row[8].strip() else None,
        "state": row[9].strip() or "FL",
        "email": None,                       # extract has no email column
        "phone": None,                       # extract has no phone column
        "is_broker": True,                   # every licensee IS the decision-maker
    }, None


SOURCES = {
    "trec": {
        "campaign": "trec-texas-agents",
        "niche": "real-estate",
        "csv": os.path.join(REGISTRY_DIR, "trec_brokers_sales_agents.csv"),
        "has_header": True,
        "parse": parse_trec_row,
        "email_source": "trec-registry",
        "dm_source": "trec",
        "notes": "TREC High Value Data Set import (scripts/import_us_realestate.py)",
    },
    "dbpr": {
        "campaign": "dbpr-florida-agents",
        "niche": "real-estate",
        "csv": os.path.join(REGISTRY_DIR, "dbpr_realestate_licensees.csv"),
        "has_header": False,
        "parse": parse_dbpr_re_row,
        "email_source": "dbpr-registry",
        "dm_source": "dbpr",
        "notes": "FL DBPR real-estate licensee extract import (scripts/import_us_realestate.py)",
    },
    "dbpr-contractors": {
        "campaign": "dbpr-fl-contractors",
        "niche": "home-services",
        "csv": os.path.join(REGISTRY_DIR, "dbpr_construction_licensees.csv"),
        "has_header": False,
        "parse": parse_dbpr_contractor_row,
        "email_source": "dbpr-registry",
        "dm_source": "dbpr",
        "notes": "FL DBPR roofing (CCC) + pool (CPC/RP) contractor import (scripts/import_us_realestate.py)",
    },
}


def rec_to_lead(rec: dict, cfg: dict, campaign_id: int | None) -> dict:
    """Mirror api/index.py _csv_row_to_lead's shape for the leads table."""
    # business must be unique per campaign; person names collide, so append
    # the brokerage/company when we have one. (No em/en dashes — copy rule.)
    business = f"{rec['name']} ({rec['company']})" if rec.get("company") else rec["name"]
    signals: dict = {"email_source": cfg["email_source"], "email_confidence": "unverified"}
    fname = first_name(rec["name"])
    if fname:
        signals["contact_first_name"] = fname
    for k in ("license_number", "license_status", "city", "state"):
        if rec.get(k):
            signals[k] = rec[k]
    lead = {
        "campaign_id": campaign_id,
        "business": business,
        "website": None,
        "phone": rec.get("phone") or None,
        "address": rec.get("address") or None,
        "city": rec.get("city") or None,
        "country": "USA",
        "source": cfg["email_source"],
        # Registry email (when present) skips the enrichment queue; rows with
        # no email stay 'pending' and are only imported with --include-no-email.
        "enrichment_status": "enriched" if rec.get("email") else "pending",
        "signals": signals,
        "decision_maker": {"name": rec["name"], "title": rec["title"],
                           "source": cfg["dm_source"]},
    }
    if rec.get("email"):
        lead["email"] = rec["email"].lower()
        lead["email_status"] = "provided"
    return lead


def parse_file(path: str, cfg: dict, limit: int, brokers_only: bool,
               include_no_email: bool) -> tuple[list[dict], dict]:
    """Parse + filter + dedupe. Returns (leads capped at limit, counters)."""
    n = {"parsed": 0, "skip_status": 0, "skip_not_person": 0, "skip_trade": 0,
         "skip_brokers_only": 0, "skip_dup_email": 0, "skip_dup_business": 0,
         "eligible_with_email": 0, "eligible_no_email": 0}
    leads: list[dict] = []
    seen_email: set[str] = set()
    seen_business: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f) if cfg["has_header"] else csv.reader(f)
        for row in reader:
            n["parsed"] += 1
            rec, reason = cfg["parse"](row)
            if rec is None:
                key = {"status": "skip_status", "not_person": "skip_not_person",
                       "filtered_trade": "skip_trade"}[reason]
                n[key] += 1
                continue
            if brokers_only and not rec["is_broker"]:
                n["skip_brokers_only"] += 1
                continue
            lead = rec_to_lead(rec, cfg, None)
            email = lead.get("email")
            if email:
                if email in seen_email:
                    n["skip_dup_email"] += 1
                    continue
                seen_email.add(email)
            # UNIQUE(campaign_id, business) — a duplicate name in one batch
            # would make the upsert hit the same row twice and fail.
            if lead["business"].lower() in seen_business:
                n["skip_dup_business"] += 1
                continue
            seen_business.add(lead["business"].lower())
            n["eligible_with_email" if email else "eligible_no_email"] += 1
            if (email or include_no_email) and len(leads) < limit:
                leads.append(lead)
    return leads, n


def ensure_campaign(sb, cfg: dict) -> dict:
    """Reuse the campaign by name, else create it PAUSED (active=false is
    explicit — the campaigns table defaults active to true)."""
    rows = sb.select("campaigns", {"name": f"eq.{cfg['campaign']}"}, limit=1)
    if rows:
        camp = rows[0]
        if camp.get("active"):
            print(f"WARNING: campaign '{cfg['campaign']}' is ACTIVE — new leads "
                  "will enter the live sequencer. Pause it first if unintended.")
        return camp
    return sb.insert("campaigns", {
        "name": cfg["campaign"],
        "niche": cfg["niche"],
        "region": "US",
        "active": False,
        "daily_scrape_target": 0,
        "notes": cfg["notes"],
    })[0]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Import US licensee registries as leads (dry-run by default).")
    ap.add_argument("--source", required=True, choices=sorted(SOURCES),
                    help="which registry file to import")
    ap.add_argument("--csv", default=None,
                    help="path to the downloaded registry CSV (defaults per source)")
    ap.add_argument("--limit", type=int, default=500,
                    help="max leads to import per run (default 500)")
    ap.add_argument("--brokers-only", action="store_true",
                    help="only brokers (the decision-makers); skip sales agents")
    ap.add_argument("--include-no-email", action="store_true",
                    help="also import rows without an email as enrichment_status="
                         "'pending' (default: skip them to avoid OpenAI spend)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write to Supabase (default is dry-run)")
    args = ap.parse_args()
    if args.limit <= 0:
        sys.exit("--limit must be a positive integer")
    cfg = SOURCES[args.source]
    path = args.csv or cfg["csv"]
    if not os.path.isfile(path):
        sys.exit(f"CSV not found: {path} — download it first (URLs in this "
                 "script's docstring) into data/registry/.")
    require_env()

    leads, n = parse_file(path, cfg, args.limit, args.brokers_only,
                          args.include_no_email)
    eligible = n["eligible_with_email"] + n["eligible_no_email"]
    mode = "APPLY" if args.apply else "DRY RUN (no writes — pass --apply to import)"
    print(f"{args.source} import — {mode}")
    print(f"  csv                       {path}")
    print(f"  campaign                  {cfg['campaign']} (niche={cfg['niche']}, region=US)")
    print(f"  rows parsed               {n['parsed']}")
    print(f"  skipped: not active       {n['skip_status']}")
    if n["skip_trade"]:
        print(f"  skipped: other trade      {n['skip_trade']}")
    print(f"  skipped: not a person     {n['skip_not_person']}")
    print(f"  skipped: brokers-only     {n['skip_brokers_only']}")
    print(f"  skipped: dup email        {n['skip_dup_email']}")
    print(f"  skipped: dup business     {n['skip_dup_business']}")
    print(f"  eligible after dedupe     {eligible}")
    print(f"    with email (enriched)   {n['eligible_with_email']}")
    print(f"    no email (pending)      {n['eligible_no_email']}"
          f"{' — imported (--include-no-email)' if args.include_no_email else ' — SKIPPED (pass --include-no-email to import)'}")
    print(f"  limit this run            {args.limit}")

    if not args.apply:
        print(f"  would insert              {len(leads)}")
        print(f"Campaign '{cfg['campaign']}' would be created/reused with active=false. "
              "Nothing sends until the owner activates it.")
        return

    from lib import supabase as sb  # after env check — module reads env on import
    camp = ensure_campaign(sb, cfg)
    for lead in leads:
        lead["campaign_id"] = camp["id"]
    inserted = 0
    for i in range(0, len(leads), INSERT_CHUNK):
        chunk = leads[i:i + INSERT_CHUNK]
        sb.insert("leads", chunk, on_conflict="campaign_id,business")
        inserted += len(chunk)
        print(f"  upserted {inserted}/{len(leads)}")
    print(f"  inserted (upserted)       {inserted}")
    print(f"Campaign '{cfg['campaign']}' (id={camp['id']}) is "
          f"{'ACTIVE' if camp.get('active') else 'PAUSED'} — it was not activated by this "
          "script; activate it from the dashboard when ready.")


if __name__ == "__main__":
    main()
