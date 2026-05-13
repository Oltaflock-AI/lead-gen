"""Lead-gen dashboard — Flask app.

Run: `python -m src.web.app`  →  http://localhost:5001
"""
import csv
import json
import logging
import os
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template, request,
    session, stream_with_context, url_for,
)
from werkzeug.utils import secure_filename
from google_auth_oauthlib.flow import Flow

from . import ai_metrics
from . import asana as asana_api
from . import db
from . import jobs
from . import metrics
from . import sequencer
from .enrich_runner import run_enrich_stream
from .gmail import check_replies as gmail_check_replies
from . import resend_send
from .email_compose import compose as compose_email
from .personalize import draft_email, draft_emails_batch
from .scraper import COUNTRY_CITIES, COUNTRY_REGION_CODES, COUNTRY_STATES, NICHE_PRESETS, run_search
from .scraper_runner import list_scrapers, run_scraper_stream
from .sheets import SCOPES, add_tab, ensure_master_spreadsheet, update_tab
from .verify import verify_lead_email, update_csv_email
from .fitcheck import compute_fit, update_csv_fit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# ─────────────────────────── logging ───────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _configure_logging():
    root = logging.getLogger()
    if getattr(root, "_lead_gen_configured", False):
        return
    root.setLevel(LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(
        LOG_DIR / "lead-gen.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.handlers = [sh, fh]
    # Tame noisy libs
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    root._lead_gen_configured = True


_configure_logging()
log = logging.getLogger("leadgen.app")

app = Flask(__name__)

_flask_debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true")

_secret = os.getenv("FLASK_SECRET_KEY", "")
if not _secret:
    if _flask_debug:
        _secret = os.urandom(24).hex()
        log.warning("FLASK_SECRET_KEY not set — using random key (dev mode)")
    else:
        raise RuntimeError(
            "FLASK_SECRET_KEY environment variable is required in production. "
            "Set FLASK_DEBUG=1 to allow a random fallback during development."
        )
app.secret_key = _secret

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5001/auth/callback")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "")

if _flask_debug:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


db.init_db()
# Any scrape that was 'running' when Flask was killed is now stale (its
# in-memory worker thread is gone). Mark such rows so they show up correctly
# in the history view instead of forever appearing 'running'.
_orphans = db.mark_orphan_scrapes_interrupted()
if _orphans:
    logging.getLogger("leadgen.app").warning(
        "marked %d orphan scrape(s) as interrupted on startup", _orphans,
    )

# Start the email-sequence scheduler in a daemon thread (idempotent).
# Disable with LEADGEN_SCHEDULER=0 if you want to run ticks manually only.
if os.getenv("LEADGEN_SCHEDULER", "1") not in ("0", "false", "no"):
    sequencer.start_scheduler()

# Reconcile any Supabase events that arrived BEFORE a backfill / schema change
# could match them to outreach_log rows. Idempotent — safe on every restart.
try:
    from . import supabase_sync as _ss
    if _ss.is_configured():
        _r = _ss.reconcile_processed()
        if _r.get("applied"):
            log.info("startup reconcile applied %s past events", _r["applied"])
except Exception:
    log.exception("startup supabase reconcile failed")


# ─────────────────────────── helpers ───────────────────────────


def _oauth_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )


def _has_oauth_config():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _google_creds():
    return db.load_token()


def _slug(s):
    """Lowercase, alphanumeric + underscore only."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "untitled"


INTERACTIVE_FIELDNAMES = [
    "Business Name", "City", "Address", "Phone",
    "Email", "Email Verified", "Email Kind", "Email Legit", "Email SMTP",
    "Rating", "Reviews", "Business Type", "Google Maps URL",
]


def _save_interactive_csv(leads, niche, country):
    """Append leads to data/outputs/interactive_<niche>_<country>.csv.

    If the file exists already, honor its (possibly widened) header so each
    appended row writes one cell per column. Without this guard, downstream
    enrichment / AI-score steps that add columns leave new scrape rows
    shorter than the header and DictReader silently shifts every value left.
    Returns the absolute path written.
    """
    if not leads:
        return None
    basename = f"interactive_{_slug(niche)}_{_slug(country)}.csv"
    path = PROJECT_ROOT / "data" / "outputs" / basename
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            try:
                existing_header = next(csv.reader(f))
            except StopIteration:
                existing_header = []
        # Use the existing header verbatim, extending only with canonical
        # fields it doesn't yet carry. Never re-order existing columns.
        fieldnames = list(existing_header)
        for col in INTERACTIVE_FIELDNAMES:
            if col not in fieldnames:
                fieldnames.append(col)
        new_file = False

        # If we widened the schema, rewrite the file with the new header so
        # every row matches the new column count.
        if fieldnames != existing_header:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                for row in rows:
                    w.writerow({k: row.get(k, "") for k in fieldnames})
    else:
        fieldnames = list(INTERACTIVE_FIELDNAMES)
        new_file = True

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for l in leads:
            writer.writerow({k: l.get(k, "") for k in fieldnames})
    return str(path)


def _push_to_master_sheet(creds, niche, country, leads):
    """Add the leads as a new tab in the master sheet. Creates the master if
    needed. Returns dict {url, tab_title, master_url, spreadsheet_id} or
    {error: ...}.
    """
    if not leads:
        return {"error": "no leads"}
    try:
        existing = db.get_setting("master_spreadsheet_id", "") or None
        spreadsheet_id, master_url = ensure_master_spreadsheet(creds, existing_id=existing)
        if spreadsheet_id != existing:
            db.set_setting("master_spreadsheet_id", spreadsheet_id)
            db.set_setting("master_spreadsheet_url", master_url)
        tab_base = f"{niche} — {country} — {datetime.now().strftime('%m/%d %H:%M')}"
        info = add_tab(creds, spreadsheet_id, tab_base, leads, INTERACTIVE_FIELDNAMES)
        return {
            "spreadsheet_id": spreadsheet_id,
            "master_url": master_url,
            "tab_url": info["url"],
            "tab_title": info["tab_title"],
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _safe_csv_path(name):
    """Resolve a CSV name to a path inside data/imports or data/outputs."""
    name = Path(name or "").name  # strip dir traversal
    if not name:
        return None
    for d in (PROJECT_ROOT / "data" / "outputs", PROJECT_ROOT / "data" / "imports"):
        p = d / name
        if p.is_file():
            return p
    return None


def _load_leads_by_email(csv_name):
    """Index a CSV by lowercased email so /api/outreach/send can pull
    rating, review count, city, and business type for the sequencer."""
    p = _safe_csv_path(csv_name)
    if not p:
        return {}
    out = {}
    try:
        with open(p, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                em = (row.get("Email") or row.get("email") or "").strip().lower()
                if not em:
                    continue
                try:
                    rating = float(row.get("Rating") or 0)
                except (TypeError, ValueError):
                    rating = 0.0
                try:
                    reviews = int(float(row.get("Reviews") or row.get("Review Count") or 0))
                except (TypeError, ValueError):
                    reviews = 0
                btype = row.get("Business Type") or row.get("Niche") or ""
                out[em] = {
                    "email": em,
                    "business_name": row.get("Business Name", ""),
                    "city": row.get("City", ""),
                    "business_type": btype,
                    "niche": row.get("Niche") or btype,
                    "rating": rating,
                    "review_count": reviews,
                    "website": row.get("Website", ""),
                }
    except Exception as e:
        log.warning("_load_leads_by_email csv=%s failed: %s", csv_name, e)
    return out


def _ctx():
    """Common template context — available to every page."""
    try:
        fail_count = db.count_recent_failures(hours=24)
    except Exception:
        fail_count = 0
    try:
        active_seq = db.sequence_stats().get("active", 0)
    except Exception:
        active_seq = 0
    return {
        "google_connected": _google_creds() is not None,
        "has_oauth_config": _has_oauth_config(),
        "claude_enabled": ai_metrics.is_enabled(),
        "asana_enabled": asana_api.is_configured(),
        "resend_enabled": resend_send.is_configured(),
        "sidebar_counts": {"fail": fail_count, "sequences": active_seq},
    }


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


def _resolve_window_start(window):
    """Map the dashboard 'window' query (today | 7d | 30d | all) to a UTC
    ISO cutoff. Today uses IST calendar midnight."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    w = (window or "today").strip().lower()
    if w == "all":
        return None
    if w in ("7d", "30d"):
        days = 7 if w == "7d" else 30
        return (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    ist = _tz(_td(hours=5, minutes=30))
    midnight_ist = _dt.now(ist).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_ist.astimezone(_tz.utc).isoformat()


def _detect_issues(window_start, sent_in_window, bounce_count):
    """Build the Issues tab card list. Always includes the duplicate-send
    'all clear' green card so operators see something even on a healthy day.
    Severity:
      'crit' (red)   — bounce rate over threshold
      'warn' (amber) — fake-prospect addresses got mailed
      'ok' (green)   — duplicate-send guard holding
    Tab badge counts only crit + warn."""
    issues = []
    if sent_in_window:
        b_rate = bounce_count / sent_in_window
        if b_rate > 0.05:
            samples = db.recent_bounces(window_minutes=24 * 60, limit=3)
            sample_lines = ", ".join(
                f"{(s.get('business_name') or s.get('lead_email') or '').strip()} ({s.get('lead_email') or ''})"
                for s in samples
            )
            issues.append({
                "id": "bounce_rate",
                "severity": "crit",
                "title": f"Bounce rate at {b_rate * 100:.1f}% — over the 5% threshold",
                "body": (f"{bounce_count} of {sent_in_window} sends in this window bounced. "
                         f"{sample_lines}. Permanent bounces are auto-suppressed; "
                         "sustained rates above 5% will hurt domain reputation."),
            })

    fake_prospects = db.detect_likely_fake_prospects(window_minutes=24 * 60, limit=3)
    if fake_prospects:
        sample = fake_prospects[0]
        issues.append({
            "id": "fake_prospects",
            "severity": "warn",
            "title": f"{sample.get('lead_email')} isn't a real prospect",
            "body": (f"Sent at {(sample.get('sent_at') or '')[:16].replace('T', ' ')} from "
                     f"{(sample.get('csv_path') or '').split('/')[-1]}. "
                     "Looks like a role account or institutional contact. "
                     f"{len(fake_prospects)} similar misclassification{'s' if len(fake_prospects) != 1 else ''} found in last 24h."),
        })

    duplicates = db.detect_duplicate_sends(window_minutes=24 * 60, limit=5)
    if duplicates:
        names = ", ".join(d.get("lead_email") or "" for d in duplicates[:3])
        issues.append({
            "id": "duplicates",
            "severity": "warn",
            "title": "Duplicate sends detected in last 24h",
            "body": f"{names} received 2+ sends within 10 minutes. Auto-enrol guard may be slipping.",
        })
    else:
        issues.append({
            "id": "duplicates_ok",
            "severity": "ok",
            "title": "No duplicate sends in the last 24h",
            "body": "Auto-enrol guard appears to be holding.",
        })

    return issues


def _resolve_range(range_raw, default_key="14"):
    """Map ?range=... query into (range_key, since_iso, days_for_metric, label).

    'today' uses IST calendar midnight (UTC+5:30) so a send at 11pm yesterday IST
    does NOT count. Other windows are rolling N-day cutoffs from now (UTC).
    'all' returns since=None (no filter)."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    keys = {"today", "7", "14", "30", "90", "all"}
    key = (range_raw or default_key).strip().lower()
    if key not in keys:
        key = default_key
    label = {"today": "Today", "7": "Last 7 days", "14": "Last 14 days",
             "30": "Last 30 days", "90": "Last 90 days", "all": "All time"}[key]
    if key == "today":
        ist = _tz(_td(hours=5, minutes=30))
        midnight_ist = _dt.now(ist).replace(hour=0, minute=0, second=0, microsecond=0)
        since_iso = midnight_ist.astimezone(_tz.utc).isoformat()
        return key, since_iso, 1, label
    if key == "all":
        return key, None, 90, label
    days = int(key)
    since_iso = (_dt.now(_tz.utc) - _td(days=days)).isoformat()
    return key, since_iso, days, label


@app.template_filter("ist")
def _ist_filter(value, fmt="full"):
    """Convert a UTC ISO timestamp into 12-hour IST.

    fmt='full'  → '2:30 PM IST · May 6'
    fmt='time'  → '2:30 PM IST'
    fmt='short' → '2:30 PM'  (no IST suffix; for tight cells)
    """
    if not value:
        return "—"
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    try:
        s = str(value).replace("Z", "+00:00")
        ts = _dt.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        ist = ts.astimezone(_tz(_td(hours=5, minutes=30)))
    except Exception:
        return str(value)[:16]
    hour = ist.hour % 12 or 12
    ampm = "AM" if ist.hour < 12 else "PM"
    time_str = f"{hour}:{ist.minute:02d} {ampm}"
    if fmt == "time":
        return f"{time_str} IST"
    if fmt == "short":
        return time_str
    return f"{time_str} IST · {ist.strftime('%b %-d')}"


@app.context_processor
def _inject_dashboard_helpers():
    """Expose the new dashboard-redesign read helpers to every template
    so pages other than the dashboard route (Outreach, Sequences) can call
    them without their underlying route being modified."""
    return {
        "daily_sends": metrics.daily_sends_last_n,
        "sparkline_points": metrics.sparkline_points,
        "csv_health": metrics.csv_health_score,
        "detect_duplicate_sends": db.detect_duplicate_sends,
        "sequence_step_distribution": db.sequence_step_distribution,
        "outreach_funnel": db.outreach_funnel,
        "csv_send_summary": db.csv_send_summary,
    }


# ─────────────────────────── pages ───────────────────────────


@app.route("/")
def dashboard():
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    range_key, range_since, range_days, range_label = _resolve_range(
        request.args.get("range"), default_key="14")
    settings = db.get_settings()
    outreach = db.outreach_stats()
    summary = metrics.dashboard_summary(settings, outreach)
    recent = db.list_outreach(limit=50)

    # Sent today / niches today (from outreach log).
    today_prefix = _dt.utcnow().strftime("%Y-%m-%d")
    recent_500 = db.list_outreach(limit=500)
    sent_today_rows = [r for r in recent_500 if (r.get("sent_at") or "").startswith(today_prefix)]
    summary["sent_today"] = len(sent_today_rows)
    summary["niches_today"] = 0  # outreach_log lacks niche column; placeholder

    # Today's queue — active sequences with next_send_at in next 24h.
    now = _dt.now(_tz.utc)
    horizon = now + _td(hours=24)
    active_seq = db.list_sequences(status="active", limit=500)
    today_queue = []
    step_labels = {1: "Cold", 2: "Bump", 3: "FOMO",
                   4: "Loom", 5: "Math", 6: "Quirky", 7: "Breakup"}
    for s in active_seq:
        nxt = (s.get("next_send_at") or "").strip()
        if not nxt:
            continue
        try:
            ts = _dt.fromisoformat(nxt.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
        except Exception:
            continue
        if now <= ts <= horizon:
            mins = int((ts - now).total_seconds() // 60)
            if mins < 60:
                rel = f"in {max(0, mins)}m"
            elif mins < 24 * 60:
                rel = f"in {mins // 60}h {mins % 60}m"
            else:
                rel = ts.strftime("tomorrow %H:%M")
            today_queue.append({
                "when": ts.strftime("%H:%M"),
                "rel": rel,
                "business_name": s.get("business_name"),
                "lead_email": s.get("lead_email"),
                "niche": s.get("niche"),
                "step": (s.get("current_step") or 0) + 1,
                "step_label": step_labels.get((s.get("current_step") or 0) + 1, ""),
            })
    today_queue.sort(key=lambda q: q["when"])
    summary["queued_today"] = len(today_queue)

    today_label = _dt.utcnow().strftime("%A, %b %-d")

    # ── Dashboard redesign additions ─────────────────────────────────────
    funnel_raw = db.outreach_funnel(since=range_since)
    sent_n      = funnel_raw.get("sent") or 0
    delivered_n = funnel_raw.get("delivered") or 0
    opened_n    = funnel_raw.get("opened") or 0
    clicked_n   = funnel_raw.get("clicked") or 0
    replied_n   = funnel_raw.get("replied") or 0
    failed_n    = funnel_raw.get("failed") or 0
    bounced_n   = funnel_raw.get("bounced") or 0

    # CSV-derived top of funnel.
    csvs = metrics.list_csvs()
    csv_summaries = [metrics.csv_summary(c["path"]) for c in csvs]
    total_leads_n = sum(c["rows"] for c in csv_summaries)
    has_email_n   = sum(c["with_email"] for c in csv_summaries)
    funnel_top    = max(1, total_leads_n)

    funnel = [
        {"label": "Scraped",    "n": total_leads_n, "pct": 100,
         "tone": "ok",   "warn": False},
        {"label": "Has email",  "n": has_email_n,
         "pct": round(100 * has_email_n / funnel_top),
         "tone": "ok",   "warn": False},
        {"label": "Sent",       "n": sent_n,
         "pct": round(100 * sent_n / funnel_top),
         "tone": "ok",   "warn": False},
        {"label": "Delivered",  "n": delivered_n,
         "pct": round(100 * delivered_n / funnel_top),
         "tone": "warn" if (sent_n and delivered_n / max(1, sent_n) < 0.7) else "ok",
         "warn": (sent_n and delivered_n / max(1, sent_n) < 0.7)},
        {"label": "Opened",     "n": opened_n,
         "pct": round(100 * opened_n / funnel_top),
         "tone": "dim",  "warn": False},
        {"label": "Replied",    "n": replied_n,
         "pct": round(100 * replied_n / funnel_top),
         "tone": "dim",  "warn": False},
        {"label": "Bounced",    "n": bounced_n,
         "pct": round(100 * bounced_n / funnel_top),
         "tone": "warn" if bounced_n > 0 else "dim",
         "warn": (sent_n and bounced_n / max(1, sent_n) >= 0.05)},
    ]
    delivery_rate  = round(100 * delivered_n / sent_n,      1) if sent_n      else 0
    real_open_rate = round(100 * opened_n    / delivered_n, 1) if delivered_n else 0
    bounce_rate    = round(100 * bounced_n   / sent_n,      1) if sent_n      else 0
    click_rate     = round(100 * clicked_n   / delivered_n, 1) if delivered_n else 0

    # KPI tiles
    daily_series = metrics.daily_sends_last_n(range_days)
    spark_points = metrics.sparkline_points(daily_series)
    avg_per_day = round(sum(d["count"] for d in daily_series) / max(1, len(daily_series)), 1)

    kpis = {
        "total_leads": total_leads_n,
        "csv_count": len(csvs),
        "sent_today": summary.get("sent_today", 0),
        "spark": spark_points,
        "avg_per_day": avg_per_day,
        "delivery_rate": delivery_rate,
        "delivery_alert": delivery_rate < 70 and sent_n > 0,
        "delivered": delivered_n,
        "sent": sent_n,
        "opened": opened_n,
        "clicked": clicked_n,
        "bounced": bounced_n,
        "failed": failed_n,
        "open_rate": real_open_rate,
        "click_rate": click_rate,
        "bounce_rate": bounce_rate,
        "bounce_alert": bounce_rate >= 5 and sent_n > 0,
        "replies": replied_n,
        "reply_rate": round(100 * replied_n / sent_n, 1) if sent_n else 0,
    }

    # Alert banner — fire when delivery is bad, or duplicates, or many failures.
    duplicates = db.detect_duplicate_sends(window_minutes=10)
    recent_failures_24h = db.count_recent_failures(hours=24)
    alert = None
    if (sent_n > 0 and delivery_rate < 70) or recent_failures_24h > 5 or duplicates:
        title_bits = []
        if sent_n > 0 and delivery_rate < 70:
            fail_pct = 100 - delivery_rate
            title_bits.append(f"{fail_pct:.0f}% of recent sends failed")
        elif recent_failures_24h > 5:
            title_bits.append(f"{recent_failures_24h} sends failed in last 24h")
        elif duplicates:
            title_bits.append("Duplicate sends detected")
        msg_bits = []
        if delivery_rate < 70 and sent_n > 0:
            msg_bits.append(
                f"{delivered_n} of {sent_n} delivered. Real open rate on delivered "
                f"is {real_open_rate}%."
            )
        if duplicates:
            sample = ", ".join((d.get("lead_email") or "")[:40]
                               for d in duplicates[:3])
            msg_bits.append(
                f"Duplicates: {sample}{'…' if len(duplicates) > 3 else ''} "
                f"received 2+ sends within 10 minutes."
            )
        alert = {
            "title": title_bits[0] if title_bits else "Delivery issue",
            "message": " ".join(msg_bits) or "Investigate the outreach log.",
            "duplicates": duplicates,
        }

    # 7-step pipeline
    pipe_dist = db.sequence_step_distribution()
    pipeline = [
        {"day": "Day 0",  "name": "Cold",   "count": pipe_dist.get(1, 0)},
        {"day": "Day 3",  "name": "Bump",   "count": pipe_dist.get(2, 0)},
        {"day": "Day 7",  "name": "FOMO",   "count": pipe_dist.get(3, 0)},
        {"day": "Day 11", "name": "Loom",   "count": pipe_dist.get(4, 0)},
        {"day": "Day 16", "name": "Math",   "count": pipe_dist.get(5, 0)},
        {"day": "Day 21", "name": "Quirky", "count": pipe_dist.get(6, 0)},
        {"day": "Day 28", "name": "Pizza",  "count": pipe_dist.get(7, 0)},
    ]
    pipeline_total = sum(p["count"] for p in pipeline)

    niche_perf = db.outreach_stats_by_niche(since=range_since)

    # Group recent activity by status for the activity block.
    def _group(rows):
        order = ["failed", "bounced", "replied", "opened", "clicked",
                 "delivered", "sent"]
        labels = {
            "failed":    {"key": "failed",    "label": "Failed",          "icon": "✕"},
            "bounced":   {"key": "bounced",   "label": "Bounced",         "icon": "↩"},
            "opened":    {"key": "opened",    "label": "Sent + opened",   "icon": "✓"},
            "clicked":   {"key": "clicked",   "label": "Clicked",         "icon": "✓"},
            "replied":   {"key": "replied",   "label": "Replied",         "icon": "✦"},
            "delivered": {"key": "delivered", "label": "Sent",            "icon": "✓"},
            "sent":      {"key": "sent",      "label": "Sent (legacy)",   "icon": "✓"},
        }
        buckets = {k: [] for k in order}
        for r in rows:
            s = (r.get("status") or "").lower()
            # opens bump status to 'opened' but legacy rows may still be
            # 'sent' with opens > 0 — promote them visually.
            if s in ("sent", "delivered") and (r.get("opens") or 0) > 0:
                s = "opened"
            if s in buckets:
                buckets[s].append(r)
            else:
                buckets.setdefault("delivered", []).append(r)
        return [
            {**labels[k], "rows": buckets[k]}
            for k in order if buckets.get(k)
        ]

    activity_groups = _group(recent[:50])

    bounce_reasons = db.bounce_breakdown(since=range_since, limit=8)
    suppression_total = db.suppression_count()
    copy_top = db.copy_performance_by_subject(since_iso=range_since, min_sends=1, limit=5)
    copy_summary = db.copy_telemetry_summary(since_iso=range_since)

    # ── Dashboard redesign · time-windowed KPIs + tabbed block ──
    window = (request.args.get("window") or "today").strip().lower()
    if window not in ("today", "7d", "30d", "all"):
        window = "today"
    window_start = _resolve_window_start(window)

    sent_in_window      = db.count_outreach_since(window_start)
    delivered_in_window = db.count_delivered_since(window_start)
    bounced_in_window   = db.count_bounced_since(window_start)
    delivery_rate_w     = metrics.delivery_rate(window_start)
    open_rate_w         = metrics.open_rate_on_delivered(window_start)
    bounce_rate_w       = metrics.bounce_rate(window_start)

    activity_status = (request.args.get("activity_status") or "").strip().lower() or None
    if activity_status not in ("opened", "clicked", "sent", "bounced", "replied", "failed"):
        activity_status = None

    new_kpis = {
        "total_leads": metrics.total_leads_all_time(),
        "csv_count": len(csvs),
        "sent_in_window": sent_in_window,
        "delivered_in_window": delivered_in_window,
        "bounced_in_window": bounced_in_window,
        "click_count": db.count_clicks_since(window_start),
        "unique_opens": db.count_unique_opens_since(window_start),
        "delivery_rate": round(delivery_rate_w * 100, 1),
        "open_rate": round(open_rate_w * 100, 1),
        "bounce_rate": round(bounce_rate_w * 100, 1),
        "bounce_alert": bounce_rate_w > 0.05 and sent_in_window > 0,
        "delivery_alert": delivery_rate_w < 0.7 and sent_in_window > 0,
        "avg_per_day": round(
            sum(d["count"] for d in metrics.daily_sends_last_n(7)) / 7, 1),
        "spark": metrics.sparkline_points(metrics.daily_sends_last_n(14)),
    }
    activity_rows = db.activity_feed(window_start, limit=10, status_filter=activity_status)
    activity_counts = db.activity_counts_by_status(window_start)
    upcoming = db.upcoming_sends(hours=24, limit=20)
    issues = _detect_issues(window_start, sent_in_window, bounced_in_window)
    issues_warn_count = sum(1 for i in issues if i["severity"] in ("crit", "warn"))
    window_label = {"today": "Today", "7d": "Last 7 days",
                    "30d": "Last 30 days", "all": "All time"}[window]

    return render_template(
        "dashboard.html",
        summary=summary,
        recent=recent,
        settings=settings,
        today_queue=today_queue[:20],
        today_label=today_label,
        funnel=funnel,
        delivery_rate=delivery_rate,
        real_open_rate=real_open_rate,
        kpis=kpis,
        new_kpis=new_kpis,
        alert=alert,
        pipeline=pipeline,
        pipeline_total=pipeline_total,
        niche_perf=niche_perf,
        activity_groups=activity_groups,
        range_key=range_key,
        range_label=range_label,
        bounce_reasons=bounce_reasons,
        suppression_total=suppression_total,
        copy_top=copy_top,
        copy_summary=copy_summary,
        window=window,
        window_label=window_label,
        activity_rows=activity_rows,
        activity_counts=activity_counts,
        activity_status=activity_status,
        upcoming=upcoming,
        issues=issues,
        issues_warn_count=issues_warn_count,
        **_ctx(),
    )


@app.route("/scrape")
def scrape_page():
    return render_template(
        "scrape.html",
        canonical_scrapers=list_scrapers(),
        countries=list(COUNTRY_CITIES.keys()),
        niches=list(NICHE_PRESETS.keys()),
        **_ctx(),
    )


@app.route("/leads")
def leads_index():
    csvs = metrics.list_csvs()
    summaries = [metrics.csv_summary(c["path"]) for c in csvs]
    for c, s in zip(csvs, summaries):
        s["dir"] = c["dir"]
    return render_template("leads.html", csvs=summaries, **_ctx())


@app.route("/api/leads/upload", methods=["POST"])
def api_leads_upload():
    """Accept a user-supplied CSV. File saved into data/imports/ with a
    sanitised name. Email column may be any of: email, Email,
    contact_emails, contact_professions_email — see metrics.COLUMN_ALIASES."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file uploaded (form field 'file')"}), 400
    raw_name = secure_filename(f.filename)
    if not raw_name.lower().endswith(".csv"):
        return jsonify({"error": "only .csv files are accepted"}), 400

    imports_dir = PROJECT_ROOT / "data" / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)

    # Disambiguate clashes so an upload never silently overwrites a prior one.
    dest = imports_dir / raw_name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = imports_dir / f"{stem}_{ts}{suffix}"

    f.save(str(dest))

    # Validate CSV is parseable + has at least one email column.
    try:
        with open(dest, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            headers = list(reader.fieldnames or [])
            n_rows = sum(1 for _ in reader)
    except Exception as e:
        dest.unlink(missing_ok=True)
        return jsonify({"error": f"unparseable CSV: {e}"}), 400

    email_aliases = {"email", "Email", "contact_emails", "contact_professions_email"}
    if not (set(headers) & email_aliases):
        dest.unlink(missing_ok=True)
        return jsonify({
            "error": "CSV must contain one of these email columns: "
                     + ", ".join(sorted(email_aliases)),
            "headers": headers,
        }), 400

    log.info("uploaded leads CSV %s rows=%d headers=%s",
             dest.name, n_rows, headers)
    return jsonify({
        "ok": True,
        "name": dest.name,
        "rows": n_rows,
        "headers": headers,
        "next_url": url_for("leads_detail", name=dest.name),
    })


@app.route("/leads/<name>")
def leads_detail(name):
    path = _safe_csv_path(name)
    if not path:
        return redirect(url_for("leads_index"))
    range_key, range_since, _, range_label = _resolve_range(
        request.args.get("range"), default_key="all")
    fieldnames, rows = metrics.read_csv_with_scores(path)
    summary = metrics.csv_summary(path)
    contacted = db.outreach_stats()["contacted_emails"]
    for r in rows:
        r["_contacted"] = r["_normalized"]["email"] in contacted if r["_normalized"]["email"] else False
    asana_for_rows = db.list_asana_tasks_for_csv(name)
    for r in rows:
        bn = r["_normalized"]["business_name"]
        r["_asana_url"] = asana_for_rows.get(bn, "")
    # Outreach status per email so the leads table mirrors the outreach board.
    outreach_status = db.outreach_status_by_email(name, since=range_since)
    campaign = db.outreach_campaign_summary(name)
    return render_template(
        "leads_detail.html",
        name=name,
        path=str(path),
        fieldnames=fieldnames,
        rows=rows,
        summary=summary,
        outreach_status=outreach_status,
        campaign=campaign,
        sheet=db.get_sheet_for_csv(name),
        range_key=range_key,
        range_label=range_label,
        **_ctx(),
    )


@app.route("/outreach")
def outreach_page():
    csv_name = request.args.get("csv", "")
    range_key, range_since, range_days, range_label = _resolve_range(
        request.args.get("range"), default_key="14")
    selected_path = _safe_csv_path(csv_name) if csv_name else None
    rows = []
    fieldnames = []
    drafts_by_email = {}
    if selected_path:
        fieldnames, all_rows = metrics.read_csv_with_scores(selected_path)
        rows = [r for r in all_rows if r["_normalized"]["email"]]
        skipped_no_email = len(all_rows) - len(rows)
        # Pull existing drafts so the table cells pre-fill on reload.
        for d in db.get_outreach_drafts(csv_name):
            drafts_by_email[d["lead_email"]] = d
    log = db.list_outreach(limit=50, csv_path=csv_name) if csv_name else db.list_outreach(limit=50)
    campaign = db.outreach_campaign_summary(csv_name) if csv_name else db.outreach_campaign_summary()
    status_by_email = db.outreach_status_by_email(csv_name)
    settings = db.get_settings()
    sig_lines = []
    if settings.get("sender_name"):
        sig_lines.append(settings["sender_name"])
    title, company = settings.get("sender_title", ""), settings.get("company_name", "")
    if title and company:
        sig_lines.append(f"{title}, {company}")
    elif company:
        sig_lines.append(company)
    if settings.get("website_url"):
        sig_lines.append(settings["website_url"])
    if settings.get("booking_url"):
        sig_lines.append(f"Book a call: {settings['booking_url']}")
    return render_template(
        "outreach.html",
        all_csvs=metrics.list_csvs(),
        selected=csv_name,
        rows=rows,
        log=log,
        drafts_by_email=drafts_by_email,
        signature_preview=sig_lines,
        campaign=campaign,
        status_by_email=status_by_email,
        sheet=db.get_sheet_for_csv(csv_name) if csv_name else None,
        range_key=range_key,
        range_label=range_label,
        range_days=range_days,
        skipped_no_email=skipped_no_email if selected_path else 0,
        **_ctx(),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        for key in db.SETTINGS_KEYS:
            if key in request.form:
                db.set_setting(key, request.form[key])
        return redirect(url_for("settings_page"))
    return render_template(
        "settings.html",
        settings=db.get_settings(),
        env_status={
            "GOOGLE_PLACES_API_KEY": bool(GOOGLE_PLACES_API_KEY),
            "GOOGLE_CLIENT_ID": bool(GOOGLE_CLIENT_ID),
            "GOOGLE_CLIENT_SECRET": bool(GOOGLE_CLIENT_SECRET),
            "ANTHROPIC_API_KEY": bool(ANTHROPIC_API_KEY),
            "RESEND_API_KEY": bool(RESEND_API_KEY),
            "RESEND_FROM": bool(RESEND_FROM),
        },
        **_ctx(),
    )


# ─────────────────────────── api: jobs ───────────────────────────


@app.route("/api/jobs/active")
def api_jobs_active():
    return jsonify({"jobs": jobs.list_active()})


@app.route("/api/jobs/recent")
def api_jobs_recent():
    return jsonify({"jobs": jobs.list_recent(limit=20)})


@app.route("/api/jobs/<jid>/events")
def api_jobs_events(jid):
    """Poll-friendly endpoint: returns events from `cursor` onward as JSON.

    Replaces the streaming SSE variant which suffered from Werkzeug
    chunk-buffering when the browser used fetch+ReadableStream.
    """
    cursor = int(request.args.get("cursor", 0))
    j = jobs.get(jid)
    if j is None:
        return jsonify({"status": "missing", "events": [], "cursor": cursor}), 404
    new_events = j["events"][cursor:]
    return jsonify({
        "status": j["status"],
        "events": new_events,
        "cursor": cursor + len(new_events),
        "result": j.get("result") if j["status"] != "running" else None,
        "error": j.get("error"),
    })


# ─────────────────────────── api: scrapers ───────────────────────────


@app.route("/api/cities/<country>")
def api_cities(country):
    return jsonify(COUNTRY_CITIES.get(country, []))


@app.route("/api/states/<country>")
def api_states(country):
    return jsonify(COUNTRY_STATES.get(country, []))


@app.route("/api/niche-types/<niche>")
def api_niche_types(niche):
    return jsonify(NICHE_PRESETS.get(niche, []))


@app.route("/api/scrape/canonical/<key>", methods=["POST"])
def api_scrape_canonical(key):
    label = f"Canonical scraper · {key}"
    jid = jobs.create_job("canonical_scrape", label)
    scrape_id = db.record_scrape_start(jid, "canonical_scrape", label, {"key": key})

    def worker():
        try:
            gen = run_scraper_stream(key)
            try:
                for line in gen:
                    if jobs.is_cancelled(jid):
                        jobs.append(jid, {"type": "log", "line": "✋ cancelled by user — stopping scraper"})
                        break
                    jobs.append(jid, {"type": "log", "line": line})
            finally:
                gen.close()  # triggers proc.terminate()
            cancelled = jobs.is_cancelled(jid)
            final_status = "cancelled" if cancelled else "done"
            jobs.append(jid, {"type": "done", "cancelled": cancelled})
            jobs.finish(jid, result={"key": key, "cancelled": cancelled}, status=final_status)
            j = jobs.get(jid)
            db.record_scrape_finish(
                scrape_id, final_status,
                events=(j or {}).get("events", []),
            )
        except Exception as e:
            db.record_scrape_finish(scrape_id, "failed",
                                    events=(jobs.get(jid) or {}).get("events", []),
                                    error=e)
            raise

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label, "scrape_id": scrape_id})


