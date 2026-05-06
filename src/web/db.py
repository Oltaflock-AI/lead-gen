import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "outreach.db"

SETTINGS_KEYS = (
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
            CREATE TABLE IF NOT EXISTS niche_offers (
              niche       TEXT PRIMARY KEY,
              offer       TEXT NOT NULL,
              tone        TEXT,
              loom_url    TEXT,
              updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sequences (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              lead_email      TEXT NOT NULL UNIQUE,
              business_name   TEXT,
              niche           TEXT,
              csv_name        TEXT,
              city            TEXT,
              status          TEXT NOT NULL DEFAULT 'active',
              current_step    INTEGER NOT NULL DEFAULT 0,
              next_send_at    TEXT,
              paused_reason   TEXT,
              created_at      TEXT NOT NULL,
              updated_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sequences_status_due
              ON sequences(status, next_send_at);
            CREATE TABLE IF NOT EXISTS sequence_messages (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              sequence_id     INTEGER NOT NULL,
              step            INTEGER NOT NULL,
              subject         TEXT,
              body            TEXT,
              scheduled_for   TEXT,
              sent_at         TEXT,
              resend_id       TEXT,
              status          TEXT NOT NULL DEFAULT 'pending',
              opens           INTEGER NOT NULL DEFAULT 0,
              clicks          INTEGER NOT NULL DEFAULT 0,
              bounced         INTEGER NOT NULL DEFAULT 0,
              replied         INTEGER NOT NULL DEFAULT 0,
              error           TEXT,
              UNIQUE(sequence_id, step)
            );
            CREATE INDEX IF NOT EXISTS idx_seqmsg_resend ON sequence_messages(resend_id);
            CREATE TABLE IF NOT EXISTS email_events (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              resend_id    TEXT,
              event        TEXT NOT NULL,
              payload_json TEXT,
              ts           TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_email_events_resend ON email_events(resend_id);
            CREATE TABLE IF NOT EXISTS scrape_history (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id              TEXT,             -- link to in-memory jobs tray
              kind                TEXT NOT NULL,    -- interactive_scrape | canonical_scrape
              label               TEXT,
              status              TEXT NOT NULL DEFAULT 'running',
              niche               TEXT,
              country             TEXT,
              state               TEXT,
              city                TEXT,
              business_types      TEXT,             -- JSON
              target_leads        INTEGER,
              min_reviews         INTEGER,
              min_rating          REAL,
              require_no_website  INTEGER,
              verify_emails       INTEGER,
              leads_count         INTEGER DEFAULT 0,
              csv_basename        TEXT,
              sheet_url           TEXT,
              events_json         TEXT,             -- snapshot of events on finish
              params_json         TEXT,             -- raw input payload
              error               TEXT,
              started_at          TEXT NOT NULL,
              finished_at         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scrape_history_status
              ON scrape_history(status, started_at DESC);
            CREATE TABLE IF NOT EXISTS outreach_drafts (
              csv_path       TEXT NOT NULL,
              lead_email     TEXT NOT NULL,
              business_name  TEXT,
              subject        TEXT NOT NULL,
              body           TEXT NOT NULL,
              engine         TEXT,
              updated_at     TEXT NOT NULL,
              PRIMARY KEY (csv_path, lead_email)
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_drafts_csv
              ON outreach_drafts(csv_path, updated_at DESC);
        """)
        # Idempotent column adds for niche_offers.
        try:
            c.execute("ALTER TABLE niche_offers ADD COLUMN brief_md TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent column adds for outreach_log analytics.
        for ddl in (
            "ALTER TABLE outreach_log ADD COLUMN resend_id TEXT",
            "ALTER TABLE outreach_log ADD COLUMN opens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN bounced INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN replied_at TEXT",
            "ALTER TABLE outreach_log ADD COLUMN last_event_at TEXT",
            "ALTER TABLE outreach_log ADD COLUMN last_event_type TEXT",
        ):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_outreach_log_resend "
            "ON outreach_log(resend_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_outreach_log_csv "
            "ON outreach_log(csv_path, sent_at DESC)"
        )
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
                 gmail_message_id=None, status="sent", resend_id=None):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO outreach_log "
            "(lead_email, business_name, csv_path, subject, body, sent_at, "
            " gmail_message_id, status, resend_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_email, business_name, csv_path, subject, body, now,
             gmail_message_id, status, resend_id),
        )


def list_outreach(limit=100, csv_path=None):
    with _conn() as c:
        if csv_path:
            rows = c.execute(
                "SELECT * FROM outreach_log WHERE csv_path = ? "
                "ORDER BY sent_at DESC LIMIT ?", (csv_path, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM outreach_log ORDER BY sent_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────── outreach analytics ───────────────────────────


# Resend event names → outreach_log update.
_OUTREACH_EVENT_MAP = {
    "email.sent":             ("status",      "sent"),
    "email.delivered":        ("status",      "delivered"),
    "email.opened":           ("counter",     "opens"),
    "email.clicked":          ("counter",     "clicks"),
    "email.bounced":          ("counter",     "bounced"),
    "email.complained":       ("counter",     "bounced"),
    "email.delivery_delayed": ("status",      "delayed"),
    "email.failed":           ("status",      "failed"),
}


def update_outreach_event(resend_id, event):
    """Roll a Resend webhook event onto the matching outreach_log row.
    Returns True when a row was updated."""
    if not resend_id or not event:
        return False
    spec = _OUTREACH_EVENT_MAP.get(event)
    if not spec:
        return False
    kind, value = spec
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        # Confirm the row exists (one-shot outreach only).
        row = c.execute(
            "SELECT id, status FROM outreach_log WHERE resend_id = ?",
            (resend_id,),
        ).fetchone()
        if not row:
            return False
        if kind == "counter":
            # Bump counter; promote status when it's still "sent".
            new_status_clause = ""
            params = [now, event, row["id"]]
            if value == "opens" and (row["status"] or "") == "sent":
                new_status_clause = ", status = 'opened'"
            elif value == "clicks" and (row["status"] or "") in ("sent", "opened"):
                new_status_clause = ", status = 'clicked'"
            elif value == "bounced":
                new_status_clause = ", status = 'bounced'"
            c.execute(
                f"UPDATE outreach_log SET {value} = {value} + 1, "
                f"last_event_at = ?, last_event_type = ?{new_status_clause} "
                "WHERE id = ?",
                params,
            )
        else:  # status update
            c.execute(
                "UPDATE outreach_log SET status = ?, "
                "last_event_at = ?, last_event_type = ? WHERE id = ?",
                (value, now, event, row["id"]),
            )
    return True


def mark_outreach_replied_by_email(lead_email):
    """Flip every outreach_log row from this address to status='replied'.
    Called by the periodic Gmail reply scanner. Returns number of rows."""
    if not lead_email:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    addr = lead_email.strip().lower()
    with _conn() as c:
        cur = c.execute(
            "UPDATE outreach_log SET status = 'replied', "
            "replied_at = COALESCE(replied_at, ?), "
            "last_event_at = ?, last_event_type = 'gmail.reply' "
            "WHERE lower(lead_email) = ? AND status NOT IN ('replied', 'bounced')",
            (now, now, addr),
        )
        return cur.rowcount


def outreach_campaign_summary(csv_path=None):
    """Aggregate counts for the campaign banner."""
    where = "WHERE csv_path = ?" if csv_path else ""
    args = (csv_path,) if csv_path else ()
    with _conn() as c:
        row = c.execute(
            f"SELECT "
            f"  COUNT(*)                                          AS sent, "
            f"  COALESCE(SUM(opens),0)                            AS opens, "
            f"  COALESCE(SUM(clicks),0)                           AS clicks, "
            f"  COALESCE(SUM(bounced),0)                          AS bounced, "
            f"  SUM(CASE WHEN status='replied'    THEN 1 ELSE 0 END) AS replied, "
            f"  SUM(CASE WHEN status='failed'     THEN 1 ELSE 0 END) AS failed, "
            f"  SUM(CASE WHEN opens > 0           THEN 1 ELSE 0 END) AS unique_opened, "
            f"  SUM(CASE WHEN clicks > 0          THEN 1 ELSE 0 END) AS unique_clicked "
            f"FROM outreach_log {where}",
            args,
        ).fetchone()
        d = dict(row) if row else {}
        sent = max(1, d.get("sent") or 0)  # avoid div-by-zero
        d["open_rate"]    = round(100 * (d.get("unique_opened") or 0) / sent, 1)
        d["click_rate"]   = round(100 * (d.get("unique_clicked") or 0) / sent, 1)
        d["reply_rate"]   = round(100 * (d.get("replied") or 0) / sent, 1)
        d["bounce_rate"]  = round(100 * (d.get("bounced") or 0) / sent, 1)
        return d


def outreach_status_by_email(csv_path):
    """Map { lead_email_lower: dict(status, opens, clicks, bounced, replied_at, ...) }
    so the outreach UI can paint a status chip per row."""
    out = {}
    if not csv_path:
        return out
    with _conn() as c:
        rows = c.execute(
            "SELECT id, lead_email, status, opens, clicks, bounced, "
            "       replied_at, sent_at, last_event_at, last_event_type "
            "FROM outreach_log WHERE csv_path = ? ORDER BY sent_at DESC",
            (csv_path,),
        ).fetchall()
    for r in rows:
        em = (r["lead_email"] or "").lower()
        if em and em not in out:  # most-recent send wins
            out[em] = dict(r)
    return out


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
        # Any successfully-delivered email is "contacted" — covers the new
        # status progression: sent → delivered → opened → clicked → replied.
        contacted_emails = c.execute(
            "SELECT DISTINCT lead_email FROM outreach_log "
            "WHERE status NOT IN ('failed', 'bounced')"
        ).fetchall()
        return {
            "sent": sent,
            "failed": failed,
            "replied": replied,
            "contacted_emails": {r["lead_email"] for r in contacted_emails},
        }


# ─────────────────────────── niche offers ───────────────────────────


def upsert_niche_offer(niche, offer, tone="", loom_url="", brief_md=""):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO niche_offers (niche, offer, tone, loom_url, brief_md, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(niche) DO UPDATE SET "
            "  offer = excluded.offer, tone = excluded.tone, "
            "  loom_url = excluded.loom_url, brief_md = excluded.brief_md, "
            "  updated_at = excluded.updated_at",
            (niche, offer, tone, loom_url, brief_md, now),
        )


def get_niche_offer(niche):
    with _conn() as c:
        row = c.execute(
            "SELECT niche, offer, tone, loom_url, brief_md, updated_at "
            "FROM niche_offers WHERE niche = ?",
            (niche,),
        ).fetchone()
        return dict(row) if row else None


def list_niche_offers():
    with _conn() as c:
        rows = c.execute(
            "SELECT niche, offer, tone, loom_url, brief_md, updated_at "
            "FROM niche_offers ORDER BY niche"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_niche_offer(niche):
    with _conn() as c:
        c.execute("DELETE FROM niche_offers WHERE niche = ?", (niche,))


# ─────────────────────────── sequences ───────────────────────────


def create_sequence(lead_email, business_name, niche, csv_name, city=""):
    """Create a sequence row. Returns id, or existing id if already present."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM sequences WHERE lead_email = ?", (lead_email,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = c.execute(
            "INSERT INTO sequences "
            "(lead_email, business_name, niche, csv_name, city, status, "
            " current_step, next_send_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)",
            (lead_email, business_name, niche, csv_name, city, now, now, now),
        )
        return cur.lastrowid


def upsert_sequence_message(sequence_id, step, subject, body, scheduled_for=None):
    with _conn() as c:
        c.execute(
            "INSERT INTO sequence_messages "
            "(sequence_id, step, subject, body, scheduled_for, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending') "
            "ON CONFLICT(sequence_id, step) DO UPDATE SET "
            "  subject = excluded.subject, body = excluded.body, "
            "  scheduled_for = excluded.scheduled_for",
            (sequence_id, step, subject, body, scheduled_for),
        )


def get_sequence(sid):
    with _conn() as c:
        row = c.execute("SELECT * FROM sequences WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None


def get_sequence_by_email(email):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequences WHERE lead_email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def list_sequences(status=None, limit=500):
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM sequences WHERE status = ? "
                "ORDER BY datetime(updated_at) DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM sequences ORDER BY datetime(updated_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def list_sequence_messages(sequence_id):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sequence_messages WHERE sequence_id = ? ORDER BY step",
            (sequence_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def due_sequences(now_iso, limit=50):
    """Active sequences whose next_send_at has elapsed."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sequences "
            "WHERE status = 'active' AND next_send_at IS NOT NULL "
            "  AND next_send_at <= ? "
            "ORDER BY next_send_at LIMIT ?",
            (now_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_sequence(sid, **fields):
    fields = {k: v for k, v in fields.items() if k in (
        "status", "current_step", "next_send_at", "paused_reason",
    )}
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = list(fields.values()) + [sid]
    with _conn() as c:
        c.execute(f"UPDATE sequences SET {sets} WHERE id = ?", args)


def mark_sequence_message_sent(message_id, resend_id):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "UPDATE sequence_messages SET status = 'sent', sent_at = ?, "
            "  resend_id = ? WHERE id = ?",
            (now, resend_id, message_id),
        )


def mark_sequence_message_failed(message_id, error):
    with _conn() as c:
        c.execute(
            "UPDATE sequence_messages SET status = 'failed', error = ? WHERE id = ?",
            (str(error), message_id),
        )


def get_pending_message(sequence_id, step):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequence_messages "
            "WHERE sequence_id = ? AND step = ? AND status = 'pending'",
            (sequence_id, step),
        ).fetchone()
        return dict(row) if row else None


def pause_sequence_for_email(email, reason):
    """Pause active sequence for a given lead email (used on reply)."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "UPDATE sequences SET status = 'paused', paused_reason = ?, "
            "  next_send_at = NULL, updated_at = ? "
            "WHERE lead_email = ? AND status = 'active'",
            (reason, now, email),
        )
        return cur.rowcount


# ─────────────────────────── email events / webhook ───────────────────────────


def log_email_event(resend_id, event, payload_json):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO email_events (resend_id, event, payload_json, ts) "
            "VALUES (?, ?, ?, ?)",
            (resend_id, event, payload_json, now),
        )


def increment_message_metric(resend_id, field, by=1):
    """field in {'opens','clicks','bounced','replied'}."""
    if field not in ("opens", "clicks", "bounced", "replied"):
        return 0
    with _conn() as c:
        cur = c.execute(
            f"UPDATE sequence_messages SET {field} = {field} + ? "
            "WHERE resend_id = ?",
            (by, resend_id),
        )
        return cur.rowcount


def get_message_by_resend_id(resend_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequence_messages WHERE resend_id = ?",
            (resend_id,),
        ).fetchone()
        return dict(row) if row else None


def sequence_stats():
    with _conn() as c:
        active = c.execute("SELECT COUNT(*) AS n FROM sequences WHERE status='active'").fetchone()["n"]
        paused = c.execute("SELECT COUNT(*) AS n FROM sequences WHERE status='paused'").fetchone()["n"]
        done = c.execute("SELECT COUNT(*) AS n FROM sequences WHERE status='done'").fetchone()["n"]
        replies = c.execute(
            "SELECT COUNT(*) AS n FROM sequences WHERE paused_reason='replied'"
        ).fetchone()["n"]
        sent = c.execute(
            "SELECT COUNT(*) AS n FROM sequence_messages WHERE status='sent'"
        ).fetchone()["n"]
        opens = c.execute("SELECT COALESCE(SUM(opens),0) AS n FROM sequence_messages").fetchone()["n"]
        clicks = c.execute("SELECT COALESCE(SUM(clicks),0) AS n FROM sequence_messages").fetchone()["n"]
        return {
            "active": active, "paused": paused, "done": done,
            "replies": replies, "messages_sent": sent,
            "opens": opens, "clicks": clicks,
        }


# ─────────────────────────── scrape history ───────────────────────────


def record_scrape_start(job_id, kind, label, params):
    """Insert a 'running' row at scrape kickoff. Returns scrape_history id."""
    now = datetime.now(timezone.utc).isoformat()
    p = params or {}
    bt = p.get("business_types")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO scrape_history "
            "(job_id, kind, label, status, niche, country, state, city, "
            " business_types, target_leads, min_reviews, min_rating, "
            " require_no_website, verify_emails, params_json, started_at) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, kind, label,
                p.get("niche"), p.get("country"), p.get("state"), p.get("city"),
                json.dumps(bt) if bt is not None else None,
                p.get("target_leads"),
                p.get("min_reviews"), p.get("min_rating"),
                1 if p.get("require_no_website") else 0,
                1 if p.get("verify_emails") else 0,
                json.dumps(p)[:8000],
                now,
            ),
        )
        return cur.lastrowid


def record_scrape_finish(scrape_id, status, *, leads_count=None,
                         csv_basename=None, sheet_url=None,
                         events=None, error=None):
    """Update a scrape row when the worker terminates."""
    now = datetime.now(timezone.utc).isoformat()
    fields = {"status": status, "finished_at": now}
    if leads_count is not None:
        fields["leads_count"] = leads_count
    if csv_basename is not None:
        fields["csv_basename"] = csv_basename
    if sheet_url is not None:
        fields["sheet_url"] = sheet_url
    if events is not None:
        # Trim oversized events lists so DB doesn't bloat. Keep last ~5000.
        ev = events[-5000:] if isinstance(events, list) else events
        fields["events_json"] = json.dumps(ev)[:2_000_000]
    if error is not None:
        fields["error"] = str(error)
    sets = ", ".join(f"{k} = ?" for k in fields)
    args = list(fields.values()) + [scrape_id]
    with _conn() as c:
        c.execute(f"UPDATE scrape_history SET {sets} WHERE id = ?", args)


def list_scrapes(limit=200, status=None):
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT id, job_id, kind, label, status, niche, country, state, "
                "city, leads_count, csv_basename, sheet_url, started_at, finished_at, error "
                "FROM scrape_history WHERE status = ? "
                "ORDER BY datetime(started_at) DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, job_id, kind, label, status, niche, country, state, "
                "city, leads_count, csv_basename, sheet_url, started_at, finished_at, error "
                "FROM scrape_history "
                "ORDER BY datetime(started_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_scrape(scrape_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM scrape_history WHERE id = ?", (scrape_id,)
        ).fetchone()
        return dict(row) if row else None


def get_scrape_by_job_id(job_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM scrape_history WHERE job_id = ? "
            "ORDER BY id DESC LIMIT 1", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def mark_orphan_scrapes_interrupted():
    """Called on app startup: any scrape still 'running' in DB cannot resume
    after a Flask restart — flag it so user sees it in history."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "UPDATE scrape_history "
            "SET status = 'interrupted', finished_at = ?, "
            "    error = COALESCE(error, 'Flask restarted while job was running') "
            "WHERE status = 'running'",
            (now,),
        )
        return cur.rowcount


def scrape_stats():
    with _conn() as c:
        running = c.execute("SELECT COUNT(*) AS n FROM scrape_history WHERE status='running'").fetchone()["n"]
        done = c.execute("SELECT COUNT(*) AS n FROM scrape_history WHERE status='done'").fetchone()["n"]
        failed = c.execute("SELECT COUNT(*) AS n FROM scrape_history WHERE status IN ('failed','interrupted')").fetchone()["n"]
        total_leads = c.execute("SELECT COALESCE(SUM(leads_count),0) AS n FROM scrape_history").fetchone()["n"]
        return {"running": running, "done": done, "failed": failed,
                "total_leads": total_leads}


# ─────────────────────────── outreach drafts ───────────────────────────


def upsert_outreach_draft(csv_path, lead_email, subject, body,
                          business_name="", engine=""):
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO outreach_drafts "
            "(csv_path, lead_email, business_name, subject, body, engine, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(csv_path, lead_email) DO UPDATE SET "
            "  business_name = excluded.business_name, "
            "  subject = excluded.subject, "
            "  body = excluded.body, "
            "  engine = excluded.engine, "
            "  updated_at = excluded.updated_at",
            (csv_path, (lead_email or "").lower(), business_name,
             subject, body, engine, now),
        )


def get_outreach_drafts(csv_path):
    with _conn() as c:
        rows = c.execute(
            "SELECT lead_email, business_name, subject, body, engine, updated_at "
            "FROM outreach_drafts WHERE csv_path = ?",
            (csv_path,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_outreach_draft(csv_path, lead_email):
    with _conn() as c:
        row = c.execute(
            "SELECT lead_email, business_name, subject, body, engine, updated_at "
            "FROM outreach_drafts WHERE csv_path = ? AND lead_email = ?",
            (csv_path, (lead_email or "").lower()),
        ).fetchone()
        return dict(row) if row else None


def delete_outreach_draft(csv_path, lead_email):
    with _conn() as c:
        c.execute(
            "DELETE FROM outreach_drafts WHERE csv_path = ? AND lead_email = ?",
            (csv_path, (lead_email or "").lower()),
        )
