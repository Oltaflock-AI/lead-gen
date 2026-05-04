import os
import json
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, session, url_for, Response, stream_with_context
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

from scraper import run_search, COUNTRY_CITIES, COUNTRY_REGION_CODES, NICHE_PRESETS
from sheets import create_sheet_and_write, SCOPES

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Allow HTTP for local dev OAuth
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def get_oauth_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:5001/auth/callback"],
            }
        },
        scopes=SCOPES,
        redirect_uri="http://localhost:5001/auth/callback",
    )


@app.route("/")
def index():
    google_connected = "google_creds" in session
    return render_template(
        "index.html",
        countries=list(COUNTRY_CITIES.keys()),
        niches=list(NICHE_PRESETS.keys()),
        google_connected=google_connected,
        has_oauth_config=bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    )


@app.route("/api/cities/<country>")
def get_cities(country):
    cities = COUNTRY_CITIES.get(country, [])
    return jsonify(cities)


@app.route("/api/niche-types/<niche>")
def get_niche_types(niche):
    types = NICHE_PRESETS.get(niche, [])
    return jsonify(types)


@app.route("/search", methods=["POST"])
def search():
    data = request.json
    country = data.get("country", "US")
    cities = data.get("cities", COUNTRY_CITIES.get(country, []))
    business_types = data.get("business_types", [])
    min_reviews = int(data.get("min_reviews", 50))
    min_rating = float(data.get("min_rating", 4.0))
    target_leads = int(data.get("target_leads", 100))
    region_code = COUNTRY_REGION_CODES.get(country, "US")

    if not business_types:
        return jsonify({"error": "No business types selected"}), 400

    def generate():
        leads = []
        for lead, progress in run_search(
            cities=cities,
            business_types=business_types,
            api_key=GOOGLE_PLACES_API_KEY,
            region_code=region_code,
            min_reviews=min_reviews,
            min_rating=min_rating,
            target_leads=target_leads,
        ):
            leads.append(lead)
            event_data = {"type": "lead", "lead": lead, "progress": progress}
            yield f"data: {json.dumps(event_data)}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'total': len(leads)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({"error": "Google OAuth not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env"}), 400
    flow = get_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = get_oauth_flow()
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["google_creds"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    return redirect(url_for("index"))


@app.route("/auth/disconnect")
def auth_disconnect():
    session.pop("google_creds", None)
    return redirect(url_for("index"))


@app.route("/export-sheets", methods=["POST"])
def export_sheets():
    cred_data = session.get("google_creds")
    if not cred_data:
        return jsonify({"error": "Google Sheets not connected"}), 401

    data = request.json
    leads = data.get("leads", [])
    if not leads:
        return jsonify({"error": "No leads to export"}), 400

    title = f"Leads - {data.get('niche', 'Search')} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    try:
        sheet_url = create_sheet_and_write(cred_data, title, leads)
        return jsonify({"url": sheet_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    if not GOOGLE_PLACES_API_KEY:
        print("WARNING: GOOGLE_PLACES_API_KEY not set in .env")
    app.run(debug=True, port=5001)
