#!/usr/bin/env python3
"""
MahaRERA registered real-estate agent scraper.

Source: https://maharera.maharashtra.gov.in/agents-search-result  (list, Drupal)
        https://maharerait.maharashtra.gov.in  (detail SPA + REST API)

The public detail pages auto-login with a shipped read-only account
(@maharera_public_view) and call getAgentInformationByUserProfileId, which
returns the full PUBLIC record: name, email, mobile, office no, full address,
RERA registration number + validity dates. This script replicates that exact
public path:

  1. list page (filtered by state/district) -> agent/view/{id} ids
     (id == userProfileId)
  2. POST getAgentInformationByUserProfileId per id -> lead row -> CSV

Default scope: Mumbai City/Suburban + Pune + Thane, active registrations only.
"""
import csv
import os
import re
import sys
import time
import argparse
import datetime as dt
import requests

LIST_URL = "https://maharera.maharashtra.gov.in/agents-search-result"
API = ("https://maharerait.maharashtra.gov.in/api/"
       "maha-rera-agent-management-service/agent/getAgentInformationByUserProfileId")
LOGIN = ("https://maharerait.maharashtra.gov.in/api/"
         "maha-rera-login-service/login/loginWithPasswordApp")
# Credentials come from the environment — never hardcode (F01). The public-view
# account is quasi-public but committing it as a literal leaks it into git history.
PUB_USER = os.environ.get("MAHARERA_USER", "@maharera_public_view")
PUB_PASS = os.environ.get("MAHARERA_PASS")
MH_STATE = 27

DISTRICTS = {  # id: name  (Maharashtra metros)
    519: "Mumbai City",
    518: "Mumbai Suburban",
    521: "Pune",
    517: "Thane",
}

VIEW_RE = re.compile(r"maharerait\.maharashtra\.gov\.in/agent/view/(\d+)\b")
TOTAL_RE = re.compile(r'<option value="(\d+)">\d+</option>')

FIELDS = [
    "userProfileId", "agentName", "fatherName", "agentRegistrationNumber",
    "agentRegistrationStartDate", "agentRegistrationEndDate",
    "emailId", "mobileNo", "officeNumber", "websiteURL",
    "buildingName", "unitNumber", "streetName", "locality", "landmark",
    "villageName", "talukaName", "districtName", "stateName", "pincode",
]


def get_token(s):
    if not PUB_PASS:
        sys.exit("MAHARERA_PASS not set — export the public-view account password before running this scraper.")
    r = s.post(LOGIN, json={"userName": PUB_USER, "password": PUB_PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["responseObject"]["accessToken"]


def list_page(s, district, page):
    r = s.get(LIST_URL, params={"agent_state": MH_STATE,
                                "agent_district": district, "page": page}, timeout=60)
    r.raise_for_status()
    return r.text


def ids_from_html(html):
    seen, out = set(), []
    for m in VIEW_RE.finditer(html):
        i = int(m.group(1))
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def max_page(html):
    opts = [int(x) for x in TOTAL_RE.findall(html)]
    return max(opts) if opts else 0


def fetch_agent(s, token, upid):
    r = s.post(API, headers={"Authorization": f"Bearer {token}"},
               json={"userProfileId": upid}, timeout=30)
    if r.status_code == 401:
        return "EXPIRED"
    r.raise_for_status()
    return (r.json() or {}).get("responseObject")


def is_active(obj, today):
    end = obj.get("agentRegistrationEndDate")
    if not end:
        return False
    try:
        return dt.date.fromisoformat(end[:10]) >= today
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--districts", default="519,518,521,517",
                    help="comma list of district ids")
    ap.add_argument("--out", default="data/maharera_agents.csv")
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--all-status", action="store_true",
                    help="include expired/lapsed registrations")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="cap pages per district (0 = all)")
    args = ap.parse_args()

    today = dt.date.today()
    districts = [int(x) for x in args.districts.split(",") if x.strip()]
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (lead-gen maharera-scrape; contact admin@oltaflock.ai)"
    token = get_token(s)

    written = set()
    total = 0
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for d in districts:
            dname = DISTRICTS.get(d, str(d))
            first = list_page(s, d, 0)
            last = max_page(first)
            if args.max_pages:
                last = min(last, args.max_pages - 1)
            print(f"[{dname}] {last + 1} pages", file=sys.stderr)
            for page in range(0, last + 1):
                try:
                    html = first if page == 0 else list_page(s, d, page)
                except Exception as e:
                    print(f"[{dname} p{page}] list error: {e}", file=sys.stderr)
                    continue
                kept = 0
                for upid in ids_from_html(html):
                    if upid in written:
                        continue
                    try:
                        obj = fetch_agent(s, token, upid)
                        if obj == "EXPIRED":
                            token = get_token(s)
                            obj = fetch_agent(s, token, upid)
                        if not obj or not obj.get("agentName"):
                            continue
                        if not args.all_status and not is_active(obj, today):
                            continue
                        w.writerow(obj)
                        written.add(upid)
                        total += 1
                        kept += 1
                    except Exception as e:
                        print(f"[id {upid}] {e}", file=sys.stderr)
                    time.sleep(args.sleep)
                f.flush()
                if page % 10 == 0 or kept:
                    print(f"[{dname} p{page}/{last}] +{kept}  total={total}",
                          file=sys.stderr)
    print(f"DONE: {total} active agents -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
