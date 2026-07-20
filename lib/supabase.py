"""Thin Supabase REST client. Service-role key only — server-side use.

Vercel functions are short-lived, so a tiny requests wrapper beats pulling in
the full supabase-py SDK and its httpx dependency.
"""
import os
from typing import Any

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

_BASE = f"{SUPABASE_URL}/rest/v1"
_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


# PostgREST silently caps every response at db-max-rows (default 1000). Any
# read that may exceed one page MUST paginate or it returns a truncated result
# with no error (C1 — the worst bug class in this repo: truncated suppression
# or dedup sets caused missed suppressions and resurrected sequences).
_PAGE = 1000


def select(table: str, params: dict | None = None, *, limit: int | None = None) -> list[dict]:
    p = dict(params or {})
    if limit is not None and limit <= _PAGE:
        # Single page — same request shape as before.
        p["limit"] = str(limit)
        r = requests.get(f"{_BASE}/{table}", headers=_HEADERS, params=p, timeout=15)
        r.raise_for_status()
        return r.json()
    # Multi-page read (limit > one page, or "give me everything"). A stable
    # order keeps pages disjoint while we walk the offset.
    p.setdefault("order", "id.asc")
    out: list[dict] = []
    offset = 0
    while True:
        want = _PAGE if limit is None else min(_PAGE, limit - len(out))
        if want <= 0:
            break
        headers = {**_HEADERS, "Range-Unit": "items", "Range": f"{offset}-{offset + want - 1}"}
        r = requests.get(f"{_BASE}/{table}", headers=headers, params=p, timeout=15)
        r.raise_for_status()
        page = r.json()
        out.extend(page)
        if len(page) < want:
            break
        offset += len(page)
    return out


def count(table: str, params: dict | None = None) -> int:
    """Exact row count via a head-style request — no row transfer, no 1000-row cap."""
    headers = {**_HEADERS, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"}
    r = requests.get(f"{_BASE}/{table}", headers=headers, params=params or {}, timeout=15)
    r.raise_for_status()
    total = r.headers.get("Content-Range", "").split("/")[-1]
    return int(total) if total.isdigit() else 0


def insert(table: str, row: dict | list[dict], *, on_conflict: str | None = None,
           ignore_duplicates: bool = False) -> list[dict]:
    headers = {**_HEADERS, "Prefer": "return=representation"}
    params = {"on_conflict": on_conflict} if on_conflict else None
    if on_conflict:
        # ignore-duplicates = insert-only: existing rows are NEVER modified and
        # the response contains only the rows actually inserted (true-insert
        # count). merge-duplicates = legacy upsert that overwrites.
        res = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
        headers["Prefer"] = f"return=representation,resolution={res}"
    r = requests.post(f"{_BASE}/{table}", headers=headers, params=params, json=row, timeout=15)
    r.raise_for_status()
    return r.json()


def update(table: str, match: dict, patch: dict) -> list[dict]:
    headers = {**_HEADERS, "Prefer": "return=representation"}
    params = {k: f"eq.{v}" for k, v in match.items()}
    r = requests.patch(f"{_BASE}/{table}", headers=headers, params=params, json=patch, timeout=15)
    r.raise_for_status()
    return r.json()


def rpc(fn: str, args: dict | None = None) -> Any:
    r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=_HEADERS, json=args or {}, timeout=15)
    r.raise_for_status()
    return r.json()
