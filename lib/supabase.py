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


def select(table: str, params: dict | None = None, *, limit: int | None = None) -> list[dict]:
    p = dict(params or {})
    if limit is not None:
        p["limit"] = str(limit)
    r = requests.get(f"{_BASE}/{table}", headers=_HEADERS, params=p, timeout=15)
    r.raise_for_status()
    return r.json()


def insert(table: str, row: dict | list[dict], *, on_conflict: str | None = None) -> list[dict]:
    headers = {**_HEADERS, "Prefer": "return=representation"}
    params = {"on_conflict": on_conflict} if on_conflict else None
    if on_conflict:
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"
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