@app.route("/api/scrape/interactive", methods=["POST"])
def api_scrape_interactive():
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY not set in .env"}), 400

    data = request.json or {}
    country = data.get("country", "US")
    state = (data.get("state") or "").strip()
    city = (data.get("city") or "").strip()
    cities = data.get("cities")
    business_types = data.get("business_types", [])
    min_reviews = int(data.get("min_reviews", 50))
    min_rating = float(data.get("min_rating", 4.0))
    target_leads = int(data.get("target_leads", 100))
    require_no_website = bool(data.get("require_no_website", False))
    verify_emails = bool(data.get("verify_emails", False))
    niche = (data.get("niche") or "scrape").strip()
    region_code = COUNTRY_REGION_CODES.get(country, "US")

    if not state:
        return jsonify({"error": "State / region is required"}), 400
    if not business_types:
        return jsonify({"error": "No business types selected"}), 400

    # Build the city-list to query. Explicit user input overrides everything.
    if not cities:
        if city:
            cities = [f"{city}, {state}"]
        else:
            cities = [state]

    # When verifying emails, we OVER-scrape heavily because email yield from
    # DDG search is low (~10-20%) — especially with require_no_website=True
    # since the lead has no site to scrape. We keep iterating until we've
    # collected `target_leads` rows that actually carry an email; the worker
    # `break`s as soon as that target is met. Multiplier sized so even a
    # ~5% hit rate still satisfies the user's target.
    if verify_emails:
        # Bias the multiplier higher when the no-website filter is on,
        # since those leads have the worst email-yield.
        per_target = 30 if require_no_website else 20
        internal_target = max(target_leads * per_target, target_leads + 200)
        internal_target = min(internal_target, 5000)
    else:
        internal_target = target_leads

    label = f"Scrape · {niche} · {city or state}"
    jid = jobs.create_job("interactive_scrape", label)
    scrape_id = db.record_scrape_start(jid, "interactive_scrape", label, {
        "niche": niche, "country": country, "state": state, "city": city,
        "business_types": business_types,
        "target_leads": target_leads,
        "min_reviews": min_reviews, "min_rating": min_rating,
        "require_no_website": require_no_website,
        "verify_emails": verify_emails,
    })
    log.info(
        "scrape job %s (history#%s) start: niche=%r country=%s state=%s city=%s "
        "types=%s min_reviews=%d min_rating=%.1f target=%d "
        "require_no_website=%s verify_emails=%s internal_target=%d",
        jid, scrape_id, niche, country, state, city, business_types,
        min_reviews, min_rating, target_leads,
        require_no_website, verify_emails, internal_target,
    )

    def on_event(kind, payload):
        """Bridge run_search diagnostics → job event log + Python logger."""
        if kind == "search_start":
            line = (
                f"▶ searching {payload['cities']} cities × {payload['types']} types "
                f"(target {payload['target']}, region {payload['region']})"
            )
        elif kind == "query_start":
            line = f"  → query: {payload['query']} (kept so far: {payload['leads_so_far']})"
        elif kind == "query_done":
            line = (
                f"  ✓ {payload['city']} / {payload['type']} — "
                f"{payload['raw']} raw, {payload['kept']} kept "
                f"(total {payload['total_kept']})"
            )
        elif kind == "query_error":
            line = (
                f"  ✗ API error on {payload.get('query','?')} — "
                f"status={payload.get('status')} reason={payload.get('reason')} "
                f"{payload.get('body','')[:160]}"
            )
            log.error("scrape job %s api error: %s", jid, payload)
        elif kind == "search_end":
            line = (
                f"■ search end — kept {payload['kept']} of {payload['raw']} raw "
                f"({payload['filtered']} filtered, {payload['api_calls']} api calls); "
                f"filters={payload['filter_counts']}"
            )
            log.info("scrape job %s search_end: %s", jid, payload)
        else:
            line = f"  · {kind}: {payload}"
        jobs.append(jid, {"type": "log", "line": line})

    def worker():
        leads = []
        skipped_no_email = 0
        for lead, progress in run_search(
            cities=cities,
            business_types=business_types,
            api_key=GOOGLE_PLACES_API_KEY,
            region_code=region_code,
            min_reviews=min_reviews,
            min_rating=min_rating,
            target_leads=internal_target,
            require_no_website=require_no_website,
            on_event=on_event,
        ):
            if jobs.is_cancelled(jid):
                jobs.append(jid, {
                    "type": "log",
                    "line": f"✋ cancelled by user — keeping {len(leads)} lead(s) collected so far",
                })
                break
            if verify_emails:
                v = verify_lead_email(
                    business_name=lead.get("Business Name", ""),
                    current_email="",
                    region=lead.get("City", ""),
                    country=country,
                    # Keep role accounts (info@, contact@) — they're often the
                    # ONLY published address for small businesses. The operator
                    # can filter later if needed.
                    drop_role=False,
                )
                # Accept any email whose MX resolves. SMTP probes are flaky
                # behind residential ISPs (port 25 blocked) so requiring full
                # `legit` would silently drop most otherwise-valid leads.
                if not v.get("email") or not v.get("mx_ok"):
                    # Drop leads with no legit email when user asked for emails-only.
                    # 'role' / 'disposable' / failed-SMTP all fall here.
                    skipped_no_email += 1
                    why = v.get("dropped") or (
                        "no email" if not v.get("email")
                        else f"smtp={v.get('smtp_check')}"
                    )
                    jobs.append(jid, {
                        "type": "log",
                        "line": f"  ↷ skip {lead.get('Business Name', '')} — {why}",
                    })
                    if len(leads) >= target_leads:
                        break
                    continue
                lead["Email"] = v["email"]
                lead["Email Verified"] = "yes" if v.get("verified") else "found"
                lead["Email Kind"] = v.get("kind", "")
                lead["Email Legit"] = "yes" if v.get("legit") else "no"
                lead["Email SMTP"] = v.get("smtp_check", "")

            leads.append(lead)
            # Override the progress numerator to count only kept leads, so the
            # UI shows progress toward the user's actual target.
            shown_progress = dict(progress)
            shown_progress["leads_found"] = len(leads)
            shown_progress["total_target"] = target_leads
            shown_progress["skipped_no_email"] = skipped_no_email
            jobs.append(jid, {"type": "lead", "lead": lead, "progress": shown_progress})

            if len(leads) >= target_leads:
                break

        # ── Auto deep research per lead ────────────────────────────────────
        # Before saving, run find_business_website + deep_find_email on every
        # collected lead. This (a) verifies has_website for real (Google
        # Places' websiteUri is often empty for businesses that DO have a
        # site) so the drafter never mis-pitches and (b) fills missing emails
        # from the discovered site / contact pages. Concurrent so a 50-lead
        # scrape adds ~30-60s, not 10+ minutes.
        if leads and not jobs.is_cancelled(jid):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from .email_finder import deep_enrich_lead

            def _enrich_one(idx_lead):
                idx, ld = idx_lead
                name = ld.get("Business Name", "")
                if not name:
                    return idx, None
                region = ld.get("City", "") or city or state
                try:
                    return idx, deep_enrich_lead(name, region, country)
                except Exception as exc:
                    log.warning(
                        "scrape job %s deep_enrich failed for %r: %s",
                        jid, name, exc,
                    )
                    return idx, None

            jobs.append(jid, {
                "type": "log",
                "line": f"🔎 deep research: enriching {len(leads)} lead(s) "
                        "(website + email discovery)...",
            })
            enriched_count = 0
            websites_found = 0
            emails_found = 0
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(_enrich_one, (i, l)): i
                        for i, l in enumerate(leads)}
                for fut in as_completed(futs):
                    if jobs.is_cancelled(jid):
                        break
                    try:
                        idx, enr = fut.result()
                    except Exception:
                        continue
                    if not enr:
                        continue
                    ld = leads[idx]
                    site = enr.get("website") or ""
                    site_score = enr.get("website_score") or 0
                    # Trust discovered site at score >= 5 (matches
                    # find_business_website threshold).
                    if site and site_score >= 5 and not ld.get("Website"):
                        ld["Website"] = site
                        ld["Has Website"] = "yes"
                        websites_found += 1
                    elif not site and not ld.get("Website"):
                        # Verified: deep search found nothing → real no-site.
                        ld["Has Website"] = "no"
                    new_email = (enr.get("email") or "").strip()
                    if new_email and not (ld.get("Email") or "").strip():
                        ld["Email"] = new_email
                        ld["Email Source"] = enr.get("email_source", "")
                        emails_found += 1
                    enriched_count += 1
            jobs.append(jid, {
                "type": "log",
                "line": f"✓ deep research done: enriched {enriched_count}/"
                        f"{len(leads)} (+{websites_found} websites, "
                        f"+{emails_found} emails)",
            })

        csv_path = _save_interactive_csv(leads, niche, country)
        # Long-term lead store — mirror every scraped row into Supabase
        # so cross-CSV intent + cohort analysis are possible later.
        try:
            from . import supabase_leads
            if supabase_leads.is_configured() and leads:
                src = Path(csv_path).name if csv_path else ""
                # Tag rows so column projection picks niche/country.
                tagged = [{**l, "niche": niche, "country": country} for l in leads]
                n = supabase_leads.upsert_leads(tagged, source_csv=src)
                log.info("supabase leads_master upsert: %d rows from %s", n, src)
        except Exception:
            log.exception("supabase_leads upsert (scrape) failed")
        sheet_info = None
        creds = _google_creds()
        if leads and creds:
            sheet_info = _push_to_master_sheet(creds, niche, country, leads)
        result = {
            "total": len(leads),
            "leads": leads,
            "csv_path": csv_path,
            "csv_basename": Path(csv_path).name if csv_path else None,
            "sheet": sheet_info,
            "skipped_no_email": skipped_no_email,
        }
        log.info(
            "scrape job %s done: kept=%d skipped_no_email=%d csv=%s sheet=%s",
            jid, len(leads), skipped_no_email,
            Path(csv_path).name if csv_path else None,
            (sheet_info or {}).get("tab_title"),
        )
        if not leads:
            jobs.append(jid, {
                "type": "log",
                "line": "⚠ scrape finished with 0 leads — check filter thresholds, API key, "
                        "or query results above for error details",
            })
        cancelled = jobs.is_cancelled(jid)
        final_status = "cancelled" if cancelled else "done"
        result["cancelled"] = cancelled
        jobs.append(jid, {"type": "done", "cancelled": cancelled,
                          **{k: v for k, v in result.items() if k != "leads"}})
        jobs.finish(jid, result=result, status=final_status)

        # Persist a snapshot of this scrape so it survives Flask restart.
        db.record_scrape_finish(
            scrape_id, final_status,
            leads_count=len(leads),
            csv_basename=Path(csv_path).name if csv_path else None,
            sheet_url=(sheet_info or {}).get("sheet_url"),
            events=(jobs.get(jid) or {}).get("events", []),
        )

    def _wrapped():
        try:
            worker()
        except Exception as e:
            db.record_scrape_finish(
                scrape_id, "failed",
                events=(jobs.get(jid) or {}).get("events", []),
                error=e,
            )
            raise

    jobs.run_in_thread(jid, _wrapped)
    return jsonify({"job_id": jid, "label": label, "scrape_id": scrape_id})


