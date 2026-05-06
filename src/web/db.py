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
            CREATE TABLE IF NOT EXISTS email_suppressions (
              email           TEXT PRIMARY KEY,
              reason          TEXT NOT NULL,
              bounce_type     TEXT,
              bounce_subtype  TEXT,
              diagnostic      TEXT,
              first_at        TEXT NOT NULL,
              last_at         TEXT NOT NULL,
              count           INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_suppressions_last
              ON email_suppressions(last_at DESC);
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
        # Persist the original lead facts on the sequence so every step can
        # be redrafted at send time with the latest engagement context.
        try:
            c.execute("ALTER TABLE sequences ADD COLUMN lead_facts_json TEXT")
        except sqlite3.OperationalError:
            pass
        # Idempotent column adds for outreach_log analytics.
        for ddl in (
            "ALTER TABLE outreach_log ADD COLUMN resend_id TEXT",
            "ALTER TABLE outreach_log ADD COLUMN opens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN bounced INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE outreach_log ADD COLUMN replied_at TEXT",
            "ALTER TABLE outreach_log ADD COLUMN last_event_at TEXT",
            "ALTER TABLE outreach_log ADD COLUMN last_event_type TEXT",
            "ALTER TABLE outreach_log ADD COLUMN bounce_type TEXT",
            "ALTER TABLE outreach_log ADD COLUMN bounce_subtype TEXT",
            "ALTER TABLE outreach_log ADD COLUMN bounce_diagnostic TEXT",
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
        # Backfill resend_id from the legacy gmail_message_id="resend:<id>"
        # convention so replayed Resend events can match these rows.
        c.execute(
            "UPDATE outreach_log "
            "SET resend_id = SUBSTR(gmail_message_id, 8) "
            "WHERE (resend_id IS NULL OR resend_id = '') "
            "  AND gmail_message_id LIKE 'resend:%'"
        )
        # Idempotency for replayed webhook events: each Resend event payload
        # carries a `created_at`. Dedupe on (resend_id, event, created_at)
        # so replaying the same event in the Resend dashboard cannot
        # double-bump opens/clicks counters.
        try:
            c.execute("ALTER TABLE email_events ADD COLUMN created_at TEXT")
        except sqlite3.OperationalError:
            pass
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_events_unique "
            "ON email_events(resend_id, event, created_at) "
            "WHERE resend_id IS NOT NULL AND created_at IS NOT NULL"
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
                 gmail_message_id=None, status="delivered", resend_id=None):
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


def delete_failed_outreach(csv_path=None, lead_emails=None):
    """Remove failed outreach_log rows. Filters:
      - csv_path:    restrict to a single CSV (None = all CSVs)
      - lead_emails: list of addresses (None = every failed row in that CSV)

    Returns the count deleted. Used to clean up duplicates that pile up when
    a retry fails, and to make a single retry collapse to one row per lead.
    """
    sql = "DELETE FROM outreach_log WHERE status = 'failed'"
    args = []
    if csv_path:
        sql += " AND csv_path = ?"
        args.append(csv_path)
    if lead_emails:
        placeholders = ",".join("?" * len(lead_emails))
        sql += f" AND lower(lead_email) IN ({placeholders})"
        args.extend([(e or "").lower() for e in lead_emails])
    with _conn() as c:
        cur = c.execute(sql, args)
        return cur.rowcount


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
    # Treat 'sent' (Resend accepted) and 'delivered' (recipient MX accepted)
    # as the same milestone for UX. The distinction adds noise without an
    # actionable difference for cold outreach.
    "email.sent":             ("status",      "delivered"),
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
            # Bump counter; promote status when it's still pre-engagement.
            new_status_clause = ""
            params = [now, event, row["id"]]
            cur_status = row["status"] or ""
            if value == "opens" and cur_status in ("sent", "delivered"):
                new_status_clause = ", status = 'opened'"
            elif value == "clicks" and cur_status in ("sent", "delivered", "opened"):
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
            # `bounced` was previously SUM(bounced) — that column is a
            # webhook-event counter that increments on every replay, so a
            # single bounced row could push the rate over 100%. Use the
            # per-row status flag instead so bounce_rate stays in 0–100.
            f"  SUM(CASE WHEN status='bounced'    THEN 1 ELSE 0 END) AS bounced, "
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


def outreach_status_by_email(csv_path, days=None, since=None):
    """Map { lead_email_lower: dict(...) } per send.
    `since` (UTC ISO) preferred for calendar-day cutoffs; `days` is rolling."""
    out = {}
    if not csv_path:
        return out
    extra_where, params = "", [csv_path]
    cutoff = since
    if cutoff is None and days is not None and days > 0:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    if cutoff:
        extra_where = " AND sent_at >= ?"
        params.append(cutoff)
    with _conn() as c:
        rows = c.execute(
            "SELECT id, lead_email, status, opens, clicks, bounced, "
            "       replied_at, sent_at, last_event_at, last_event_type "
            f"FROM outreach_log WHERE csv_path = ?{extra_where} ORDER BY sent_at DESC",
            params,
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


def create_sequence(lead_email, business_name, niche, csv_name, city="",
                    lead_facts=None):
    """Create a sequence row. Returns id, or existing id if already present.
    `lead_facts` is a dict (rating, review_count, website, etc.) — stored as
    JSON so steps 2-7 can be redrafted at send time with full context."""
    now = datetime.now(timezone.utc).isoformat()
    facts_json = json.dumps(lead_facts) if lead_facts else None
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM sequences WHERE lead_email = ?", (lead_email,)
        ).fetchone()
        if existing:
            return existing["id"]
        cur = c.execute(
            "INSERT INTO sequences "
            "(lead_email, business_name, niche, csv_name, city, status, "
            " current_step, next_send_at, created_at, updated_at, lead_facts_json) "
            "VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?)",
            (lead_email, business_name, niche, csv_name, city, now, now, now,
             facts_json),
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


def get_active_sequence_by_email(email):
    """Return the currently-active sequence row for this email, or None.

    Used by the auto-enrol path on /api/outreach/send to skip leads that
    are already mid-drip (idempotent re-sends of the same campaign).
    """
    if not email:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequences "
            "WHERE lower(lead_email) = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row else None


def get_sequence_message(sequence_id, step):
    """Return the message row for one (sequence_id, step), or None.

    Unlike `get_pending_message`, this returns the row regardless of status
    (so callers can read the resend_id after a successful send).
    """
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequence_messages "
            "WHERE sequence_id = ? AND step = ?",
            (sequence_id, step),
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


def update_sequence_message(message_id, subject, body):
    """Overwrite a pending step's subject/body just before send so we can
    inject prior-engagement context into the redraft."""
    with _conn() as c:
        c.execute(
            "UPDATE sequence_messages SET subject = ?, body = ? WHERE id = ?",
            (subject, body, message_id),
        )


def list_sent_sequence_messages(sequence_id, before_step=None):
    """Return all already-sent steps on a sequence, with engagement counters,
    ordered by step. Pass `before_step=N` to limit to steps preceding N."""
    q = ("SELECT step, subject, sent_at, opens, clicks, replied "
         "FROM sequence_messages "
         "WHERE sequence_id = ? AND status = 'sent'")
    args = [sequence_id]
    if before_step is not None:
        q += " AND step < ?"
        args.append(before_step)
    q += " ORDER BY step"
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


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


def log_email_event(resend_id, event, payload_json, created_at=None):
    """Persist a webhook event row. Returns True when a NEW row was written,
    False when the (resend_id, event, created_at) tuple was already logged
    (i.e. the user replayed an event in the Resend dashboard) so callers can
    skip duplicate counter increments.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        try:
            c.execute(
                "INSERT INTO email_events (resend_id, event, payload_json, ts, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (resend_id, event, payload_json, now, created_at),
            )
            return True
        except sqlite3.IntegrityError:
            # idx_email_events_unique blocked a replay of the same event.
            return False


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


# ─────────────────────────── suppression list ───────────────────────────


def upsert_suppression(email, reason, bounce_type=None, bounce_subtype=None,
                       diagnostic=None):
    """Add or bump an entry on the suppression list. Idempotent.

    `reason` is the high-level cause: bounced | complained | manual.
    Resend's bounce object provides type/subType/message — pass through so
    operators can see WHY each address bounced (mailbox missing, mailbox
    full, ISP block, content complaint, etc.)."""
    if not email:
        return False
    addr = email.strip().lower()
    if not addr:
        return False
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO email_suppressions "
            "  (email, reason, bounce_type, bounce_subtype, diagnostic, "
            "   first_at, last_at, count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(email) DO UPDATE SET "
            "  reason         = excluded.reason, "
            "  bounce_type    = COALESCE(excluded.bounce_type,    bounce_type), "
            "  bounce_subtype = COALESCE(excluded.bounce_subtype, bounce_subtype), "
            "  diagnostic     = COALESCE(excluded.diagnostic,     diagnostic), "
            "  last_at        = excluded.last_at, "
            "  count          = count + 1",
            (addr, reason, bounce_type, bounce_subtype, diagnostic, now, now),
        )
    return True


def remove_suppression(email):
    """Manually clear an address from the suppression list."""
    if not email:
        return 0
    addr = email.strip().lower()
    with _conn() as c:
        cur = c.execute("DELETE FROM email_suppressions WHERE email = ?", (addr,))
        return cur.rowcount


def is_suppressed(email):
    if not email:
        return False
    addr = email.strip().lower()
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM email_suppressions WHERE email = ? LIMIT 1", (addr,),
        ).fetchone()
        return bool(row)


def filter_suppressed(emails):
    """Given an iterable of addresses, return set of those on the suppression
    list. Used by the outreach send route to drop known-bad addresses BEFORE
    we hit Resend's API (saves quota and keeps bounce rate low)."""
    addrs = sorted({(e or "").strip().lower() for e in emails if e})
    addrs = [a for a in addrs if a]
    if not addrs:
        return set()
    with _conn() as c:
        placeholders = ",".join("?" for _ in addrs)
        rows = c.execute(
            f"SELECT email FROM email_suppressions WHERE email IN ({placeholders})",
            addrs,
        ).fetchall()
    return {r["email"] for r in rows}


def list_suppressions(limit=200):
    with _conn() as c:
        rows = c.execute(
            "SELECT email, reason, bounce_type, bounce_subtype, diagnostic, "
            "       first_at, last_at, count "
            "FROM email_suppressions ORDER BY last_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def suppression_count():
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM email_suppressions").fetchone()
        return int(row["n"] or 0)


def bounce_breakdown(days=None, since=None, limit=10):
    """Top N bounce reasons over a window. Drives the dashboard
    'why are we bouncing' card. Falls back to bounce_subtype, then
    bounce_type, then 'unknown' so every bounce is accounted for."""
    where, params = "WHERE status = 'bounced'", []
    cutoff = since
    if cutoff is None and days is not None and days > 0:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    if cutoff:
        where += " AND sent_at >= ?"
        params.append(cutoff)
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT
              COALESCE(NULLIF(bounce_subtype, ''),
                       NULLIF(bounce_type,    ''),
                       'unknown')                AS label,
              COUNT(*)                           AS n,
              MAX(bounce_diagnostic)             AS sample_diag
            FROM outreach_log
            {where}
            GROUP BY label
            ORDER BY n DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def annotate_bounce(resend_id, bounce_type, bounce_subtype, diagnostic):
    """Persist Resend's bounce reason fields onto the outreach_log row so
    operators can diagnose WHY each address bounced from the outreach UI."""
    if not resend_id:
        return False
    with _conn() as c:
        cur = c.execute(
            "UPDATE outreach_log SET "
            "  bounce_type       = COALESCE(?, bounce_type), "
            "  bounce_subtype    = COALESCE(?, bounce_subtype), "
            "  bounce_diagnostic = COALESCE(?, bounce_diagnostic) "
            "WHERE resend_id = ?",
            (bounce_type, bounce_subtype, diagnostic, resend_id),
        )
        return cur.rowcount > 0


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


def delete_scrape(scrape_id):
    """Delete a scrape_history row. Does NOT remove the underlying CSV file
    or any leads — only the audit-log entry. Returns rows affected."""
    with _conn() as c:
        cur = c.execute("DELETE FROM scrape_history WHERE id = ?", (scrape_id,))
        return cur.rowcount


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


# ─────────────────────────── send quota ───────────────────────────


def _send_count_since(iso_threshold):
    """Count emails actually sent since `iso_threshold`. Combines one-shot
    outreach (outreach_log) + sequencer steps (sequence_messages)."""
    with _conn() as c:
        n_one = c.execute(
            "SELECT COUNT(*) AS n FROM outreach_log "
            "WHERE sent_at >= ? AND status NOT IN ('failed')",
            (iso_threshold,),
        ).fetchone()["n"]
        n_seq = c.execute(
            "SELECT COUNT(*) AS n FROM sequence_messages "
            "WHERE status = 'sent' AND sent_at >= ?",
            (iso_threshold,),
        ).fetchone()["n"]
        return int(n_one or 0) + int(n_seq or 0)


def send_quota_status(daily_cap=100, monthly_cap=3000):
    """Return today / month-to-date send counts + remaining headroom.
    Wired to whatever caps you pass; defaults match Resend free tier."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_n = _send_count_since(today)
    month_n = _send_count_since(month_start)
    return {
        "today": today_n,
        "today_cap": daily_cap,
        "today_remaining": max(0, daily_cap - today_n),
        "today_pct": round(100 * today_n / daily_cap, 1) if daily_cap else 0,
        "month": month_n,
        "month_cap": monthly_cap,
        "month_remaining": max(0, monthly_cap - month_n),
        "month_pct": round(100 * month_n / monthly_cap, 1) if monthly_cap else 0,
        "daily_blocked": today_n >= daily_cap,
        "monthly_blocked": month_n >= monthly_cap,
    }


# ─────────────────────────── dashboard redesign helpers ───────────────────────────
# All SELECT-only. Read-paths only — no schema changes, no inserts/updates/deletes.


def detect_duplicate_sends(window_minutes=10, limit=20):
    """Return list of dicts (lead_email, n, first, last) where the same
    recipient received 2+ outreach_log sends within the rolling time window.
    Catches the auto-enrol duplicate-send bug surfaced on the dashboard."""
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT lead_email,
                   COUNT(*)       AS n,
                   MIN(sent_at)   AS first,
                   MAX(sent_at)   AS last
            FROM outreach_log
            WHERE sent_at >= datetime('now', ?)
            GROUP BY lead_email
            HAVING n >= 2
            ORDER BY n DESC, last DESC
            LIMIT ?
            """,
            (f"-{int(window_minutes)} minutes", limit),
        ).fetchall()
        return [dict(r) for r in rows]


def count_recent_failures(hours=24):
    """Count outreach_log rows with status='failed' in the last N hours."""
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM outreach_log "
            "WHERE status = 'failed' "
            "  AND sent_at >= datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ).fetchone()
        return int(row["n"] or 0)


def outreach_funnel(days=None, since=None):
    """Funnel counts across outreach_log. `days=None` → all-time;
    integer `days` is a rolling-N-day window; `since` is an absolute
    UTC ISO cutoff (preferred — used for IST calendar boundaries)."""
    where, params = "", []
    cutoff = since
    if cutoff is None and days is not None and days > 0:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    if cutoff:
        where = "WHERE sent_at >= ?"
        params = [cutoff]
    with _conn() as c:
        row = c.execute(
            f"""
            SELECT
              COUNT(*)                                                              AS sent,
              SUM(CASE WHEN status NOT IN ('failed','bounced') THEN 1 ELSE 0 END)   AS delivered,
              SUM(CASE WHEN opens > 0                          THEN 1 ELSE 0 END)   AS opened,
              SUM(CASE WHEN clicks > 0                         THEN 1 ELSE 0 END)   AS clicked,
              SUM(CASE WHEN status = 'replied'                 THEN 1 ELSE 0 END)   AS replied,
              SUM(CASE WHEN status = 'failed'                  THEN 1 ELSE 0 END)   AS failed,
              SUM(CASE WHEN status = 'bounced'                 THEN 1 ELSE 0 END)   AS bounced
            FROM outreach_log
            {where}
            """,
            params,
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("sent", "delivered", "opened",
                                              "clicked", "replied", "failed",
                                              "bounced")}


def outreach_stats_by_niche(days=None, since=None):
    """Per-niche outreach performance. `days=None` and `since=None` → all-time."""
    where, params = "", []
    cutoff = since
    if cutoff is None and days is not None and days > 0:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    if cutoff:
        where = "WHERE o.sent_at >= ?"
        params = [cutoff]
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT
              COALESCE(s.niche, '(unassigned)')                              AS niche,
              s.city                                                         AS city,
              COUNT(o.id)                                                    AS sent,
              SUM(CASE WHEN o.status NOT IN ('failed') THEN 1 ELSE 0 END)    AS delivered,
              SUM(CASE WHEN o.opens > 0                THEN 1 ELSE 0 END)    AS opened,
              SUM(CASE WHEN o.status = 'replied'       THEN 1 ELSE 0 END)    AS replied,
              SUM(CASE WHEN o.status = 'failed'        THEN 1 ELSE 0 END)    AS failed,
              MAX(o.sent_at)                                                 AS last_send
            FROM outreach_log o
            LEFT JOIN sequences s ON lower(s.lead_email) = lower(o.lead_email)
            {where}
            GROUP BY niche
            ORDER BY sent DESC
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sent = max(1, d.get("sent") or 0)
            delivered = d.get("delivered") or 0
            d["delivery_rate"] = round(100 * delivered / sent, 1)
            d["open_rate"] = round(100 * (d.get("opened") or 0) / max(1, delivered), 1) if delivered else 0
            out.append(d)
        return out


def sequence_step_distribution():
    """Where every active lead currently sits in the 7-step pipeline.
    Returns a dict {1..7: count}. `current_step` is 0 when nothing has
    sent yet, otherwise the most recent step that did send — so an
    "active at step N" lead lives at bucket N+1 (the step queued next).
    """
    out = {i: 0 for i in range(1, 8)}
    with _conn() as c:
        rows = c.execute(
            "SELECT current_step, COUNT(*) AS n "
            "FROM sequences WHERE status = 'active' "
            "GROUP BY current_step"
        ).fetchall()
        for r in rows:
            cur = int(r["current_step"] or 0)
            bucket = cur + 1 if cur < 7 else 7
            out[bucket] = out.get(bucket, 0) + int(r["n"] or 0)
        return out


def csv_send_summary(csv_path):
    """For a CSV path, return aggregated send health for the Leads card
    footer. Counts are scoped to outreach_log rows tagged with that csv_path."""
    with _conn() as c:
        row = c.execute(
            """
            SELECT
              COUNT(*)                                                       AS sent,
              SUM(CASE WHEN status NOT IN ('failed') THEN 1 ELSE 0 END)      AS delivered,
              SUM(CASE WHEN status = 'failed'        THEN 1 ELSE 0 END)      AS failed,
              SUM(CASE WHEN opens > 0                THEN 1 ELSE 0 END)      AS opened,
              SUM(CASE WHEN status = 'replied'       THEN 1 ELSE 0 END)      AS replied,
              MAX(sent_at)                                                   AS last_send
            FROM outreach_log
            WHERE csv_path = ?
            """,
            (csv_path,),
        ).fetchone()
        return {k: (int(row[k] or 0) if k != "last_send" else row["last_send"])
                for k in ("sent", "delivered", "failed", "opened",
                          "replied", "last_send")}


# ─────────────────────────── dashboard-redesign read helpers ───────────────────────────
# Append-only · all SELECT, no schema changes.


def _since_clause(since_iso, col="sent_at"):
    """Build a tuple ('AND col >= ?', [iso]) when since_iso is set,
    else ('', []). Helper for the *_since family below."""
    if since_iso:
        return f" AND {col} >= ?", [since_iso]
    return "", []


def count_outreach_since(since_iso=None):
    """Total outreach_log rows since the cutoff. None → all-time."""
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM outreach_log {where}", params,
        ).fetchone()
        return int(row["n"] or 0)


def count_delivered_since(since_iso=None):
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM outreach_log "
            f"{where}{' AND' if since_iso else 'WHERE'} status NOT IN ('failed','bounced')",
            params,
        ).fetchone()
        return int(row["n"] or 0)


