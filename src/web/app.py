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
from . import metrics
from .enrich_runner import run_enrich_stream
from .gmail import send_email
from . import resend_send
from .personalize import draft_email
from .scraper import COUNTRY_CITIES, COUNTRY_REGION_CODES, NICHE_PRESETS, run_search
from .scraper_runner import list_scrapers, run_scraper_stream
from .sheets import SCOPES, create_sheet_and_write, update_sheet_values
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


def _safe_csv_path(name):
    """Resolve a CSV name to a path inside data/imports or data/outputs."""
    name = Path(name).name  # strip dir traversal
    for d in (PROJECT_ROOT / "data" / "outputs", PROJECT_ROOT / "data" / "imports"):
        p = d / name
        if p.exists():
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
        for key in ("close_rate", "avg_deal_value", "email_signature", "sender_name"):
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


# ─────────────────────────── api: scrapers ───────────────────────────


@app.route("/api/cities/<country>")
def api_cities(country):
    return jsonify(COUNTRY_CITIES.get(country, []))


@app.route("/api/niche-types/<niche>")
def api_niche_types(niche):
    return jsonify(NICHE_PRESETS.get(niche, []))


@app.route("/api/scrape/canonical/<key>", methods=["POST"])
def api_scrape_canonical(key):
    def stream():
        for line in run_scraper_stream(key):
            yield _sse({"type": "log", "line": line})
        yield _sse({"type": "done"})

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/scrape/interactive", methods=["POST"])
def api_scrape_interactive():
    if not GOOGLE_PLACES_API_KEY:
        return jsonify({"error": "GOOGLE_PLACES_API_KEY not set in .env"}), 400

    data = request.json or {}
    country = data.get("country", "US")
    cities = data.get("cities") or COUNTRY_CITIES.get(country, [])
    business_types = data.get("business_types", [])
    min_reviews = int(data.get("min_reviews", 50))
    min_rating = float(data.get("min_rating", 4.0))
    target_leads = int(data.get("target_leads", 100))
    require_no_website = bool(data.get("require_no_website", False))
    verify_emails = bool(data.get("verify_emails", False))
    region_code = COUNTRY_REGION_CODES.get(country, "US")

    if not business_types:
        return jsonify({"error": "No business types selected"}), 400

    def stream():
        leads = []
        for lead, progress in run_search(
            cities=cities,
            business_types=business_types,
            api_key=GOOGLE_PLACES_API_KEY,
            region_code=region_code,
            min_reviews=min_reviews,
            min_rating=min_rating,
            target_leads=target_leads,
            require_no_website=require_no_website,
        ):
            if verify_emails:
                v = verify_lead_email(
                    business_name=lead.get("Business Name", ""),
                    current_email="",
                    region=lead.get("City", ""),
                    country=country,
                )
                lead["Email"] = v.get("email", "")
                lead["Email Verified"] = "yes" if v.get("verified") else ("found" if v.get("email") else "no")
            leads.append(lead)
            yield _sse({"type": "lead", "lead": lead, "progress": progress})
        yield _sse({"type": "done", "total": len(leads), "leads": leads})

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    def stream():
        for line in run_enrich_stream(path, keep_with_website=keep_with_website, skip_email=skip_email):
            yield _sse({"type": "log", "line": line})
        yield _sse({"type": "done"})

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@app.route("/api/outreach/send", methods=["POST"])
def api_outreach_send():
    creds = _google_creds()
    if not creds:
        return jsonify({"error": "Google not connected — visit /auth/google first"}), 401

    data = request.json or {}
    sends = data.get("sends", [])
    csv_path = data.get("csv_path", "")
    signature = db.get_setting("email_signature", "")

    results = []
    for s in sends:
        to_addr = s.get("to") or s.get("email")
        subject = s.get("subject", "")
        body = s.get("body", "")
        business_name = s.get("business_name", "")
        if not (to_addr and subject and body):
            results.append({"to": to_addr, "ok": False, "error": "missing fields"})
            continue
        try:
            mid = send_email(creds, to_addr, subject, body, signature)
            db.log_outreach(to_addr, business_name, csv_path, subject, body,
                            gmail_message_id=mid, status="sent")
            results.append({"to": to_addr, "ok": True, "message_id": mid})
        except Exception as e:
            db.log_outreach(to_addr, business_name, csv_path, subject, body,
                            gmail_message_id=None, status="failed")
            results.append({"to": to_addr, "ok": False, "error": str(e)})
    return jsonify({"results": results})


@app.route("/api/outreach/send-resend", methods=["POST"])
def api_outreach_send_resend():
    if not resend_send.is_configured():
        return jsonify({"error": "Resend not configured. Set RESEND_API_KEY and RESEND_FROM in .env"}), 400

    data = request.json or {}
    sends = data.get("sends", [])
    csv_path = data.get("csv_path", "")
    signature = db.get_setting("email_signature", "")

    results = []
    for s in sends:
        to_addr = s.get("to") or s.get("email")
        subject = s.get("subject", "")
        body = s.get("body", "")
        business_name = s.get("business_name", "")
        if not (to_addr and subject and body):
            results.append({"to": to_addr, "ok": False, "error": "missing fields"})
            continue
        try:
            mid = resend_send.send_email(to_addr, subject, body, signature)
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
    creds = _google_creds()
    if not creds:
        return jsonify({"error": "Google not connected"}), 401

    data = request.json or {}
    name = data.get("name", "")
    leads = data.get("leads")  # optional override (interactive search)
    fieldnames = data.get("fieldnames")
    force_new = bool(data.get("force_new", False))

    if not leads and name:
        path = _safe_csv_path(name)
        if not path:
            return jsonify({"error": f"CSV not found: {name}"}), 404
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            leads = list(reader)

    if not leads:
        return jsonify({"error": "No leads to export"}), 400

    existing = db.get_sheet_for_csv(name) if (name and not force_new) else None
    title = data.get("title") or f"Leads — {name or 'Search'} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    try:
        if existing:
            try:
                url = update_sheet_values(creds, existing["sheet_id"], leads, fieldnames=fieldnames)
                db.set_sheet_for_csv(name, existing["sheet_id"], url)
                return jsonify({"url": url, "synced": True})
            except Exception:
                pass  # sheet may have been deleted; fall through to create new
        sheet_id, url = create_sheet_and_write(creds, title, leads, fieldnames=fieldnames)
        if name:
            db.set_sheet_for_csv(name, sheet_id, url)
        return jsonify({"url": url, "synced": False, "created": True})
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
    if persist and csv_name and result.get("email") and result["email"] != current_email:
        path = _safe_csv_path(csv_name)
        if path:
            persisted = update_csv_email(path, business_name, result["email"])
    result["persisted"] = persisted
    return jsonify(result)


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
    """Write ai_score + ai_score_reason columns to the matching rows in csv."""
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
    for col in ("ai_score", "ai_score_reason"):
        if col not in fieldnames:
            fieldnames.append(col)
    by_name = {(l.get("business_name") or "").strip(): s for l, s in zip(leads, scores)}
    n = 0
    for r in rows:
        s = by_name.get((r.get(name_col) or "").strip())
        if s and s.get("score") is not None:
            r["ai_score"] = str(s["score"])
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
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = _oauth_flow()
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