# ─────────────────────────── api: enrich ───────────────────────────


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    data = request.json or {}
    name = data.get("name", "")
    keep_with_website = bool(data.get("keep_with_website", False))
    skip_email = bool(data.get("skip_email", False))

    path = _safe_csv_path(name)
    if not path:
        return jsonify({"error": f"CSV not found: {name}"}), 404

    label = f"Enrich · {name}"
    jid = jobs.create_job("enrich", label)

    def worker():
        for line in run_enrich_stream(path, keep_with_website=keep_with_website, skip_email=skip_email):
            jobs.append(jid, {"type": "log", "line": line})
        jobs.append(jid, {"type": "done"})
        jobs.finish(jid, result={"csv": name})

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


# ─────────────────────────── api: outreach ───────────────────────────


@app.route("/api/outreach/preview", methods=["POST"])
def api_outreach_preview():
    data = request.json or {}
    leads = data.get("leads", [])
    csv_name = (data.get("csv_name") or "").strip()
    force = bool(data.get("force"))
    sender = db.get_setting("sender_name", "") or ""

    norms = [
        (lead.get("_normalized") if lead.get("_normalized") else metrics.normalize_lead(lead))
        for lead in leads
    ]

    # Skip leads that already have a saved draft (unless `force`).
    cached = {}
    to_generate = []
    to_generate_idx = []
    if csv_name and not force:
        for d in db.get_outreach_drafts(csv_name):
            cached[d["lead_email"]] = d

    def _stale_website_pitch_draft(lead, draft):
        """Cached draft was made under the old no-website pivot and pitches a
        prebuilt site. We no longer ship the website-mockup pitch — regen so
        the AI services pitch (voice agent + chatbot + ROI) replaces it."""
        text = ((draft.get("subject") or "") + " " +
                (draft.get("body") or "")).lower()
        markers = (
            "mocked", "preview link", "built you a quick site",
            "built a quick site", "doesn't have a website",
            "no website", "free demo + 7-day trial",
        )
        return any(m in text for m in markers)

    out = [None] * len(norms)
    for i, n in enumerate(norms):
        em = (n.get("email") or "").lower()
        if em in cached and not force and not _stale_website_pitch_draft(n, cached[em]):
            c = cached[em]
            out[i] = {
                "subject": c["subject"], "body": c["body"],
                "personalized": True, "lead": n, "cached": True,
            }
        else:
            to_generate.append(n)
            to_generate_idx.append(i)

    t0 = datetime.now()
    if to_generate:
        fresh = draft_emails_batch(to_generate, sender_name=sender)
        engine = "claude" if ANTHROPIC_API_KEY else "template"
        for i, d in zip(to_generate_idx, fresh):
            d["cached"] = False
            out[i] = d
            if csv_name and d.get("body"):
                db.upsert_outreach_draft(
                    csv_path=csv_name,
                    lead_email=(d["lead"].get("email") or "").lower(),
                    subject=d.get("subject", ""),
                    body=d.get("body", ""),
                    business_name=d["lead"].get("business_name", ""),
                    engine=engine,
                )
    elapsed = (datetime.now() - t0).total_seconds()
    log.info("outreach preview csv=%s total=%d generated=%d cached=%d in %.1fs",
             csv_name, len(out), len(to_generate), len(cached) - sum(
                 1 for x in out if not x or not x.get("cached")), elapsed)
    return jsonify({
        "drafts": out,
        "personalized_engine": "claude" if ANTHROPIC_API_KEY else "template",
        "elapsed_sec": round(elapsed, 2),
        "generated": len(to_generate),
        "from_cache": sum(1 for x in out if x and x.get("cached")),
    })


