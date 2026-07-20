#!/usr/bin/env python3
"""
Enrich remaining obfuscated emails in travel_agencies_75_leads.csv
using Prospeo API. Run this after rate limit resets (likely hourly).

Usage: python3 scripts/enrich_remaining.py

Requires PROSPEO_API_KEY env var.
"""
import csv, json, os, time, urllib.request

API_KEY = os.environ.get("PROSPEO_API_KEY", "")
CSV_PATH = "data/outputs/travel_agencies_75_leads.csv"
BASE_URL = "https://api.prospeo.io/v1"

# person_id -> company_domain mapping for remaining 28
REMAINING = {
    "aaaa8f7bc69fb9f57491e321": "savaana.travel",
    "aaaa908d025fef5fc5388266": "tourdeguide.com",
    "aaaafda2b707edb5466e81e4": "bharatselfdrive.com",
    "aaaa4635c6ef16c63b7c6014": "travnet.co.in",
    "aaaa5e9255949a7e2ddc8eab": "touchdownindia.com",
    "aaaab5324612b422ace58462": "rentalogue.in",
    "aaaabbf6b81c42ec408eb0a0": "aragotravels.com",
    "aaaacd4488059f8b125eac1a": "3stravelnetwork.com",
    "aaaa83d4c09d6c2993c00e6f": "trekatribe.com",
    "aaaa6e61b493cb1f758482ba": "provoy.in",
    "aaaaacc9ea9a9fd9e49a8bcb": "reddotreps.com",
    "aaaa479471135beb910baa0f": "rajjastours.com",
    "aaaaf1d2e6408e8a930319cd": "indiaonroaming.com",
    "aaaa40b2e4cf386bbb6af3d6": "allegraprivatetours.com",
    "aaaac35ced86b15d0ab0c9d8": "airssist.com",
    "aaaa17f964c3de04a4180ce3": "dubz.com",
    "aaaabeefcafe88f1a0af73f5": "whitebirdclub.com",
    "aaaa342d095a83df0c67312d": "elitetravel.ae",
    "aaaaf2e3c2368d92ded42c1a": "travelwings.ae",
    "aaaa4b90a7744b5c4ac0d768": "zaitoncorp.com",
    "aaaa4034db3b9a18dc0ac6e6": "seasafari.ae",
    "aaaa691f377bc442de5d6368": "theconciergebox.com",
    "aaaa2de2c43c91eafaab0cf4": "avtourism.com",
    "aaaa65394c39dab7142fa1fd": "eaglecrestdmc.com",
    "aaaa93c5ef2f78eaa781db47": "skysham.com",
    "aaaa9a9540f7805091988528": "tominigroup.com",
    "aaaa0baa320331cfe3689258": "unitedworld.ae",
    "aaaa11aa5ead732d1ff41247": "skyseekers.ae",
}


def enrich_person(person_id):
    url = f"{BASE_URL}/person/enrich"
    payload = json.dumps({
        "person_id": person_id,
        "only_verified_email": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            if data.get("success") and data["data"].get("found"):
                email = data["data"]["person"]["email"].get("email")
                if email:
                    return email
    except Exception as e:
        print(f"  Error: {e}")
    return None


def main():
    if not API_KEY:
        print("Set PROSPEO_API_KEY env var first")
        return

    # Load CSV
    with open(CSV_PATH) as f:
        rows = list(csv.DictReader(f))

    # Build domain -> row index map
    domain_idx = {}
    for i, row in enumerate(rows):
        domain_idx[row["company_domain"]] = i

    revealed = 0
    failed = 0
    for pid, domain in REMAINING.items():
        idx = domain_idx.get(domain)
        if idx is None:
            continue
        if "*" not in rows[idx]["email"]:
            print(f"  {domain}: already revealed, skip")
            continue

        print(f"  Enriching {domain}...", end=" ")
        email = enrich_person(pid)
        if email:
            rows[idx]["email"] = email
            rows[idx]["email_revealed"] = "yes"
            revealed += 1
            print(f"-> {email}")
        else:
            failed += 1
            print("-> FAILED (rate limit or no match)")

        time.sleep(2)  # 2s gap between calls to respect rate limit

    # Write back
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    total_revealed = sum(1 for r in rows if r["email_revealed"] == "yes")
    print(f"\nDone. Revealed {revealed} new emails. Total: {total_revealed}/75. Failed: {failed}")


if __name__ == "__main__":
    main()
