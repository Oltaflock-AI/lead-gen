#!/usr/bin/env python3
"""Crawl the full active-tender list from eprocure.gov.in (CPPP / GePNIC).

Strategy: the public "Tenders by Organisation" page lists every organisation
with a tender count and a session-bound $DirectLink. Each DirectLink returns
that org's FULL active-tender table (id="table") on a single page (no
pagination). We walk all orgs and aggregate into one CSV.

Session note: the sp= tokens expire with the JSESSIONID (a few minutes idle).
We guard against mid-crawl timeouts by re-fetching the org page (which mints
fresh tokens) and resuming by org name.
"""
import csv
import re
import sys
import time
from html import unescape

import requests
from bs4 import BeautifulSoup

BASE = "https://eprocure.gov.in"
ORG_PAGE = BASE + "/eprocure/app?page=FrontEndTendersByOrganisation&service=page"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
OUT = sys.argv[1] if len(sys.argv) > 1 else "data/outputs/eprocure_active_tenders.csv"
DELAY = 0.4


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    return s


def fetch_orgs(s):
    """Return list of (org_name, count, directlink_href) from the org page."""
    html = s.get(ORG_PAGE, timeout=40).text
    soup = BeautifulSoup(html, "lxml")
    orgs = []
    for a in soup.find_all("a", href=lambda h: h and "DirectLink" in h):
        tr = a.find_parent("tr")
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name = tds[1].get_text(strip=True)
        cnt = a.get_text(strip=True)
        orgs.append((name, int(cnt) if cnt.isdigit() else 0, unescape(a["href"])))
    return orgs


def parse_col4(td):
    """col4 = <a>[Title]</a> [RefNo][TenderID]  ->  (title, ref_no, tender_id, url)."""
    a = td.find("a", href=True)
    url = BASE + unescape(a["href"]) if a else ""
    title = a.get_text(strip=True).strip("[]") if a else ""
    # remaining text after the anchor holds [RefNo][TenderID]
    tail = td.get_text(" ", strip=True)
    groups = re.findall(r"\[([^\[\]]+)\]", tail)
    # drop the title group if it reappears, keep last two as ref + id
    ref_no = tender_id = ""
    if len(groups) >= 2:
        tender_id = groups[-1].strip()
        ref_no = groups[-2].strip()
    elif len(groups) == 1:
        ref_no = groups[0].strip()
    return title, ref_no, tender_id, url


def parse_org_tenders(html, org_name):
    soup = BeautifulSoup(html, "lxml")
    tbl = soup.find("table", id="table")
    if not tbl:
        return None  # signal: no table (timeout / error)
    rows = []
    for tr in tbl.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        title, ref_no, tender_id, url = parse_col4(tds[4])
        if not title:
            continue
        rows.append({
            "organisation": org_name,
            "title": title,
            "ref_no": ref_no,
            "tender_id": tender_id,
            "epublished_date": tds[1].get_text(" ", strip=True),
            "closing_date": tds[2].get_text(" ", strip=True),
            "opening_date": tds[3].get_text(" ", strip=True),
            "org_chain": tds[5].get_text(" ", strip=True),
            "detail_url": url,
        })
    return rows


def main():
    s = new_session()
    orgs = fetch_orgs(s)
    print(f"orgs: {len(orgs)}  expected tenders: {sum(c for _, c, _ in orgs)}")
    href_by_name = {n: h for n, c, h in orgs}

    all_rows = []
    for i, (name, cnt, href) in enumerate(orgs, 1):
        for attempt in (1, 2):
            r = s.get(BASE + href, timeout=40)
            rows = parse_org_tenders(r.text, name)
            if rows is not None:
                break
            # session likely timed out -> refresh tokens, retry once
            if attempt == 1 and "timed out" in r.text.lower():
                s = new_session()
                orgs2 = fetch_orgs(s)
                href_by_name = {n: h for n, c, h in orgs2}
                href = href_by_name.get(name, href)
                time.sleep(DELAY)
            else:
                rows = []
        all_rows.extend(rows)
        print(f"[{i:3}/{len(orgs)}] {name[:46]:46} got {len(rows):3} (exp {cnt})  total={len(all_rows)}")
        time.sleep(DELAY)

    fields = ["organisation", "title", "ref_no", "tender_id", "epublished_date",
              "closing_date", "opening_date", "org_chain", "detail_url"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nDONE: {len(all_rows)} tenders -> {OUT}")


if __name__ == "__main__":
    main()