@app.route("/api/outreach/draft", methods=["POST"])
def api_outreach_draft_save():
    """Persist user edits to a draft (subject/body) without re-generating."""
    data = request.json or {}
    csv_name = (data.get("csv_name") or "").strip()
    lead_email = (data.get("lead_email") or "").strip().lower()
    if not csv_name or not lead_email:
        return jsonify({"error": "csv_name and lead_email required"}), 400
    db.upsert_outreach_draft(
        csv_path=csv_name,
        lead_email=lead_email,
        subject=(data.get("subject") or "").strip(),
        body=(data.get("body") or "").strip(),
        business_name=(data.get("business_name") or "").strip(),
        engine="manual",
    )
    return jsonify({"ok": True})


@app.route("/api/outreach/refine", methods=["POST"])
def api_outreach_refine():
    """Apply a user instruction to an existing draft. Cheap, fast, persists."""
    data = request.json or {}
    csv_name = (data.get("csv_name") or "").strip()
    lead = data.get("lead") or {}
    norm = lead.get("_normalized") or metrics.normalize_lead(lead) if lead else {}
    instruction = (data.get("instruction") or "").strip()
    current_subject = (data.get("subject") or "").strip()
    current_body = (data.get("body") or "").strip()

    if not instruction:
        return jsonify({"error": "instruction required"}), 400
    if not current_body:
        return jsonify({"error": "no draft to refine"}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 400

    try:
        from anthropic import Anthropic
        from .personalize import SYSTEM_PROMPT, BATCH_MODEL, BATCH_MAX_TOKENS
        from . import niche_briefs
    except Exception as e:
        return jsonify({"error": f"refine setup failed: {e}"}), 500

    sender = db.get_setting("sender_name", "") or ""
    brief = niche_briefs.get_brief_for_lead(norm) if norm else None
    sys_blocks = [{"type": "text", "text": SYSTEM_PROMPT,
                   "cache_control": {"type": "ephemeral"}}]
    if brief:
        sys_blocks.extend(niche_briefs.system_blocks(brief))

    facts = (
        f"business_name: {norm.get('business_name','')}\n"
        f"city: {norm.get('city','')}\n"
        f"business_type: {norm.get('business_type','')}\n"
        f"google_rating: {norm.get('rating',0)}\n"
        f"review_count: {norm.get('review_count',0)}\n"
        f"sender_name: {sender}\n"
    )
    user_msg = (
        "Refine the cold email below using the user instruction. Keep it "
        "compliant with all formatting rules. Output JSON only.\n\n"
        f"--- lead facts ---\n{facts}\n"
        f"--- current subject ---\n{current_subject}\n\n"
        f"--- current body ---\n{current_body}\n\n"
        f"--- user instruction ---\n{instruction}\n"
    )

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=BATCH_MODEL,
            max_tokens=BATCH_MAX_TOKENS,
            system=sys_blocks,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        import re as _re, json as _json
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            return jsonify({"error": "no JSON in refine output"}), 500
        parsed = _json.loads(m.group(0))
        new_subject = (parsed.get("subject") or current_subject)[:120]
        new_body = (parsed.get("body") or "").strip()
        if not new_body:
            return jsonify({"error": "empty body returned"}), 500
    except Exception as e:
        log.exception("refine failed")
        return jsonify({"error": str(e)}), 500

    if csv_name and norm.get("email"):
        db.upsert_outreach_draft(
            csv_path=csv_name,
            lead_email=norm["email"].lower(),
            subject=new_subject,
            body=new_body,
            business_name=norm.get("business_name", ""),
            engine="claude-refine",
        )
    return jsonify({"subject": new_subject, "body": new_body})


@app.route("/api/outreach/regen-field", methods=["POST"])
def api_outreach_regen_field():
    """Regenerate ONLY subject OR ONLY body for a single lead. Saves credits.

    Payload: { csv_name, lead, field: 'subject'|'body', subject, body }
    """
    data = request.json or {}
    csv_name = (data.get("csv_name") or "").strip()
    lead = data.get("lead") or {}
    norm = lead.get("_normalized") or metrics.normalize_lead(lead) if lead else {}
    field = (data.get("field") or "").strip().lower()
    current_subject = (data.get("subject") or "").strip()
    current_body = (data.get("body") or "").strip()

    if field not in ("subject", "body"):
        return jsonify({"error": "field must be 'subject' or 'body'"}), 400
    if not norm.get("email"):
        return jsonify({"error": "lead missing email"}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 400

    try:
        from anthropic import Anthropic
        from .personalize import SYSTEM_PROMPT, BATCH_MODEL
        from . import niche_briefs
    except Exception as e:
        return jsonify({"error": f"setup failed: {e}"}), 500

    sender = db.get_setting("sender_name", "") or ""
    brief = niche_briefs.get_brief_for_lead(norm)
    sys_blocks = [{"type": "text", "text": SYSTEM_PROMPT,
                   "cache_control": {"type": "ephemeral"}}]
    if brief:
        sys_blocks.extend(niche_briefs.system_blocks(brief))

    facts = (
        f"business_name: {norm.get('business_name','')}\n"
        f"city: {norm.get('city','')}\n"
        f"business_type: {norm.get('business_type','')}\n"
        f"google_rating: {norm.get('rating',0)}\n"
        f"review_count: {norm.get('review_count',0)}\n"
        f"sender_name: {sender}\n"
    )

    if field == "subject":
        user_msg = (
            "Generate a NEW subject line ONLY. Quirky, curiosity-driven, "
            "pattern-interrupt — must follow all subject rules. Keep the body "
            "EXACTLY as given. Output JSON.\n\n"
            f"--- lead facts ---\n{facts}\n"
            f"--- current subject (replace this) ---\n{current_subject}\n\n"
            f"--- current body (do not change) ---\n{current_body}\n"
        )
        max_tokens = 80
    else:
        user_msg = (
            "Rewrite the BODY ONLY for this prospect, keeping the subject "
            "exactly as given. Hyper-personalize using the lead facts. "
            "Follow the formatting rules strictly. Output JSON with the "
            "unchanged subject and the new body.\n\n"
            f"--- lead facts ---\n{facts}\n"
            f"--- subject (do not change) ---\n{current_subject}\n\n"
            f"--- current body (replace this) ---\n{current_body or '(empty)'}\n"
        )
        max_tokens = 500

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=BATCH_MODEL,
            max_tokens=max_tokens,
            system=sys_blocks,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        import re as _re, json as _json
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            return jsonify({"error": "no JSON in regen output"}), 500
        parsed = _json.loads(m.group(0))
    except Exception as e:
        log.exception("regen-field failed")
        return jsonify({"error": str(e)}), 500

    new_subject = (parsed.get("subject") or current_subject)[:120].strip()
    new_body = (parsed.get("body") or current_body).strip()
    # Enforce that only the requested field actually changed.
    if field == "subject":
        new_body = current_body
    else:
        new_subject = current_subject or new_subject

    if csv_name:
        db.upsert_outreach_draft(
            csv_path=csv_name,
            lead_email=norm["email"].lower(),
            subject=new_subject,
            body=new_body,
            business_name=norm.get("business_name", ""),
            engine=f"claude-regen-{field}",
        )
    return jsonify({"subject": new_subject, "body": new_body, "field": field})


@app.route("/api/outreach/draft/clear", methods=["POST"])
def api_outreach_draft_clear():
    data = request.json or {}
    csv_name = (data.get("csv_name") or "").strip()
    lead_email = (data.get("lead_email") or "").strip().lower()
    if not csv_name or not lead_email:
        return jsonify({"error": "csv_name and lead_email required"}), 400
    db.delete_outreach_draft(csv_name, lead_email)
    return jsonify({"ok": True})


@app.route("/api/copy-performance")
def api_copy_performance():
    """Surface per-subject and per-first-line open/click/reply rates.

    Used by the dashboard 'copy telemetry' card AND by external tooling
    that wants to inspect what subject lines are working. Outreach_log
    is the source of truth — every send already records subject + body
    + opens + clicks per row, so this is pure aggregation."""
    days_raw = (request.args.get("days") or "").strip()
    since_iso = None
    if days_raw and days_raw.isdigit():
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        since_iso = (_dt.now(_tz.utc) - _td(days=int(days_raw))).isoformat()
    return jsonify({
        "summary":      db.copy_telemetry_summary(since_iso=since_iso),
        "by_subject":   db.copy_performance_by_subject(since_iso=since_iso, min_sends=1, limit=50),
        "by_first_line": db.copy_performance_by_first_line(since_iso=since_iso, min_sends=2, limit=20),
        "winners":      db.top_performing_subjects(min_sends=2, min_open_rate=20, limit=10),
    })


@app.route("/api/outreach/check-replies", methods=["POST"])
def api_outreach_check_replies():
    creds = _google_creds()
    if not creds:
        return jsonify({"error": "Google not connected. /auth/google first."}), 401
    days = int((request.json or {}).get("days", 30))
    try:
        result = gmail_check_replies(creds, days=days)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/outreach/send", methods=["POST"])
def api_outreach_send():
    """Kick off Send-All as a background job and return a job_id.

    The actual send loop runs in jobs.run_in_thread so a Werkzeug reload
    or client navigate-away can't kill the batch mid-flight. Frontend
    polls /api/jobs/<jid>/events for per-lead progress.
    """
    if not resend_send.is_configured():
        return jsonify({"error": "Resend not configured. Set RESEND_API_KEY and RESEND_FROM in .env"}), 400

    data = request.json or {}
    sends = data.get("sends", [])
    csv_path = data.get("csv_path", "")
    if not sends:
        return jsonify({"error": "no sends in batch"}), 400

    settings = db.get_settings()
    sender = settings.get("sender_name", "")

    # The daily/monthly caps in /api/quota are advisory indicators only —
    # the user wants to keep sending until Resend itself rate-limits us.
    # We do not block batches here; per-send Resend errors are surfaced
    # via the worker's results array.

    leads_by_email = _load_leads_by_email(csv_path) if csv_path else {}

    if csv_path:
        retry_emails = [s.get("to") or s.get("email") for s in sends if s.get("to") or s.get("email")]
        if retry_emails:
            db.delete_failed_outreach(csv_path=csv_path, lead_emails=retry_emails)

    all_addrs = [s.get("to") or s.get("email") for s in sends]
    suppressed_set = db.filter_suppressed(all_addrs)
    verify_on_send = os.getenv("LEADGEN_VERIFY_ON_SEND", "0") in ("1", "true", "yes")

    total = len(sends)
    label = f"Send {total} email{'s' if total != 1 else ''} · {csv_path or 'mixed'}"
    jid = jobs.create_job("outreach_send", label)

    def worker():
        sent_ok, failed, skipped = 0, 0, 0
        results = []
        for i, s in enumerate(sends, 1):
            to_addr = s.get("to") or s.get("email")
            subject = s.get("subject", "")
            body = s.get("body", "")
            business_name = s.get("business_name", "")

            def progress(status, note=""):
                jobs.append(jid, {
                    "type": "lead",
                    "i": i, "total": total,
                    "to": to_addr, "business_name": business_name,
                    "status": status, "note": note,
                    "sent_count": sent_ok, "failed_count": failed,
                    "skipped_count": skipped,
                    "progress": {"i": i, "total": total},
                })

            if not (to_addr and subject and body):
                failed += 1
                results.append({"to": to_addr, "ok": False, "error": "missing fields"})
                progress("failed", "missing fields")
                continue

            addr_lower = (to_addr or "").strip().lower()
            if addr_lower in suppressed_set:
                skipped += 1
                db.log_outreach(to_addr, business_name, csv_path, subject, body,
                                gmail_message_id=None, status="skipped")
                results.append({
                    "to": to_addr, "ok": False,
                    "error": "suppressed (previously bounced or complained)",
                    "skipped": "suppressed",
                })
                progress("skipped", "suppressed")
                continue

            if verify_on_send:
                try:
                    v = verify_lead_email(business_name, to_addr, "", "",
                                          probe_smtp=True, skip_search=True)
                    if not v.get("legit"):
                        smtp_check = v.get("smtp_check") or "unknown"
                        diag = "; ".join(v.get("reasons", [])) or smtp_check
                        db.upsert_suppression(
                            to_addr, reason="manual",
                            bounce_type="PreSendVerify",
                            bounce_subtype=smtp_check, diagnostic=diag,
                        )
                        skipped += 1
                        db.log_outreach(to_addr, business_name, csv_path, subject, body,
                                        gmail_message_id=None, status="skipped")
                        results.append({
                            "to": to_addr, "ok": False,
                            "error": f"failed pre-send verify ({smtp_check})",
                            "skipped": "verify_failed",
                        })
                        progress("skipped", f"verify failed ({smtp_check})")
                        continue
                except Exception as e:
                    log.warning("pre-send verify error for %s: %s", to_addr, e)

            csv_lead = leads_by_email.get(to_addr.strip().lower(), {})
            lead = {
                **csv_lead,
                "email": to_addr,
                "business_name": business_name or csv_lead.get("business_name", ""),
                "niche": csv_lead.get("niche") or csv_lead.get("business_type", ""),
                "city": csv_lead.get("city", ""),
                "rating": csv_lead.get("rating", 0),
                "review_count": csv_lead.get("review_count", 0),
                "website": csv_lead.get("website", ""),
            }

            try:
                result = sequencer.enqueue_lead_with_first_send(
                    lead, csv_name=csv_path, sender_name=sender,
                    override_subject=subject, override_body=body,
                )
            except Exception as e:
                log.exception("enqueue_lead_with_first_send crashed for %s", to_addr)
                failed += 1
                db.log_outreach(to_addr, business_name, csv_path, subject, body,
                                gmail_message_id=None, status="failed")
                results.append({"to": to_addr, "ok": False, "error": str(e)})
                progress("failed", str(e)[:120])
                continue

            if result["status"] == "queued":
                if result["resend_id"]:
                    db.log_outreach(
                        to_addr, business_name, csv_path, subject, body,
                        gmail_message_id=f"resend:{result['resend_id']}",
                        status="sent", resend_id=result["resend_id"],
                    )
                    try:
                        from . import supabase_leads
                        supabase_leads.mark_sent(to_addr, business_name, subject)
                    except Exception:
                        log.exception("supabase_leads mark_sent failed for %s", to_addr)
                sent_ok += 1
                results.append({
                    "to": to_addr, "ok": True,
                    "message_id": result["resend_id"],
                    "sequence_id": result["sequence_id"],
                    "note": "enrolled in 7-step sequence",
                })
                progress("sent", "step 1 sent + enrolled")
            elif result["status"] == "already_active":
                skipped += 1
                results.append({
                    "to": to_addr, "ok": True,
                    "message_id": None,
                    "sequence_id": result["sequence_id"],
                    "note": "already in active sequence",
                })
                progress("skipped", "already in active sequence")
            else:
                failed += 1
                db.log_outreach(to_addr, business_name, csv_path, subject, body,
                                gmail_message_id=None, status="failed")
                results.append({"to": to_addr, "ok": False,
                                "error": result["error"]})
                progress("failed", (result.get("error") or "")[:120])

        summary = {
            "total": total,
            "sent": sent_ok,
            "failed": failed,
            "skipped": skipped,
            "results": results,
        }
        jobs.append(jid, {"type": "done", **{k: v for k, v in summary.items() if k != "results"}})
        jobs.finish(jid, result=summary)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label, "total": total})


