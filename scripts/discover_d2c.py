#!/usr/bin/env python3
"""Discover D2C brands currently running Meta ads for a product keyword.

Standalone, operator-run, CSV-only. Never writes to Supabase — review the CSV,
then import the brands you want via the dashboard /upload flow.

Uses the public Meta Ad Library web endpoint via lib/meta_ads (UNOFFICIAL and
fragile — see the lib/meta_ads.py docstring). Keyword search IS supported by
that endpoint (same search the public website runs), but results are advertiser
pages matching the keyword's ads, capped politely; expect the tool to break
whenever Meta rotates the Ad Library build, and to fail soft to an empty list.

Usage:
    python3 scripts/discover_d2c.py "running shoes" --country US --max 50
    python3 scripts/discover_d2c.py "protein powder" --country AU --max 25 \
        --out data/outputs/protein_au.csv
"""
import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import meta_ads  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "outputs")
FIELDS = ["brand_name", "page_id", "page_url", "ad_count", "country",
          "keyword", "discovered_at"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "keyword"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keyword", help="Product keyword, e.g. 'running shoes'")
    ap.add_argument("--country", default="US", help="2-letter country (default US)")
    ap.add_argument("--max", dest="max_n", type=int, default=50,
                    help="Max advertisers to collect (default 50)")
    ap.add_argument("--out", default=None,
                    help="CSV path (default data/outputs/d2c_<kw>_<cc>_<date>.csv)")
    args = ap.parse_args()

    if args.max_n < 1:
        print("--max must be >= 1"); return 2
    country = args.country.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        print(f"--country must be a 2-letter code, got {args.country!r}"); return 2

    # ~30 results/page; a couple of extra pages covers page-level aggregation.
    pages = min(10, (args.max_n // 30) + 2)
    print(f"Searching Meta Ad Library: {args.keyword!r} in {country} "
          f"(max {args.max_n} advertisers, <= {pages} pages, polite pacing)...")
    rows = meta_ads.search_advertisers(args.keyword, country,
                                       max_advertisers=args.max_n, max_pages=pages)
    if not rows:
        print("No advertisers returned. Either no active ads match, or the "
              "unofficial Ad Library web endpoint has changed (it is fragile; "
              "see lib/meta_ads.py docstring for how to refresh the doc ids).")
        return 1

    out_path = args.out or os.path.join(
        OUT_DIR, f"d2c_{_slug(args.keyword)}_{country.lower()}_"
                 f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for a in rows:
            pid = a.get("page_id") or ""
            w.writerow({
                "brand_name": a.get("page_name") or "",
                "page_id": pid,
                "page_url": f"https://www.facebook.com/{pid}" if pid else "",
                "ad_count": a.get("ad_count") or 0,
                "country": country,
                "keyword": args.keyword,
                "discovered_at": now,
            })
    print(f"Wrote {len(rows)} advertisers -> {out_path}")
    print("Top advertisers by active-ad volume:")
    for a in rows[:10]:
        print(f"  {a.get('ad_count', 0):>4}  {a.get('page_name')}")
    print("Review the CSV, then import chosen brands via the dashboard /upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