def count_bounced_since(since_iso=None):
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM outreach_log "
            f"{where}{' AND' if since_iso else 'WHERE'} status = 'bounced'",
            params,
        ).fetchone()
        return int(row["n"] or 0)


def count_clicks_since(since_iso=None):
    """Count outreach_log rows where clicks > 0 since cutoff."""
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"SELECT COALESCE(SUM(clicks), 0) AS n FROM outreach_log {where}",
            params,
        ).fetchone()
        return int(row["n"] or 0)


def count_unique_opens_since(since_iso=None):
    """Distinct outreach_log rows with opens > 0 since cutoff."""
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"SELECT COUNT(*) AS n FROM outreach_log "
            f"{where}{' AND' if since_iso else 'WHERE'} opens > 0",
            params,
        ).fetchone()
        return int(row["n"] or 0)


def activity_feed(since_iso=None, limit=10, status_filter=None):
    """Recent outreach rows for the Activity tab. `status_filter` is one of
    'opened' | 'clicked' | 'sent' | 'bounced' | 'replied' | None.

    Each row carries `seq_step` (1..7 — the drip step this send represents)
    and `seq_status` so the Activity column can show 'Step 3 of 7' for
    sequence-driven sends and '—' for one-shots."""
    clauses = []
    params = []
    if since_iso:
        clauses.append("o.sent_at >= ?")
        params.append(since_iso)
    if status_filter == "opened":
        clauses.append("o.opens > 0 AND o.status NOT IN ('replied','bounced')")
    elif status_filter == "clicked":
        clauses.append("o.clicks > 0")
    elif status_filter == "bounced":
        clauses.append("o.status = 'bounced'")
    elif status_filter == "replied":
        clauses.append("o.status = 'replied'")
    elif status_filter == "sent":
        clauses.append("o.status IN ('sent','delivered') AND o.opens = 0")
    elif status_filter == "failed":
        clauses.append("o.status = 'failed'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT
              o.id, o.lead_email, o.business_name, o.csv_path, o.subject,
              o.status, o.opens, o.clicks, o.sent_at, o.replied_at,
              o.bounce_type, o.bounce_subtype, o.bounce_diagnostic,
              o.resend_id,
              sm.step        AS seq_step,
              s.status       AS seq_status,
              s.current_step AS seq_current_step
            FROM outreach_log o
            LEFT JOIN sequence_messages sm
              ON sm.resend_id = o.resend_id AND sm.resend_id IS NOT NULL
            LEFT JOIN sequences s
              ON s.id = sm.sequence_id
            {where}
            ORDER BY o.sent_at DESC LIMIT ?
            """,
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def activity_counts_by_status(since_iso=None):
    """Counts for the Activity tab filter chips. Returns
    {all, opened, clicked, sent, bounced, replied, failed}."""
    where, params = ("WHERE sent_at >= ?", [since_iso]) if since_iso else ("", [])
    with _conn() as c:
        row = c.execute(
            f"""
            SELECT
              COUNT(*)                                                   AS total_n,
              SUM(CASE WHEN opens > 0 AND status NOT IN ('replied','bounced')
                       THEN 1 ELSE 0 END)                                AS opened,
              SUM(CASE WHEN clicks > 0                THEN 1 ELSE 0 END) AS clicked,
              SUM(CASE WHEN status IN ('sent','delivered') AND opens = 0
                       THEN 1 ELSE 0 END)                                AS sent,
              SUM(CASE WHEN status = 'bounced'        THEN 1 ELSE 0 END) AS bounced,
              SUM(CASE WHEN status = 'replied'        THEN 1 ELSE 0 END) AS replied,
              SUM(CASE WHEN status = 'failed'         THEN 1 ELSE 0 END) AS failed
            FROM outreach_log {where}
            """,
            params,
        ).fetchone()
    out = {k: int(row[k] or 0) for k in ("total_n", "opened", "clicked", "sent", "bounced", "replied", "failed")}
    out["all"] = out.pop("total_n")
    return out


def recent_bounces(window_minutes=1440, limit=20):
    """Most recent bounced sends with their diagnostic. Drives the Issues
    tab bounce card. window_minutes None → all-time."""
    where = ""
    params = []
    if window_minutes:
        where = "AND sent_at >= datetime('now', ?)"
        params.append(f"-{int(window_minutes)} minutes")
    with _conn() as c:
        rows = c.execute(
            f"SELECT lead_email, business_name, csv_path, sent_at, "
            f"       bounce_type, bounce_subtype, bounce_diagnostic "
            f"FROM outreach_log "
            f"WHERE status = 'bounced' {where} "
            f"ORDER BY sent_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


# Recipient local-parts that almost never represent real prospects.
# Used to flag misclassified leads that ate quota — not for scoring.
_FAKE_PROSPECT_LOCALS = (
    "accessibility", "webmaster", "postmaster", "abuse", "noreply", "no-reply",
    "donotreply", "do-not-reply", "privacy", "compliance", "legal",
    "media", "press", "careers", "jobs", "recruiting", "recruitment",
    "events",
)
_FAKE_PROSPECT_DOMAIN_HINTS = (".edu", ".gov", ".gc.ca", ".ac.uk")


def detect_likely_fake_prospects(window_minutes=1440, limit=20):
    """Heuristic match for misclassified recipients (university accessibility
    desks, government contact lines, role accounts). Reads outreach_log
    rows in the window and returns those whose address looks non-prospect.
    Pure SELECT — no writes."""
    where = ""
    params = []
    if window_minutes:
        where = "WHERE sent_at >= datetime('now', ?)"
        params.append(f"-{int(window_minutes)} minutes")
    with _conn() as c:
        rows = c.execute(
            f"SELECT lead_email, business_name, csv_path, sent_at, status "
            f"FROM outreach_log {where} "
            f"ORDER BY sent_at DESC LIMIT 500",
            params,
        ).fetchall()
    out = []
    for r in rows:
        em = (r["lead_email"] or "").lower().strip()
        if not em or "@" not in em:
            continue
        local, _, domain = em.partition("@")
        looks_fake = (
            local in _FAKE_PROSPECT_LOCALS
            or any(domain.endswith(h) for h in _FAKE_PROSPECT_DOMAIN_HINTS)
        )
        if looks_fake:
            out.append(dict(r))
            if len(out) >= limit:
                break
    return out


def upcoming_sends(hours=24, limit=20):
    """Active sequence messages whose next_send_at falls in the next N
    hours. Drives the Pipeline tab 'Next 24 hours' timeline."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, lead_email, business_name, niche, current_step, "
            "       next_send_at "
            "FROM sequences "
            "WHERE status = 'active' "
            "  AND next_send_at IS NOT NULL "
            "  AND next_send_at >= datetime('now') "
            "  AND next_send_at <= datetime('now', ?) "
            "ORDER BY next_send_at ASC LIMIT ?",
            (f"+{int(hours)} hours", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def copy_performance_by_subject(since_iso=None, min_sends=1, limit=50):
    """Per-subject performance aggregation. Drives the copy-telemetry
    feedback loop — the AI prompt pulls top performers from this query
    so future drafts learn from what's actually getting opened.

    Returns rows of {subject, sent, delivered, opens, opens_rate,
    clicks, replied, bounced} ordered by opens_rate DESC."""
    where, params = "", []
    cutoff = since_iso
    if cutoff:
        where = "WHERE sent_at >= ?"
        params = [cutoff]
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT
              subject,
              COUNT(*)                                                       AS sent,
              SUM(CASE WHEN status NOT IN ('failed','bounced') THEN 1 ELSE 0 END) AS delivered,
              SUM(CASE WHEN opens > 0 THEN 1 ELSE 0 END)                     AS unique_opens,
              COALESCE(SUM(opens), 0)                                        AS total_opens,
              SUM(CASE WHEN clicks > 0 THEN 1 ELSE 0 END)                    AS unique_clicks,
              SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END)            AS replied,
              SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END)            AS bounced,
              MAX(sent_at)                                                   AS last_sent
            FROM outreach_log
            {where}
            GROUP BY subject
            HAVING sent >= ?
            ORDER BY (CAST(unique_opens AS FLOAT) / NULLIF(delivered, 0)) DESC,
                     sent DESC
            LIMIT ?
            """,
            params + [min_sends, limit],
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        delivered = d["delivered"] or 0
        d["open_rate"]  = round(100 * (d["unique_opens"] or 0) / delivered, 1) if delivered else 0
        d["click_rate"] = round(100 * (d["unique_clicks"] or 0) / delivered, 1) if delivered else 0
        d["reply_rate"] = round(100 * (d["replied"] or 0) / delivered, 1) if delivered else 0
        out.append(d)
    return out


def top_performing_subjects(min_sends=2, min_open_rate=20, limit=10):
    """Subjects with proven open performance. Used by personalize.py
    to inject positive examples into the LLM prompt so newly-drafted
    subject lines learn from what's actually working."""
    rows = copy_performance_by_subject(min_sends=min_sends, limit=200)
    winners = [r for r in rows if r["open_rate"] >= min_open_rate]
    return winners[:limit]