@app.route("/api/outreach/clear-failed", methods=["POST"])
def api_outreach_clear_failed():
    """One-shot cleanup of accumulated failed log entries. Optional
    csv_name in body restricts to a single CSV; absent = clear globally."""
    data = request.json or {}
    csv_name = (data.get("csv_name") or "").strip() or None
    n = db.delete_failed_outreach(csv_path=csv_name)
    return jsonify({"ok": True, "deleted": n, "csv_name": csv_name})


@app.route("/api/outreach/mark-replied/<int:log_id>", methods=["POST"])
def api_mark_replied(log_id):
    import sqlite3
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db.DB_PATH) as c:
        c.execute(
            "UPDATE outreach_log SET status = 'replied', "
            "replied_at = COALESCE(replied_at, ?), "
            "last_event_at = ?, last_event_type = 'manual.mark_replied' "
            "WHERE id = ?",
            (now, now, log_id),
        )
    return jsonify({"ok": True})


@app.route("/api/outreach/unmark-replied/<int:log_id>", methods=["POST"])
def api_unmark_replied(log_id):
    """Revert a manually-marked reply. Falls back to the highest signal we
    have from webhook events: clicked > opened > delivered > sent."""
    import sqlite3
    with sqlite3.connect(db.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT clicks, opens, bounced FROM outreach_log WHERE id = ?",
            (log_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "row not found"}), 404
        if row["bounced"]:
            new_status = "bounced"
        elif row["clicks"]:
            new_status = "clicked"
        elif row["opens"]:
            new_status = "opened"
        else:
            new_status = "delivered"
        now = datetime.utcnow().isoformat()
        c.execute(
            "UPDATE outreach_log SET status = ?, replied_at = NULL, "
            "last_event_at = ?, last_event_type = 'manual.unmark_replied' "
            "WHERE id = ?",
            (new_status, now, log_id),
        )
    return jsonify({"ok": True, "status": new_status})


