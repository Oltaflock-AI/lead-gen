"""One-shot import: leads_master → new campaigns + leads tables.

Strategy:
  1. For each unique (niche, country) in leads_master, upsert a campaign.
  2. For each lead row, upsert into leads with enrichment_status='enriched'
     (already enriched in prior pipeline). Preserve historical signals in
     signals jsonb so the sequencer can use them.

Idempotent: leads have UNIQUE(campaign_id, business) and campaigns have
UNIQUE(name). Re-runs are safe.

Usage:
  python3 scripts/import_leads_master.py [--dry-run] [--limit N]
"""
import argparse
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from lib import supabase as sb


def slugify(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def campaign_name(niche: str, country: str) -> str:
    return f"{slugify(niche)}-{slugify(country)}"


def fetch_all(table: str, params: dict, chunk: int = 1000) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        p = {**params, "limit": str(chunk), "offset": str(offset)}
        rows = sb.select(table, p)
        out.extend(rows)
        if len(rows) < chunk:
            break
        offset += chunk
    return out


def ensure_campaigns(masters: list[dict]) -> dict[tuple[str, str], int]:
    """Create one campaign per unique (niche, country). Returns lookup map."""
    pairs: set[tuple[str, str]] = set()
    for r in masters:
        n = (r.get("niche") or "general").strip() or "general"
        c = (r.get("country") or "Unknown").strip() or "Unknown"
        pairs.add((n, c))

    rows = [{
        "name": campaign_name(n, c),
        "niche": n,
        "region": c,
        "active": False,  # imported = paused by default; user activates manually
        "daily_scrape_target": 50,
        "notes": "Auto-imported from leads_master",
    } for n, c in sorted(pairs)]

    inserted = sb.insert("campaigns", rows, on_conflict="name")
    # Build name → id map
    by_name = {c["name"]: c["id"] for c in inserted}
    return {(n, c): by_name[campaign_name(n, c)] for (n, c) in pairs}


def transform_lead(r: dict, campaign_id: int) -> dict | None:
    business = (r.get("business_name") or "").strip()
    if not business:
        return None

    signals = {k: r[k] for k in (
        "business_type", "place_id", "rating", "review_count",
        "intent_signal", "ai_lead_score", "ai_niche_fit", "ai_score_reason",
        "last_sent_at", "last_opened_at", "last_clicked_at", "last_subject",
        "replied_at", "first_seen_at", "outreach_status",
        "source_csv", "tags", "opening_hours_summary",
    ) if r.get(k) is not None}

    email = (r.get("email") or "").strip() or None
    verified = r.get("email_verified")
    if email and verified is True:
        email_status = "valid"
    elif email and verified is False:
        email_status = "invalid"
    elif email:
        email_status = "unknown"
    else:
        email_status = None

    return {
        "campaign_id": campaign_id,
        "business": business,
        "website": (r.get("website") or "").strip() or None,
        "email": email,
        "email_status": email_status,
        "phone": (r.get("phone") or "").strip() or None,
        "address": (r.get("address") or "").strip() or None,
        "city": (r.get("city") or "").strip() or None,
        "country": (r.get("country") or "").strip() or None,
        "source": "leads_master_import",
        "signals": signals or None,
        "intent_score": r.get("intent_score"),
        "enrichment_status": "enriched",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    print("Fetching leads_master…")
    params = {"select": "*", "order": "first_seen_at.asc"}
    if args.limit:
        params["limit"] = str(args.limit)
        masters = sb.select("leads_master", params)
    else:
        masters = fetch_all("leads_master", params)
    print(f"  → {len(masters)} rows")

    print("\nUpserting campaigns…")
    cmap = ensure_campaigns(masters)
    print(f"  → {len(cmap)} campaigns")
    for (n, c), cid in sorted(cmap.items()):
        print(f"    [{cid}] {campaign_name(n, c)}  ({n} / {c})")

    print("\nBuilding lead rows…")
    rows: list[dict] = []
    skipped = 0
    for r in masters:
        n = (r.get("niche") or "general").strip() or "general"
        c = (r.get("country") or "Unknown").strip() or "Unknown"
        cid = cmap[(n, c)]
        lead = transform_lead(r, cid)
        if lead:
            rows.append(lead)
        else:
            skipped += 1
    print(f"  → {len(rows)} importable, {skipped} skipped (missing business name)")

    if args.dry_run:
        print("\n[dry-run] not writing.")
        return

    print("\nUpserting leads (batches of 500)…")
    total = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        out = sb.insert("leads", batch, on_conflict="campaign_id,business")
        total += len(out)
        print(f"  batch {i // 500 + 1}: +{len(out)}  (cumulative {total})")

    by_camp = defaultdict(int)
    for r in rows:
        by_camp[r["campaign_id"]] += 1

    print(f"\n✓ Imported {total} leads across {len(by_camp)} campaigns.")
    print("  Campaigns are PAUSED by default — activate from the dashboard to start outreach.")


if __name__ == "__main__":
    main()
