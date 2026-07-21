"""One-off: export legacy SQLite suppressions into Supabase (plan.md Phase 1.7).

Reads data/outreach.db email_suppressions (READ-ONLY) and inserts into the
Supabase `suppressions` table. Insert-ignore on email: an address already
suppressed in Supabase keeps its existing row. Bounce diagnostics ride along in
the reason string so nothing is lost before Phase 4 ports them properly.

Usage:  python3 scripts/export_legacy_suppressions.py
Requires SUPABASE_URL + SUPABASE_SERVICE_KEY in the environment (.env is loaded
if python-dotenv is installed).
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from lib import supabase as sb  # noqa: E402  (needs env set first)

DB = PROJECT_ROOT / "data" / "outreach.db"


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select email, reason, bounce_type, bounce_subtype, diagnostic, first_at"
        " from email_suppressions"
    ).fetchall()
    con.close()

    payload = []
    for r in rows:
        email = (r["email"] or "").strip().lower()
        if not email or "@" not in email:
            continue
        reason = r["reason"] or "legacy"
        diag = "/".join(x for x in (r["bounce_type"], r["bounce_subtype"]) if x)
        if diag:
            reason = f"{reason} ({diag})"
        payload.append({"email": email, "reason": reason[:200], "source": "legacy-sqlite"})

    if not payload:
        print("nothing to export")
        return 0

    inserted = sb.insert("suppressions", payload, on_conflict="email", ignore_duplicates=True)
    print(f"legacy rows: {len(rows)}  exported: {len(payload)}  newly inserted: {len(inserted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