# ─────────────────────────── api: sheets ───────────────────────────


@app.route("/api/sheets/export", methods=["POST"])
def api_sheets_export():
    """Sync a CSV to a tab inside the single master spreadsheet.

    First call: ensures the master exists, adds a tab named after the CSV,
    persists the mapping in db.csv_sheets.
    Subsequent calls: clear and rewrite the existing tab in place. Never
    creates a new spreadsheet.
    """
    creds = _google_creds()
    if not creds:
        return jsonify({"error": "Google not connected"}), 401

    data = request.json or {}
    name = data.get("name", "")
    leads = data.get("leads")
    fieldnames = data.get("fieldnames")

    if not name:
        return jsonify({"error": "csv name required"}), 400

    if not leads:
        path = _safe_csv_path(name)
        if not path:
            return jsonify({"error": f"CSV not found: {name}"}), 404
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            leads = list(reader)

    if not leads:
        return jsonify({"error": "No leads to export"}), 400

    try:
        existing_master = db.get_setting("master_spreadsheet_id", "") or None
        spreadsheet_id, master_url = ensure_master_spreadsheet(creds, existing_id=existing_master)
        if spreadsheet_id != existing_master:
            db.set_setting("master_spreadsheet_id", spreadsheet_id)
            db.set_setting("master_spreadsheet_url", master_url)

        # Pick a stable tab name — the CSV basename minus .csv. Fits the user's
        # mental model of "one tab per lead list".
        existing_mapping = db.get_sheet_for_csv(name)
        tab_title = (existing_mapping and existing_mapping.get("tab_title")) or name.replace(".csv", "")

        synced = False
        if existing_mapping and existing_mapping.get("sheet_id") == spreadsheet_id and existing_mapping.get("tab_title"):
            try:
                info = update_tab(creds, spreadsheet_id, tab_title, leads, fieldnames=fieldnames)
                synced = True
            except Exception:
                # Tab may have been deleted upstream — fall through and recreate.
                info = add_tab(creds, spreadsheet_id, tab_title, leads, fieldnames=fieldnames)
        else:
            info = add_tab(creds, spreadsheet_id, tab_title, leads, fieldnames=fieldnames)

        db.set_sheet_for_csv(name, spreadsheet_id, info["url"], tab_title=info["tab_title"])
        return jsonify({
            "url": info["url"],
            "master_url": master_url,
            "tab_title": info["tab_title"],
            "synced": synced,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sheets/url/<path:name>")
def api_sheets_url(name):
    name = Path(name).name
    rec = db.get_sheet_for_csv(name)
    if not rec:
        return jsonify({"url": None})
    return jsonify({"url": rec["sheet_url"], "updated_at": rec["updated_at"]})


# ─────────────────────────── api: verify + fit ───────────────────────────


@app.route("/api/verify-email", methods=["POST"])
def api_verify_email():
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY required for email search"}), 400
    data = request.json or {}
    business_name = data.get("business_name", "")
    current_email = data.get("current_email", "")
    region = data.get("region", "")
    country = data.get("country", "")
    csv_name = data.get("csv_name", "")
    persist = bool(data.get("persist", True))

    if not business_name and not current_email:
        return jsonify({"error": "business_name or current_email required"}), 400

    result = verify_lead_email(business_name, current_email, region, country)
    persisted = False
    if persist and csv_name and business_name:
        path = _safe_csv_path(csv_name)
        if path:
            persisted = update_csv_email(
                path, business_name,
                new_email=result.get("email", ""),
                verified=result.get("verified"),
                kind=result.get("kind"),
                legit=result.get("legit"),
                smtp_check=result.get("smtp_check"),
            )
    result["persisted"] = persisted
    return jsonify(result)


@app.route("/api/bulk-verify-emails", methods=["POST"])
def api_bulk_verify_emails():
    """Iterate every row in a CSV. For each business, look up + MX-verify
    an email and persist back to the CSV. Streams progress via SSE.
    """
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY required"}), 400
    data = request.json or {}
    csv_name = data.get("csv_name", "")
    path = _safe_csv_path(csv_name)
    if not path:
        return jsonify({"error": f"CSV not found: {csv_name}"}), 404

    _, rows = metrics.read_csv_with_scores(path)
    total = len(rows)

    label = f"Bulk verify emails · {csv_name}"
    jid = jobs.create_job("bulk_verify", label)

    def worker():
        verified, found, missing, persisted = 0, 0, 0, 0
        for i, r in enumerate(rows, 1):
            n = r["_normalized"]
            name = n["business_name"]
            current = n["email"]
            region = n["city"]
            if not name:
                continue
            v = verify_lead_email(name, current, region, "")
            if v.get("verified"):
                verified += 1
            elif v.get("email"):
                found += 1
            else:
                missing += 1
            if v.get("email") or current:
                ok = update_csv_email(
                    path, name, new_email=v.get("email", current),
                    verified=v.get("verified"),
                    kind=v.get("kind"),
                    legit=v.get("legit"),
                    smtp_check=v.get("smtp_check"),
                )
                if ok:
                    persisted += 1
            jobs.append(jid, {
                "type": "progress",
                "i": i, "total": total,
                "name": name,
                "email": v.get("email", ""),
                "verified": v.get("verified"),
                "verified_count": verified,
                "found_count": found,
                "missing_count": missing,
            })
        result = {
            "total": total,
            "verified": verified,
            "found": found,
            "missing": missing,
            "persisted": persisted,
        }
        jobs.append(jid, {"type": "done", **result})
        jobs.finish(jid, result=result)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


@app.route("/api/bulk-deep-enrich", methods=["POST"])
def api_bulk_deep_enrich():
    """Per-lead deep enrichment:
      1. Search the open web for the business's official website. If the
         site is found with high confidence, write `Website` + `Has Website
         = yes` back to the CSV (this corrects scrapes where Google Places
         had no websiteUri but the business actually has a public site).
      2. Pull emails from the discovered site's home/contact/about pages,
         plus DDG snippets and directory pages (Facebook /about, Yelp,
         BBB, YellowPages). Score, MX-verify, persist."""
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY required"}), 400
    data = request.json or {}
    csv_name = data.get("csv_name", "")
    only_missing = bool(data.get("only_missing", True))
    path = _safe_csv_path(csv_name)
    if not path:
        return jsonify({"error": f"CSV not found: {csv_name}"}), 404

    from .email_finder import deep_enrich_lead
    from .verify import update_csv_website

    _, rows = metrics.read_csv_with_scores(path)
    total = len(rows)

    label = f"Deep enrich · {csv_name}"
    jid = jobs.create_job("deep_enrich", label)

    from .email_finder import find_business_website, deep_find_email

    def worker():
        verified, found, missing, persisted = 0, 0, 0, 0
        websites_found, skipped = 0, 0
        for i, r in enumerate(rows, 1):
            if jobs.is_cancelled(jid):
                jobs.append(jid, {"type": "log",
                                  "line": f"✋ cancelled — processed {i-1}/{total}"})
                break
            n = r["_normalized"]
            name = n["business_name"]
            current = n["email"]
            region = n["city"]
            country_guess = n.get("country", "") or ""
            has_site_already = (n.get("has_website") or "").lower() == "yes" \
                                or bool((n.get("website") or "").strip())
            need_email = not current
            need_website = not has_site_already
            if not name:
                continue
            # Skip only when the row needs neither website nor email.
            # When only_missing is False, every row gets re-checked.
            if only_missing and not need_email and not need_website:
                skipped += 1
                jobs.append(jid, {
                    "type": "progress",
                    "i": i, "total": total, "name": name,
                    "email": current, "verified": True,
                    "website": n.get("website") or "",
                    "website_score": 0,
                    "verified_count": verified, "found_count": found,
                    "missing_count": missing, "websites_found": websites_found,
                    "skipped_count": skipped,
                })
                continue

            site_url, site_score = "", 0
            email_addr, email_src = "", ""
            v = {"email": current, "verified": False, "kind": "unknown",
                 "legit": False, "smtp_check": "skipped"}
            try:
                # Website discovery — always run when needed, even if email
                # is already on file. Cheap and orthogonal to email lookup.
                if need_website:
                    site_url, site_score = find_business_website(
                        name, region, country_guess,
                    )
                # Email lookup only when missing (or when only_missing=False
                # forces a refresh).
                if need_email or not only_missing:
                    enr = deep_find_email(name, region, country_guess)
                    found_email = enr[0] if isinstance(enr, tuple) else ""
                    if found_email:
                        v = verify_lead_email(
                            name, found_email, region, country_guess,
                            skip_search=True,
                        )
                        email_src = enr[1] if isinstance(enr, tuple) else "search"
                    else:
                        v = verify_lead_email(
                            name, current, region, country_guess,
                            deep=True,
                        )
                        email_src = v.get("source", "")
                    email_addr = v.get("email", "")
                else:
                    email_addr = current
                    email_src = "existing"
            except Exception as e:
                log.exception("deep enrich row %d failed", i)
                jobs.append(jid, {"type": "log",
                                  "line": f"  ✗ {name} — {e}"})

            if site_url:
                if update_csv_website(path, name, site_url):
                    websites_found += 1
            if need_email:
                if v.get("verified"):
                    verified += 1
                elif v.get("email"):
                    found += 1
                else:
                    missing += 1
                if v.get("email") or current:
                    ok = update_csv_email(
                        path, name, new_email=v.get("email", current),
                        verified=v.get("verified"),
                        kind=v.get("kind"),
                        legit=v.get("legit"),
                        smtp_check=v.get("smtp_check"),
                    )
                    if ok:
                        persisted += 1
            jobs.append(jid, {
                "type": "progress",
                "i": i, "total": total,
                "name": name,
                "email": email_addr,
                "verified": v.get("verified"),
                "website": site_url,
                "website_score": site_score,
                "verified_count": verified,
                "found_count": found,
                "missing_count": missing,
                "websites_found": websites_found,
                "skipped_count": skipped,
            })
            jobs.append(jid, {
                "type": "log",
                "line": (
                    f"  [{i}/{total}] {name} — "
                    f"site: {('yes (' + (site_url or '') + ', score=' + str(site_score) + ')') if site_url else ('skip' if not need_website else 'none')} · "
                    f"email: {email_addr or 'none'}"
                    + (f" via {email_src}" if email_src else "")
                ),
            })
        result = {
            "total": total,
            "verified": verified,
            "found": found,
            "missing": missing,
            "persisted": persisted,
            "websites_found": websites_found,
            "skipped": skipped,
        }
        jobs.append(jid, {"type": "done", **result})
        jobs.finish(jid, result=result)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


@app.route("/api/bulk-verify-existing", methods=["POST"])
def api_bulk_verify_existing():
    """MX/SMTP-verify only emails already present in the CSV. Skips rows
    with no email and skips DDG search. Persists verification flags back.
    """
    data = request.json or {}
    csv_name = data.get("csv_name", "")
    path = _safe_csv_path(csv_name)
    if not path:
        return jsonify({"error": f"CSV not found: {csv_name}"}), 404

    _, rows = metrics.read_csv_with_scores(path)
    total = len(rows)

    label = f"Verify existing emails · {csv_name}"
    jid = jobs.create_job("bulk_verify_existing", label)

    def worker():
        verified, found, missing, persisted, skipped = 0, 0, 0, 0, 0
        for i, r in enumerate(rows, 1):
            n = r["_normalized"]
            name = n["business_name"]
            current = n["email"]
            region = n["city"]
            if not current or "@" not in current:
                skipped += 1
                jobs.append(jid, {
                    "type": "progress",
                    "i": i, "total": total,
                    "name": name, "email": "", "verified": None,
                    "verified_count": verified,
                    "found_count": found,
                    "missing_count": missing,
                    "skipped_count": skipped,
                })
                continue
            v = verify_lead_email(name, current, region, "", skip_search=True)
            if v.get("verified"):
                verified += 1
            elif v.get("email"):
                found += 1
            else:
                missing += 1
            ok = update_csv_email(
                path, name,
                new_email=v.get("email", current),
                verified=v.get("verified"),
                kind=v.get("kind"),
                legit=v.get("legit"),
                smtp_check=v.get("smtp_check"),
            )
            if ok:
                persisted += 1
            jobs.append(jid, {
                "type": "progress",
                "i": i, "total": total,
                "name": name,
                "email": v.get("email", current),
                "verified": v.get("verified"),
                "verified_count": verified,
                "found_count": found,
                "missing_count": missing,
                "skipped_count": skipped,
            })
        result = {
            "total": total,
            "verified": verified,
            "found": found,
            "missing": missing,
            "skipped": skipped,
            "persisted": persisted,
        }
        jobs.append(jid, {"type": "done", **result})
        jobs.finish(jid, result=result)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


@app.route("/api/fitcheck", methods=["POST"])
def api_fitcheck():
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY required"}), 400
    data = request.json or {}
    business_name = data.get("business_name", "")
    region = data.get("region", "")
    country = data.get("country", "")
    csv_name = data.get("csv_name", "")
    persist = bool(data.get("persist", True))

    if not business_name:
        return jsonify({"error": "business_name required"}), 400

    fit = compute_fit(business_name, region, country)
    persisted = False
    if persist and csv_name and not fit.get("error"):
        path = _safe_csv_path(csv_name)
        if path:
            persisted = update_csv_fit(path, business_name, fit)
    fit["persisted"] = persisted
    return jsonify(fit)


# ─────────────────────────── api: ai ───────────────────────────


@app.route("/api/ai/score-leads", methods=["POST"])
def api_ai_score_leads():
    if not ai_metrics.is_enabled():
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400
    data = request.json or {}
    leads = data.get("leads", [])
    csv_name = data.get("csv_name", "")
    persist = bool(data.get("persist", True))

    scores = ai_metrics.score_leads(leads)

    persisted = 0
    if persist and csv_name and scores:
        path = _safe_csv_path(csv_name)
        if path:
            persisted = _persist_ai_scores(path, leads, scores)

    return jsonify({"scores": scores, "persisted": persisted})


def _persist_ai_scores(csv_path, leads, scores):
    """Write ai_niche_fit + ai_lead_score + ai_score_reason columns."""
    import csv as _csv
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    name_col = next((c for c in ("business_name", "Business Name", "prospect_company_name") if c in fieldnames), None)
    if not name_col:
        return 0
    for col in ("ai_niche_fit", "ai_lead_score", "ai_score_reason"):
        if col not in fieldnames:
            fieldnames.append(col)
    by_name = {(l.get("business_name") or "").strip(): s for l, s in zip(leads, scores)}
    n = 0
    for r in rows:
        s = by_name.get((r.get(name_col) or "").strip())
        if not s:
            continue
        if s.get("niche_fit") is not None or s.get("lead_score") is not None:
            if s.get("niche_fit") is not None:
                r["ai_niche_fit"] = str(s["niche_fit"])
            if s.get("lead_score") is not None:
                r["ai_lead_score"] = str(s["lead_score"])
            r["ai_score_reason"] = s.get("reason", "")
            n += 1
    if n == 0:
        return 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return n


# ─────────────────────────── api: asana ───────────────────────────


@app.route("/api/asana/meta")
def api_asana_meta():
    if not asana_api.is_configured():
        return jsonify({"configured": False, "error": "ASANA_PAT not set in .env"})
    try:
        ws = asana_api.get_workspace()
        projects = asana_api.list_projects(ws["gid"])
        users = asana_api.list_users(ws["gid"])
        return jsonify({
            "configured": True,
            "workspace": ws,
            "projects": [{"gid": p["gid"], "name": p["name"]} for p in projects],
            "users": [{"gid": u["gid"], "name": u.get("name", ""), "email": u.get("email", "")} for u in users],
        })
    except asana_api.AsanaError as e:
        return jsonify({"configured": True, "error": str(e)}), 400


@app.route("/api/asana/task", methods=["POST"])
def api_asana_create_task():
    if not asana_api.is_configured():
        return jsonify({"error": "ASANA_PAT not set"}), 400
    data = request.json or {}
    name = (data.get("name") or "").strip()
    notes = data.get("notes", "")
    project_gid = data.get("project_gid") or None
    assignee_gid = data.get("assignee_gid") or None
    csv_name = data.get("csv_name", "")
    business_name = data.get("business_name", "")
    if not name:
        return jsonify({"error": "task name required"}), 400
    try:
        task = asana_api.create_task(
            name=name, notes=notes,
            project_gid=project_gid, assignee_gid=assignee_gid,
        )
        if task.get("gid"):
            db.log_asana_task(csv_name, business_name, task["gid"], task.get("permalink_url", ""))
        return jsonify({"ok": True, "task": task})
    except asana_api.AsanaError as e:
        return jsonify({"error": str(e)}), 400


# ─────────────────────────── scrapes history ───────────────────────────


@app.route("/scrapes")
def scrapes_history_page():
    status = request.args.get("status")
    return render_template(
        "scrapes.html",
        scrapes=db.list_scrapes(status=status),
        stats=db.scrape_stats(),
        active_filter=status or "all",
        **_ctx(),
    )


@app.route("/api/scrapes")
def api_scrapes_list():
    status = request.args.get("status")
    return jsonify({"scrapes": db.list_scrapes(status=status)})


@app.route("/api/scrapes/<int:sid>/cancel", methods=["POST"])
def api_scrape_cancel(sid):
    """Cooperatively cancel a running scrape. Leads collected so far are
    saved; the rest of the search is discarded. History row transitions
    `running` → `cancelled` once the worker drops out."""
    s = db.get_scrape(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    if s.get("status") != "running":
        return jsonify({"error": f"scrape is {s.get('status')} — nothing to cancel"}), 400
    jid = s.get("job_id")
    if not jid:
        return jsonify({"error": "no job_id on this scrape"}), 400
    ok = jobs.cancel(jid)
    return jsonify({"ok": ok, "job_id": jid, "scrape_id": sid})


@app.route("/api/scrapes/<int:sid>", methods=["DELETE"])
def api_scrape_delete(sid):
    """Remove a scrape_history row. The underlying CSV file is preserved —
    only the audit log entry is removed."""
    s = db.get_scrape(sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    if s.get("status") == "running":
        return jsonify({"error": "scrape still running — cancel from /scrape first"}), 400
    n = db.delete_scrape(sid)
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/scrapes/<int:sid>")
def api_scrape_detail(sid):
    """Return a scrape row + its event log. Live scrapes are stitched from
    the in-memory job buffer so the user sees current progress; finished
    scrapes pull from `events_json` snapshot."""
    s = db.get_scrape(sid)
    if not s:
        return jsonify({"error": "not found"}), 404

    # Live events for running jobs come from in-memory `jobs` module — DB
    # only gets the snapshot on finish.
    events = []
    if s["status"] == "running" and s["job_id"]:
        live = jobs.get(s["job_id"])
        if live:
            events = list(live.get("events", []))
    elif s.get("events_json"):
        try:
            events = json.loads(s["events_json"])
        except Exception:
            events = []

    return jsonify({"scrape": s, "events": events})


# ─────────────────────────── offers + sequences ───────────────────────────


@app.route("/offers")
def offers_page():
    offers = db.list_niche_offers()
    for o in offers:
        md = (o.get("brief_md") or "")
        o["brief_chars"] = len(md)
        o["has_brief"] = bool(md)
    disk_briefs = []
    for fname, niche in (
        ("home-services-offer.md", "Home Services"),
        ("real-estate-offer.md",   "Real Estate"),
    ):
        p = PROJECT_ROOT / fname
        if p.exists():
            disk_briefs.append({
                "filename": p.name,
                "suggested_niche": niche,
                "size": p.stat().st_size,
            })
    return render_template(
        "offers.html",
        offers=offers,
        disk_briefs=disk_briefs,
        niche_presets=list(__import__("src.web.scraper", fromlist=["NICHE_PRESETS"]).NICHE_PRESETS.keys()),
        **_ctx(),
    )


@app.route("/api/offers/brief-file/<path:filename>", methods=["GET", "POST"])
def api_offer_brief_file(filename):
    """GET: return on-disk brief markdown. POST: overwrite it from UI edits.

    Whitelisted to flat .md filenames in the project root so the offers page
    can save edits back to e.g. home-services-offer.md, and so the same file
    is then served fresh to the AI writer (the niche_briefs cache is keyed
    on mtime so it reloads automatically).
    """
    if "/" in filename or ".." in filename or not filename.endswith(".md"):
        return jsonify({"error": "invalid filename"}), 400
    p = PROJECT_ROOT / filename
    if request.method == "POST":
        if not p.exists() or not p.is_file():
            return jsonify({"error": "not found"}), 404
        data = request.json or {}
        md = data.get("markdown", "")
        if not isinstance(md, str):
            return jsonify({"error": "markdown must be a string"}), 400
        if len(md) > 1_000_000:
            return jsonify({"error": "brief too large (>1 MB)"}), 400
        try:
            p.write_text(md, encoding="utf-8")
        except OSError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "filename": filename, "chars": len(md)})

    if not p.exists() or not p.is_file():
        return jsonify({"error": "not found"}), 404
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"filename": filename, "markdown": text, "chars": len(text)})


