import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "outreach.db"

SETTINGS_KEYS = (
    "close_rate", "avg_deal_value",
    "sender_name", "sender_title", "company_name",
    "website_url", "booking_url",
)

INITIAL_SETTINGS = {
    "sender_name":  "Khush Mutha",
    "sender_title": "Founder",
    "company_name": "Alter Flog AI",
    "website_url":  "",
    "booking_url":  "",
}


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        # Idempotent migrations for legacy installs that pre-date columns added later.
        try:
            c.execute("ALTER TABLE csv_sheets ADD COLUMN tab_title TEXT")
        except sqlite3.OperationalError:
            pass

        c.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outreach_log (
              id               INTEGER PRIMARY KEY AUTOINCREMENT,
              lead_email       TEXT NOT NULL,
              business_name    TEXT,
              csv_path         TEXT,
              subject          TEXT,
              body             TEXT,
              sent_at          TEXT NOT NULL,
              gmail_message_id TEXT,
              status           TEXT DEFAULT 'sent'
            );
            CREATE TABLE IF NOT EXISTS gmail_tokens (
              id          INTEGER PRIMARY KEY,
              token_json  TEXT NOT NULL,
              updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS csv_sheets (
              csv_name    TEXT PRIMARY KEY,
              sheet_id    TEXT NOT NULL,         -- master spreadsheet id
              sheet_url   TEXT NOT NULL,         -- deep link to the tab
              tab_title   TEXT,                  -- tab name inside the master
              updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_forecast (
              scope       TEXT PRIMARY KEY,
              json_blob   TEXT NOT NULL,
              computed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asana_tasks (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              csv_name      TEXT,
              business_name TEXT,
              task_gid      TEXT NOT NULL,
              task_url      TEXT,
              created_at    TEXT NOT NULL
            );
        """)
        # Clear any legacy seeded placeholder values.
        c.execute("DELETE FROM settings WHERE value IN ('0.02', '500', 'Khush', 'Best,\nKhush')")
        # Drop the obsolete free-form signature key (replaced by structured fields).
        c.execute("DELETE FROM settings WHERE key = 'email_signature'")
        for k in SETTINGS_KEYS:
            default = INITIAL_SETTINGS.get(k, "")
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, default))


def get_setting(key, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def get_settings():
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def save_token(token_dict):
    payload = json.dumps(token_dict)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute("DELETE FROM gmail_tokens WHERE id = 1")
        c.execute(
            "INSERT INTO gmail_tokens (id, token_json, updated_at) VALUES (1, ?, ?)",
            (payload, now),
        )


def load_token():
    with _conn() as c:
        row = c.execute("SELECT token_json FROM gmail_tokens WHERE id = 1").fetchone()
        return json.loads(row["token_json"]) if row else None


def clear_token():
    with _conn() as c:
        c.execute("DELETE FROM gmail_tokens WHERE id = 1")


def log_outreach(lead_email, business_name, csv_path, subject, body,
                 gmail_message_id=None, status="sent"):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO outreach_log "
            "(lead_email, business_name, csv_path, subject, body, sent_at, gmail_message_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_email, business_name, csv_path, subject, body, now, gmail_message_id, status),
        )


def list_outreach(limit=100):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM outreach_log ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_sheet_for_csv(csv_name):
    with _conn() as c:
        row = c.execute(
            "SELECT sheet_id, sheet_url, tab_title, updated_at FROM csv_sheets WHERE csv_name = ?",
            (csv_name,),
        ).fetchone()
        return dict(row) if row else None


def set_sheet_for_csv(csv_name, sheet_id, sheet_url, tab_title=None):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO csv_sheets (csv_name, sheet_id, sheet_url, tab_title, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(csv_name) DO UPDATE SET "
            "  sheet_id = excluded.sheet_id, "
            "  sheet_url = excluded.sheet_url, "
            "  tab_title = excluded.tab_title, "
            "  updated_at = excluded.updated_at",
            (csv_name, sheet_id, sheet_url, tab_title, now),
        )


def list_sheets():
    with _conn() as c:
        rows = c.execute("SELECT csv_name, sheet_id, sheet_url, updated_at FROM csv_sheets").fetchall()
        return {r["csv_name"]: dict(r) for r in rows}


def save_forecast(scope, payload):
    now = datetime.now(timezone.utc).isoformat()
    blob = json.dumps(payload)
    with _conn() as c:
        c.execute(
            "INSERT INTO ai_forecast (scope, json_blob, computed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET json_blob = excluded.json_blob, computed_at = excluded.computed_at",
            (scope, blob, now),
        )


def load_forecast(scope):
    with _conn() as c:
        row = c.execute(
            "SELECT json_blob, computed_at FROM ai_forecast WHERE scope = ?",
            (scope,),
        ).fetchone()
        if not row:
            return None
        return {"forecast": json.loads(row["json_blob"]), "computed_at": row["computed_at"]}


def log_asana_task(csv_name, business_name, task_gid, task_url):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO asana_tasks (csv_name, business_name, task_gid, task_url, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (csv_name or "", business_name or "", task_gid, task_url or "", now),
        )


def list_asana_tasks_for_csv(csv_name):
    """Return {business_name: task_url} for tasks linked to this csv."""
    with _conn() as c:
        rows = c.execute(
            "SELECT business_name, task_url FROM asana_tasks WHERE csv_name = ?",
            (csv_name,),
        ).fetchall()
        return {r["business_name"]: r["task_url"] for r in rows if r["business_name"]}


def outreach_stats():
    with _conn() as c:
        sent = c.execute(
            "SELECT COUNT(*) AS n FROM outreach_log WHERE status = 'sent'"
        ).fetchone()["n"]
        failed = c.execute(
            "SELECT COUNT(*) AS n FROM outreach_log WHERE status = 'failed'"
        ).fetchone()["n"]
        replied = c.execute(
            "SELECT COUNT(*) AS n FROM outreach_log WHERE status = 'replied'"
        ).fetchone()["n"]
        contacted_emails = c.execute(
            "SELECT DISTINCT lead_email FROM outreach_log WHERE status IN ('sent','replied')"
        ).fetchall()
        return {
            "sent": sent,
            "failed": failed,
            "replied": replied,
            "contacted_emails": {r["lead_email"] for r in contacted_emails},
        }