def copy_performance_by_first_line(since_iso=None, min_sends=2, limit=30):
    """Aggregate by the body's first non-empty line (the cold open). The
    opening line is what makes a recipient keep reading — track its open
    rate so the AI can converge on what works."""
    where, params = "", []
    cutoff = since_iso
    if cutoff:
        where = "WHERE sent_at >= ?"
        params = [cutoff]
    with _conn() as c:
        rows = c.execute(
            f"""
            SELECT id, subject, body, status, opens, clicks, sent_at
            FROM outreach_log
            {where}
            ORDER BY sent_at DESC
            """,
            params,
        ).fetchall()
    buckets = {}
    for r in rows:
        body = (r["body"] or "").strip()
        first = next((ln.strip() for ln in body.split("\n") if ln.strip()), "")
        # Hi <name> openers are nearly identical across sends — skip the
        # greeting, key on the second non-greeting line which is the hook.
        if first.lower().startswith(("hi ", "hello ", "hey ", "dear ")):
            after = body.split("\n", 1)[1] if "\n" in body else ""
            second = next((ln.strip() for ln in after.split("\n") if ln.strip()), "")
            first = second or first
        if not first:
            continue
        key = first[:120]
        b = buckets.setdefault(key, {"first_line": key, "sent": 0,
                                     "delivered": 0, "unique_opens": 0,
                                     "unique_clicks": 0, "last_sent": None})
        b["sent"] += 1
        if r["status"] not in ("failed", "bounced"):
            b["delivered"] += 1
        if (r["opens"] or 0) > 0:
            b["unique_opens"] += 1
        if (r["clicks"] or 0) > 0:
            b["unique_clicks"] += 1
        if not b["last_sent"] or (r["sent_at"] or "") > b["last_sent"]:
            b["last_sent"] = r["sent_at"]
    out = []
    for b in buckets.values():
        if b["sent"] < min_sends:
            continue
        delivered = b["delivered"] or 0
        b["open_rate"]  = round(100 * b["unique_opens"]  / delivered, 1) if delivered else 0
        b["click_rate"] = round(100 * b["unique_clicks"] / delivered, 1) if delivered else 0
        out.append(b)
    out.sort(key=lambda x: (-x["open_rate"], -x["sent"]))
    return out[:limit]


def copy_telemetry_summary(since_iso=None):
    """Top-level numbers for the copy-telemetry block on the dashboard.
    Counts how many distinct subject lines we've actually shipped + how
    many have proven (>=20% open) performance."""
    subjects = copy_performance_by_subject(since_iso=since_iso, min_sends=1, limit=10000)
    winners = [s for s in subjects if s["sent"] >= 2 and s["open_rate"] >= 20]
    return {
        "distinct_subjects": len(subjects),
        "proven_winners": len(winners),
        "subjects_tested_2plus": sum(1 for s in subjects if s["sent"] >= 2),
    }