@app.route("/api/offers", methods=["POST"])
def api_offer_save():
    data = request.json or {}
    niche = (data.get("niche") or "").strip()
    if not niche:
        return jsonify({"error": "niche required"}), 400
    db.upsert_niche_offer(
        niche=niche,
        offer=(data.get("offer") or "").strip(),
        tone=(data.get("tone") or "").strip(),
        loom_url=(data.get("loom_url") or "").strip(),
        brief_md=(data.get("brief_md") or "").strip(),
    )
    return jsonify({"ok": True, "niche": niche})


@app.route("/api/offers/<niche>", methods=["DELETE"])
def api_offer_delete(niche):
    db.delete_niche_offer(niche)
    return jsonify({"ok": True})


@app.route("/sequences")
def sequences_page():
    status = request.args.get("status")
    return render_template(
        "sequences.html",
        sequences=db.list_sequences(status=status),
        stats=db.sequence_stats(),
        active_filter=status or "all",
        all_csvs=metrics.list_csvs(),
        **_ctx(),
    )


@app.route("/api/sequences/<int:sid>")
def api_sequence_detail(sid):
    seq = db.get_sequence(sid)
    if not seq:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "sequence": seq,
        "messages": db.list_sequence_messages(sid),
    })


@app.route("/api/sequences/<int:sid>/pause", methods=["POST"])
def api_sequence_pause(sid):
    db.update_sequence(sid, status="paused", paused_reason="manual",
                       next_send_at=None)
    return jsonify({"ok": True})


@app.route("/api/sequences/<int:sid>/resume", methods=["POST"])
def api_sequence_resume(sid):
    seq = db.get_sequence(sid)
    if not seq:
        return jsonify({"error": "not found"}), 404
    # Resume: schedule next-step send for now.
    if seq["current_step"] >= sequencer.NUM_STEPS:
        db.update_sequence(sid, status="done", next_send_at=None)
    else:
        db.update_sequence(sid, status="active", paused_reason=None,
                           next_send_at=datetime.now().astimezone().isoformat())
    return jsonify({"ok": True})


@app.route("/api/sequences/<int:sid>/cancel", methods=["POST"])
def api_sequence_cancel(sid):
    db.update_sequence(sid, status="cancelled", next_send_at=None)
    return jsonify({"ok": True})


