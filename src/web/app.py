"""Lead-gen dashboard — Flask app.

Run: `python -m src.web.app`  →  http://localhost:5001
"""
import csv
import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template, request,
    session, stream_with_context, url_for,
)
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
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5001/auth/callback")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "")

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

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


def _ctx():
    """Common template context — available to every page."""
    return {
        "google_connected": _google_creds() is not None,
        "has_oauth_config": _has_oauth_config(),
        "claude_enabled": ai_metrics.is_enabled(),
        "asana_enabled": asana_api.is_configured(),
        "resend_enabled": resend_send.is_configured(),
    }


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


# ─────────────────────────── pages ───────────────────────────


@app.route("/")
def dashboard():
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    settings = db.get_settings()
    outreach = db.outreach_stats()
    summary = metrics.dashboard_summary(settings, outreach)
    recent = db.list_outreach(limit=10)

    # Sent today / niches today (from outreach log).
    today_prefix = _dt.utcnow().strftime("%Y-%m-%d")
    sent_today_rows = [r for r in db.list_outreach(limit=500) if (r.get("sent_at") or "").startswith(today_prefix)]
    summary["sent_today"] = len(sent_today_rows)
    summary["niches_today"] = 0  # outreach_log lacks niche column; placeholder

    # Today's queue — active sequences with next_send_at in next 24h.
    now = _dt.now(_tz.utc)
    horizon = now + _td(hours=24)
    active_seq = db.list_sequences(status="active", limit=500)
    today_queue = []
    step_labels = {1: "Cold", 2: "Bump", 3: "Loom", 4: "Breakup"}
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
            today_queue.append({
                "when": ts.strftime("%H:%M"),
                "business_name": s.get("business_name"),
                "lead_email": s.get("lead_email"),
                "niche": s.get("niche"),
                "step": (s.get("current_step") or 0) + 1,
                "step_label": step_labels.get((s.get("current_step") or 0) + 1, ""),
            })
    today_queue.sort(key=lambda q: q["when"])
    summary["queued_today"] = len(today_queue)

    today_label = _dt.utcnow().strftime("%A, %b %-d")

    return render_template(
        "dashboard.html",
        summary=summary,
        recent=recent,
        settings=settings,
        today_queue=today_queue[:20],
        today_label=today_label,
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


@app.route("/leads/<name>")
def leads_detail(name):
    path = _safe_csv_path(name)
    if not path:
        return redirect(url_for("leads_index"))
    fieldnames, rows = metrics.read_csv_with_scores(path)
    summary = metrics.csv_summary(path)
    contacted = db.outreach_stats()["contacted_emails"]
    for r in rows:
        r["_contacted"] = r["_normalized"]["email"] in contacted if r["_normalized"]["email"] else False
    asana_for_rows = db.list_asana_tasks_for_csv(name)
    for r in rows:
        bn = r["_normalized"]["business_name"]
        r["_asana_url"] = asana_for_rows.get(bn, "")
    return render_template(
        "leads_detail.html",
        name=name,
        path=str(path),
        fieldnames=fieldnames,
        rows=rows,
        summary=summary,
        sheet=db.get_sheet_for_csv(name),
        **_ctx(),
    )


@app.route("/outreach")
def outreach_page():
    csv_name = request.args.get("csv", "")
    selected_path = _safe_csv_path(csv_name) if csv_name else None
    rows = []
    fieldnames = []
    drafts_by_email = {}
    if selected_path:
        fieldnames, rows = metrics.read_csv_with_scores(selected_path)
        rows = [r for r in rows if r["_normalized"]["email"]]
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
            for line in run_scraper_stream(key):
                jobs.append(jid, {"type": "log", "line": line})
            jobs.append(jid, {"type": "done"})
            jobs.finish(jid, result={"key": key})
            j = jobs.get(jid)
            db.record_scrape_finish(
                scrape_id, "done",
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

    # When verifying emails, we need to OVER-scrape because many leads will not
    # have a discoverable email. Search a larger pool, but stop appending once
    # we've collected `target_leads` rows that actually carry an email.
    internal_target = target_leads * 6 if verify_emails else target_leads
    internal_target = min(internal_target, 1000)

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

        csv_path = _save_interactive_csv(leads, niche, country)
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
        jobs.append(jid, {"type": "done", **{k: v for k, v in result.items() if k != "leads"}})
        jobs.finish(jid, result=result)

        # Persist a snapshot of this scrape so it survives Flask restart.
        db.record_scrape_finish(
            scrape_id, "done",
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

    out = [None] * len(norms)
    for i, n in enumerate(norms):
        em = (n.get("email") or "").lower()
        if em in cached and not force:
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
    """All sending goes through Resend. Gmail is read-only (reply detection)."""
    if not resend_send.is_configured():
        return jsonify({"error": "Resend not configured. Set RESEND_API_KEY and RESEND_FROM in .env"}), 400

    data = request.json or {}
    sends = data.get("sends", [])
    csv_path = data.get("csv_path", "")
    settings = db.get_settings()

    results = []
    for s in sends:
        to_addr = s.get("to") or s.get("email")
        subject = s.get("subject", "")
        body = s.get("body", "")
        business_name = s.get("business_name", "")
        if not (to_addr and subject and body):
            results.append({"to": to_addr, "ok": False, "error": "missing fields"})
            continue
        text_body, html_body = compose_email(body, settings)
        try:
            mid = resend_send.send_email(to_addr, subject, text_body, html_body)
            db.log_outreach(
                to_addr, business_name, csv_path, subject, body,
                gmail_message_id=f"resend:{mid}", status="sent",
                resend_id=mid,
            )
            results.append({"to": to_addr, "ok": True, "message_id": mid})
        except Exception as e:
            db.log_outreach(to_addr, business_name, csv_path, subject, body,
                            gmail_message_id=None, status="failed")
            results.append({"to": to_addr, "ok": False, "error": str(e)})
    return jsonify({"results": results})


@app.route("/api/outreach/mark-replied/<int:log_id>", methods=["POST"])
def api_mark_replied(log_id):
    import sqlite3
    with sqlite3.connect(db.DB_PATH) as c:
        c.execute("UPDATE outreach_log SET status = 'replied' WHERE id = ?", (log_id,))
    return jsonify({"ok": True})


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
    home_md = (PROJECT_ROOT / "home-services-offer.md")
    if home_md.exists():
        disk_briefs.append({
            "filename": home_md.name,
            "suggested_niche": "Home Services",
            "size": home_md.stat().st_size,
        })
    return render_template(
        "offers.html",
        offers=offers,
        disk_briefs=disk_briefs,
        niche_presets=list(__import__("src.web.scraper", fromlist=["NICHE_PRESETS"]).NICHE_PRESETS.keys()),
        **_ctx(),
    )


@app.route("/api/offers/brief-file/<path:filename>")
def api_offer_brief_file(filename):
    """Return contents of an on-disk brief markdown for the offers UI to load."""
    if "/" in filename or ".." in filename or not filename.endswith(".md"):
        return jsonify({"error": "invalid filename"}), 400
    p = PROJECT_ROOT / filename
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
    app.run(debug=True, port=5001)
