"""Lead-gen dashboard — Flask app.

Run: `python -m src.web.app`  →  http://localhost:5001
"""
import csv
import json
import os
from datetime import datetime
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
from .enrich_runner import run_enrich_stream
from .gmail import check_replies as gmail_check_replies
from . import resend_send
from .email_compose import compose as compose_email
from .personalize import draft_email
from .scraper import COUNTRY_CITIES, COUNTRY_REGION_CODES, COUNTRY_STATES, NICHE_PRESETS, run_search
from .scraper_runner import list_scrapers, run_scraper_stream
from .sheets import SCOPES, add_tab, ensure_master_spreadsheet, update_tab
from .verify import verify_lead_email, update_csv_email
from .fitcheck import compute_fit, update_csv_fit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

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
    "Business Name", "City", "Address", "Phone", "Email", "Email Verified",
    "Rating", "Reviews", "Business Type", "Google Maps URL",
]


def _save_interactive_csv(leads, niche, country):
    """Append leads to data/outputs/interactive_<niche>_<country>.csv.
    Creates the file with a header row if it doesn't exist yet.
    Returns the absolute path written.
    """
    if not leads:
        return None
    basename = f"interactive_{_slug(niche)}_{_slug(country)}.csv"
    path = PROJECT_ROOT / "data" / "outputs" / basename
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INTERACTIVE_FIELDNAMES, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for l in leads:
            writer.writerow({k: l.get(k, "") for k in INTERACTIVE_FIELDNAMES})
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
    settings = db.get_settings()
    outreach = db.outreach_stats()
    summary = metrics.dashboard_summary(settings, outreach)
    recent = db.list_outreach(limit=10)
    cached_forecast = db.load_forecast("dashboard")
    return render_template(
        "dashboard.html",
        summary=summary,
        recent=recent,
        settings=settings,
        cached_forecast=cached_forecast,
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
    if selected_path:
        fieldnames, rows = metrics.read_csv_with_scores(selected_path)
        rows = [r for r in rows if r["_normalized"]["email"]]
    log = db.list_outreach(limit=50)
    return render_template(
        "outreach.html",
        all_csvs=metrics.list_csvs(),
        selected=csv_name,
        rows=rows,
        log=log,
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

    def worker():
        for line in run_scraper_stream(key):
            jobs.append(jid, {"type": "log", "line": line})
        jobs.append(jid, {"type": "done"})
        jobs.finish(jid, result={"key": key})

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


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
        ):
            if verify_emails:
                v = verify_lead_email(
                    business_name=lead.get("Business Name", ""),
                    current_email="",
                    region=lead.get("City", ""),
                    country=country,
                )
                if not v.get("email"):
                    # Drop leads with no email when the user asked for emails-only.
                    skipped_no_email += 1
                    jobs.append(jid, {
                        "type": "log",
                        "line": f"  ↷ skip {lead.get('Business Name', '')} — no email found",
                    })
                    if len(leads) >= target_leads:
                        break
                    continue
                lead["Email"] = v["email"]
                lead["Email Verified"] = "yes" if v.get("verified") else "found"

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
        }
        jobs.append(jid, {"type": "done", **{k: v for k, v in result.items() if k != "leads"}})
        jobs.finish(jid, result=result)

    jobs.run_in_thread(jid, worker)
    return jsonify({"job_id": jid, "label": label})


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
    sender = db.get_setting("sender_name", "") or ""
    drafts = []
    for lead in leads:
        norm = metrics.normalize_lead(lead) if not lead.get("_normalized") else lead.get("_normalized")
        d = draft_email(norm, sender_name=sender)
        d["lead"] = norm
        drafts.append(d)
    return jsonify({"drafts": drafts, "personalized_engine": "claude" if ANTHROPIC_API_KEY else "template"})


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
            db.log_outreach(to_addr, business_name, csv_path, subject, body,
                            gmail_message_id=f"resend:{mid}", status="sent")
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


@app.route("/api/ai/forecast", methods=["GET", "POST"])
def api_ai_forecast():
    if not ai_metrics.is_enabled():
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400

    if request.method == "GET":
        cached = db.load_forecast("dashboard")
        return jsonify(cached or {"forecast": None})

    settings = db.get_settings()
    outreach = db.outreach_stats()
    summary = metrics.dashboard_summary(settings, outreach)
    forecast = ai_metrics.forecast_pipeline(summary["per_csv"])
    if not forecast.get("error"):
        db.save_forecast("dashboard", forecast)
    return jsonify({"forecast": forecast})


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