@app.route("/api/sequences/start-from-csv", methods=["POST"])
def api_sequences_start_from_csv():
    """Bulk-enqueue every emailable lead in a CSV into the sequencer."""
    data = request.json or {}
    csv_name = data.get("csv_name", "")
    niche_override = (data.get("niche") or "").strip()
    path = _safe_csv_path(csv_name)
    if not path:
        return jsonify({"error": f"CSV not found: {csv_name}"}), 404

    sender_name = db.get_setting("sender_name", "")
    _, rows = metrics.read_csv_with_scores(path)

    label = f"Enqueue sequence · {csv_name}"
    jid = jobs.create_job("seq_enqueue", label)

    def worker():
        queued, skipped = 0, 0
        reasons = {}
        for r in rows:
            n = r["_normalized"]
            email = (n.get("email") or "").strip().lower()
            if not email or "@" not in email:
                skipped += 1
                reasons["no_email"] = reasons.get("no_email", 0) + 1
                continue
            niche = niche_override or n.get("business_type") or n.get("niche") or ""
            lead = {
                "email": email,
                "business_name": n.get("business_name", ""),
                "city": n.get("city", ""),
                "niche": niche,
                "rating": n.get("rating", 0),
                "review_count": n.get("review_count", 0),
                "website": n.get("website", ""),
            }
            sid, msg = sequencer.enqueue_lead(
                lead, csv_name=csv_name, sender_name=sender_name,
            )
            if sid is None:
                skipped += 1
                reasons[msg] = reasons.get(msg, 0) + 1
            else:
                queued += 1
                jobs.append(jid, {
                    "type": "log",
                    "line": f"  ✓ {lead['business_name']} → seq#{sid} ({msg})",
                })
        result = {"queued": queued, "skipped": skipped, "reasons": reasons}
        jobs.append(jid, {"type": "done", **result})
        jobs.finish(jid, result=result)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


@app.route("/api/sequences/add-lead", methods=["POST"])
def api_sequences_add_lead():
    """Manually enrol a single lead into the 7-step sequence — bypasses CSV.
    Required: email + business_name + niche.  Optional: city, rating,
    review_count, website."""
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    business = (data.get("business_name") or "").strip()
    niche = (data.get("niche") or "").strip()
    if not email or "@" not in email:
        return jsonify({"error": "valid email required"}), 400
    if not business:
        return jsonify({"error": "business_name required"}), 400
    if not niche:
        return jsonify({"error": "niche required (so the AI writer knows the playbook)"}), 400

    lead = {
        "email": email,
        "business_name": business,
        "niche": niche,
        "city": (data.get("city") or "").strip(),
        "rating": float(data.get("rating") or 0),
        "review_count": int(data.get("review_count") or 0),
        "website": (data.get("website") or "").strip(),
    }
    sender_name = db.get_setting("sender_name", "")
    sid, msg = sequencer.enqueue_lead(
        lead,
        csv_name=(data.get("csv_name") or "manual"),
        sender_name=sender_name,
    )
    if sid is None:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "sequence_id": sid, "message": msg})


@app.route("/api/quota")
def api_quota():
    return jsonify(db.send_quota_status(
        daily_cap=int(os.getenv("LEADGEN_DAILY_CAP", "100")),
        monthly_cap=int(os.getenv("LEADGEN_MONTHLY_CAP", "3000")),
    ))


@app.route("/api/sequences/tick", methods=["POST"])
def api_sequences_tick():
    """Manual tick — useful when the scheduler is disabled."""
    return jsonify(sequencer.tick())


@app.route("/api/sequences/check-replies", methods=["POST"])
def api_sequences_check_replies():
    creds_dict = db.load_token()
    if not creds_dict:
        return jsonify({"error": "Gmail not connected"}), 400
    return jsonify(sequencer.process_replies(creds_dict))


# ─────────────────────────── resend webhook ───────────────────────────


@app.route("/api/outreach/live-status")
def api_outreach_live_status():
    """Live polling endpoint for the outreach page. Returns:
      - campaign summary (sent/opens/clicks/replies/bounced + rates)
      - per-email status map for chip updates
      - timestamp of latest webhook event we've applied (for change detection)
    Pulls fresh from Supabase first so the table reflects events that
    arrived since the last 60-second scheduler tick.
    """
    csv_name = (request.args.get("csv") or "").strip()
    pulled = 0
    try:
        from . import supabase_sync
        if supabase_sync.is_configured():
            r = supabase_sync.sync_once()
            pulled = r.get("applied", 0)
    except Exception:
        log.exception("live-status sync_once failed")

    summary = (db.outreach_campaign_summary(csv_name)
               if csv_name else db.outreach_campaign_summary())
    status_map = db.outreach_status_by_email(csv_name) if csv_name else {}
    return jsonify({
        "campaign": summary,
        "status_by_email": status_map,
        "pulled_now": pulled,
    })


@app.route("/api/supabase/sync-leads", methods=["POST"])
def api_supabase_sync_leads():
    """Backfill the Supabase leads_master table from existing CSVs.
    Call once after creating the table; subsequent scrapes auto-sync.
    Body (optional JSON): {"csv": "<basename>"}  — limit to one CSV.
    """
    from . import supabase_leads
    if not supabase_leads.is_configured():
        return jsonify({"error": "Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY)"}), 400
    target = (request.get_json(silent=True) or {}).get("csv", "")
    csvs = metrics.list_csvs()
    if target:
        csvs = [c for c in csvs if c.get("name") == target or Path(c.get("path", "")).name == target]
        if not csvs:
            return jsonify({"error": f"CSV not found: {target}"}), 404
    totals = {"csvs": 0, "rows": 0, "skipped_no_email": 0}
    for c in csvs:
        path = c.get("path")
        if not path:
            continue
        try:
            _, rows = metrics.read_csv_with_scores(path)
        except Exception as e:
            log.warning("backfill read failed %s: %s", path, e)
            continue
        # Filename pattern: interactive_<niche>_<country>.csv → derive tags.
        basename = Path(path).name
        m = re.match(r"interactive_(.+?)_([^_]+)\.csv$", basename)
        niche_slug   = m.group(1).replace("_", " ").replace("-", " ").strip() if m else ""
        country_slug = m.group(2).replace("_", " ").replace("-", " ").strip() if m else ""
        with_email = [r for r in rows if (r.get("_normalized") or {}).get("email")]
        totals["skipped_no_email"] += len(rows) - len(with_email)
        tagged = [{**r, "niche": r.get("niche") or niche_slug,
                   "country": r.get("country") or country_slug}
                  for r in with_email]
        n = supabase_leads.upsert_leads(tagged, source_csv=basename)
        totals["csvs"] += 1
        totals["rows"] += n
    return jsonify({"ok": True, **totals})


@app.route("/api/webhook-status")
def api_webhook_status():
    """Detect any locally-running ngrok tunnel by calling ngrok's local API
    on 127.0.0.1:4040. Returns the public webhook URL + reachability state.
    Also pings our own /webhook/resend through the public URL to confirm
    the round-trip works (so a green check here = Resend can also reach it).
    """
    import json as _json
    import urllib.request
    import urllib.error

    secret_set = bool(os.getenv("RESEND_WEBHOOK_SECRET"))
    out = {
        "ngrok_running": False,
        "public_url": None,
        "webhook_url": None,
        "reachable": False,
        "secret_configured": secret_set,
        "warning": None,
    }
    try:
        req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels")
        with urllib.request.urlopen(req, timeout=2) as r:
            data = _json.loads(r.read().decode("utf-8"))
        tunnels = data.get("tunnels") or []
        # Prefer https tunnel forwarded to our Flask port.
        public = None
        for t in tunnels:
            url = t.get("public_url") or ""
            cfg = (t.get("config") or {}).get("addr", "")
            if url.startswith("https://") and "5001" in cfg:
                public = url
                break
        if not public and tunnels:
            public = tunnels[0].get("public_url")
        if public:
            out["ngrok_running"] = True
            out["public_url"] = public
            out["webhook_url"] = public.rstrip("/") + "/webhook/resend"
    except urllib.error.URLError:
        out["warning"] = "ngrok API not reachable on 127.0.0.1:4040 — start the tunnel"
    except Exception as e:
        out["warning"] = f"ngrok API error: {type(e).__name__}: {e}"

    if out["webhook_url"]:
        try:
            req = urllib.request.Request(out["webhook_url"], method="GET",
                                         headers={"ngrok-skip-browser-warning": "1"})
            with urllib.request.urlopen(req, timeout=4) as r:
                body = _json.loads(r.read().decode("utf-8"))
                out["reachable"] = bool(body.get("ok"))
                out["pong"] = body
        except Exception as e:
            out["reachable"] = False
            out["warning"] = f"reachable from your machine? {type(e).__name__}: {e}"
    if not secret_set:
        msg = "RESEND_WEBHOOK_SECRET unset — webhook will accept ANY payload (insecure)"
        out["warning"] = (out["warning"] + " · " + msg) if out["warning"] else msg
    return jsonify(out)


@app.route("/webhook/resend", methods=["GET", "HEAD", "POST"], strict_slashes=False)
def webhook_resend():
    """Resend webhook receiver with Svix signature verification.

    GET/HEAD return a 200 health pong so Resend's dashboard reachability
    check (and curl/uptime probes through ngrok) see the endpoint as
    reachable instead of 405-ing.

    POST receives the actual signed event. Resend signs every payload via
    Svix. Set `RESEND_WEBHOOK_SECRET` in `.env` to the signing secret from
    the Resend dashboard endpoint detail page (starts with `whsec_`). When
    set, requests missing or with invalid Svix signatures are rejected
    with 401. Leave the env var empty during local testing — the endpoint
    will accept any payload without verification.
    """
    if request.method in ("GET", "HEAD"):
        return jsonify({
            "ok": True,
            "endpoint": "/webhook/resend",
            "method_required": "POST",
            "verification": "svix" if os.getenv("RESEND_WEBHOOK_SECRET") else "disabled",
        })

    secret = os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not secret and not _flask_debug:
        log.warning("RESEND_WEBHOOK_SECRET not configured in production — rejecting webhook")
        return jsonify({"error": "webhook secret not configured"}), 403
    raw = request.get_data()  # MUST be raw bytes — Svix signs the byte string
    log.info("resend webhook POST: bytes=%d secret_configured=%s headers=%s",
             len(raw), bool(secret),
             [h for h in request.headers.keys() if h.lower().startswith("svix")])
    if secret:
        try:
            from svix.webhooks import Webhook, WebhookVerificationError
            wh = Webhook(secret)
            wh.verify(raw, dict(request.headers))
        except WebhookVerificationError as e:
            log.warning("resend webhook signature invalid: %s", e)
            return jsonify({"error": "invalid signature"}), 401
        except ImportError:
            log.error("svix not installed — `pip install svix` then restart")
            return jsonify({"error": "svix not installed"}), 500
        except Exception as e:
            # Malformed signature header (bad base64, missing parts, etc.)
            log.warning("resend webhook signature unparseable: %s", e)
            return jsonify({"error": "invalid signature"}), 401
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    return jsonify(sequencer.record_event(payload))


# ─────────────────────────── auth ───────────────────────────


@app.route("/auth/google")
def auth_google():
    if not _has_oauth_config():
        return ("Google OAuth not configured. Add GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET to .env, then restart."), 400
    flow = _oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    # google_auth_oauthlib uses PKCE by default — the code_verifier generated
    # during authorization_url() must be replayed on /auth/callback.
    if getattr(flow, "code_verifier", None):
        session["oauth_code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    expected_state = session.pop("oauth_state", None)
    received_state = request.args.get("state")
    if not expected_state or expected_state != received_state:
        log.warning("OAuth state mismatch: expected=%s received=%s",
                    expected_state, received_state)
        return "OAuth state mismatch — possible CSRF. Please try again.", 403
    flow = _oauth_flow()
    cv = session.pop("oauth_code_verifier", None)
    if cv:
        flow.code_verifier = cv
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    db.save_token({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    })
    return redirect(url_for("dashboard"))


@app.route("/auth/disconnect")
def auth_disconnect():
    db.clear_token()
    return redirect(url_for("dashboard"))


# ─────────────────────────── main ───────────────────────────


if __name__ == "__main__":
    if not GOOGLE_PLACES_API_KEY:
        print("WARNING: GOOGLE_PLACES_API_KEY not set in .env")
    if not _has_oauth_config():
        print("WARNING: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — Sheets + Gmail disabled")
    if not ANTHROPIC_API_KEY:
        print("INFO: ANTHROPIC_API_KEY not set — outreach will use static template")
    # use_reloader=False: Werkzeug's file-watcher restarts the whole process
    # on any source save, which kills in-flight background jobs (Send All,
    # scrape, bulk verify). Override with LEADGEN_RELOAD=1 if you want the
    # auto-restart back while editing UI templates.
    use_reloader = os.getenv("LEADGEN_RELOAD", "0") in ("1", "true", "yes")
    app.run(debug=_flask_debug, port=5001, use_reloader=use_reloader)
