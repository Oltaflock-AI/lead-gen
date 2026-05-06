# OltaFlock Lead-Gen

A Flask app for scraping local-business leads, enriching them with AI, and running multi-step email outreach campaigns through Resend. Built as the internal tool for OltaFlock AI.

This README walks a brand-new teammate from a fresh Mac/Linux laptop to a running dashboard with their own sender identity, in about 15 minutes.

---

## What this app does

1. **Scrape** business leads (real estate, home services, law firms, etc.) from Google Places and DuckDuckGo by city + niche.
2. **Enrich** each lead with verified emails, AI fit-score, and personalized signals.
3. **Compose** outreach emails using Claude (Anthropic) against per-niche offer briefs.
4. **Send** via Resend with timezone-aware scheduling, daily/monthly caps, and a 5-step drip sequencer.
5. **Track** opens, clicks, replies, and bounces through a Resend webhook → Supabase buffer → SQLite pipeline.
6. **Dashboard** shows funnel metrics, per-CSV stats, sequence progress, and full outreach logs.

Everything runs locally against a SQLite file at `data/outreach.db`. No production deployment is required to use it.

---

## Prerequisites

You need:

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) or `brew install python@3.12` |
| Git | any | `brew install git` |
| A Resend account | free tier works | [resend.com](https://resend.com) |
| An Anthropic API key | pay-as-you-go | [console.anthropic.com](https://console.anthropic.com) |
| A Google Cloud project | free | [console.cloud.google.com](https://console.cloud.google.com) — only if you want to scrape new leads |

Optional but recommended:

- A **Supabase** project (free) — required only if you want webhook event ingestion (open/click/bounce tracking).
- An **Asana** Personal Access Token — only if you want to push leads into Asana.

---

## 1. Clone and install

```bash
git clone <repo-url> lead-gen
cd lead-gen

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

That installs Flask, the Resend SDK, Anthropic SDK, Google API client, dnspython, and a few other things listed in `requirements.txt`.

---

## 2. Create your `.env` file

Copy the example and fill it in:

```bash
cp .env.example .env
```

Then open `.env` in your editor. Below is every variable the app reads, grouped by what you actually need.

### Required (the app won't send email without these)

```bash
# Resend — your sending account
RESEND_API_KEY=re_your_key_here
RESEND_FROM="Your Name <you@yourdomain.com>"

# Anthropic — for AI-generated emails and lead scoring
ANTHROPIC_API_KEY=sk-ant-your_key_here
```

How to get them:

- **Resend**: sign up → [resend.com/api-keys](https://resend.com/api-keys) → "Create API key" with full access. Then go to **Domains**, add the domain you'll send from, and verify the DNS records. Until the domain is verified, you can only send to addresses on the same account.
- **Anthropic**: [console.anthropic.com](https://console.anthropic.com) → API keys → "Create key". Add a small amount of credit ($5 is plenty for testing).

### Required if you want to scrape new leads

```bash
GOOGLE_PLACES_API_KEY=your_places_key
```

Enable the **Places API (New)** and **Geocoding API** in your Google Cloud project, then create an API key under *APIs & Services → Credentials*. The app uses this for the "Scrape" tab.

### Optional — Gmail OAuth (only if you want to send via Gmail instead of Resend)

```bash
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OAUTH_REDIRECT_URI=http://localhost:5001/auth/callback
```

Most teammates can skip this — Resend is the default.

### Optional — Supabase (event tracking)

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=eyJ...service_role_key
RESEND_WEBHOOK_SECRET=whsec_from_resend
```

Set these only if you want open/click/bounce tracking. The webhook flow lives in `supabase/functions/` and writes to a `resend_events` table (schema in `supabase/migrations/001_resend_events.sql`). Without Supabase, sends still work — you just won't see delivery events in the dashboard.

### Optional — Asana

```bash
ASANA_PAT=your_personal_access_token
ASANA_WORKSPACE_GID=your_workspace_gid
```

Only needed if you want the "Push to Asana" buttons to work.

### Tunables (sane defaults — edit only if you know why)

```bash
LEADGEN_DAILY_CAP=100              # max sends per day
LEADGEN_MONTHLY_CAP=3000           # max sends per month
LEADGEN_DEFAULT_TZ=America/New_York # default lead timezone
LEADGEN_DEFAULT_SEND_HOUR=10       # 10am local time
LEADGEN_SEND_WEEKDAYS=1,2,3        # Mon=1 ... Sun=7 (sends only Mon/Tue/Wed by default)
LEADGEN_SCHEDULER=1                # set to 0 to disable the background sender
LEADGEN_REPLY_SCAN_SEC=900         # how often to scan for replies (15 min)
CLAUDE_MODEL=claude-sonnet-4-6     # main model
CLAUDE_MODEL_FAST=claude-haiku-4-5-20251001  # batch model
LOG_LEVEL=INFO
FLASK_SECRET_KEY=                  # auto-generated if blank
```

Save `.env` — it's gitignored and stays local.

---

## 3. Set your sender identity (replace "Khush")

The dashboard ships with `Khush Mutha / Founder / OltaFlock AI` as the default signature. **You need to change this to your own name before sending anything.**

You have two ways:

### Option A — through the UI (recommended)

1. Start the app (next section).
2. Visit [http://localhost:5001/settings](http://localhost:5001/settings).
3. Update:
   - **Sender name** — your full name (e.g. "Jane Smith")
   - **Sender title** — your role (e.g. "Founder", "Growth Lead")
   - **Company name** — your company
   - **Website URL** — used in email footers
   - **Booking URL** — your Cal.com / Calendly link
4. Click **Save**. Settings are stored in `data/outreach.db` and used by every outgoing email.

Also make sure `RESEND_FROM` in `.env` matches the address you actually want to send from — that's the literal `From:` header. The signature in the email body comes from the settings above.

### Option B — directly in the DB

If you'd rather seed it before the first run:

```bash
sqlite3 data/outreach.db "UPDATE settings SET value='Jane Smith' WHERE key='sender_name';"
sqlite3 data/outreach.db "UPDATE settings SET value='Founder' WHERE key='sender_title';"
sqlite3 data/outreach.db "UPDATE settings SET value='Acme Co' WHERE key='company_name';"
```

(The DB is created on first launch — run the app once first if the file doesn't exist yet.)

---

## 4. Run the app

```bash
source .venv/bin/activate    # if not already active
python -m src.web.app
```

You should see:

```
 * Running on http://127.0.0.1:5001
```

Open [http://localhost:5001](http://localhost:5001) in your browser. The dashboard loads with empty stats on a fresh DB.

The background scheduler (which actually sends queued emails on the right cadence) starts automatically. Set `LEADGEN_SCHEDULER=0` in `.env` if you want to disable it for testing.

---

## 5. Your first campaign — end to end

A typical workflow:

1. **Scrape** — go to `/scrape`, pick a niche (e.g. "real_estate"), country, and city. Hit run. Results land in `data/outputs/<scrape>.csv`.
2. **Verify & enrich** — open the CSV from `/leads`, click the lead detail page, and use the verify/enrich buttons. The app pulls websites, finds emails, runs SMTP/DNS checks, and computes an AI fit score.
3. **Pick an offer brief** — at `/offers`, attach the right offer markdown (e.g. `home-services-offer.md`) to the niche.
4. **Draft & refine** — on a lead's detail page, click "Draft outreach". Claude writes a personalized email using the offer brief + the lead's signals. Edit inline if needed.
5. **Send** — from `/outreach`, select leads and hit "Send". The scheduler respects daily caps, send-window weekdays, and per-lead timezones.
6. **Sequencer** — leads who don't reply get auto-enrolled into a 5-step drip. You can pause or remove anyone from `/sequences`.
7. **Track** — `/` (dashboard) shows funnel, per-niche stats, and recent activity. `/outreach` has the full send log.

> **Important rule from team policy:** never test sends against real lead addresses. Use `admin@oltaflock.ai` or any `*@resend.dev` sandbox address while you're learning the UI. Real sends only after verifying templates render correctly.

---

## 6. Optional — wire up Resend webhooks (open/click/bounce tracking)

Skip this section unless you need event tracking.

1. Deploy the Supabase Edge Function in `supabase/functions/` (it receives the webhook and writes to `resend_events`).
2. Run the migration: `supabase db push` (or paste `supabase/migrations/001_resend_events.sql` into the SQL editor).
3. In Resend → Webhooks, add an endpoint pointing to your Supabase function URL. Copy the signing secret.
4. Put the secret in your `.env` as `RESEND_WEBHOOK_SECRET`.
5. The Flask scheduler polls Supabase every minute and ingests new events into local SQLite.

---

## Project layout

```
lead-gen/
├── src/
│   ├── scrapers/        # one module per niche (real_estate, home_services, law_firms…)
│   ├── processors/      # enrich_leads.py — email/SMTP/DNS verification + AI scoring
│   └── web/             # Flask app
│       ├── app.py             # routes + entrypoint
│       ├── db.py              # SQLite schema, settings, outreach log
│       ├── sequencer.py       # 5-step drip + reply-scan loop
│       ├── resend_send.py     # Resend integration
│       ├── email_compose.py   # Claude-driven email drafting
│       ├── personalize.py     # per-lead signal extraction
│       ├── send_timing.py     # timezone + business-hour logic
│       ├── supabase_sync.py   # webhook event ingestion
│       └── templates/         # Jinja2 HTML for the dashboard
├── data/
│   ├── outreach.db      # all state — leads, outreach log, settings, sequences
│   ├── imports/         # CSVs you upload manually
│   └── outputs/         # CSVs generated by the scrapers
├── supabase/            # webhook edge function + migrations
├── scripts/             # one-off helpers (e.g. test_sequence_preview.py)
├── tests/
├── home-services-offer.md   # offer brief used by the AI composer
├── real-estate-offer.md
├── requirements.txt
└── .env.example
```

`AGENTS.md` documents the file-naming conventions if you're contributing code.

---

## Common problems

**`ModuleNotFoundError: No module named 'src'`**
You're not running from the project root, or the venv isn't activated. Run `source .venv/bin/activate && python -m src.web.app` from the lead-gen folder.

**`Resend domain not verified`**
You can only send to addresses on your Resend account until the sending domain's DNS records are verified. Check Resend → Domains.

**Emails sit in queue, never send**
- Check the daily/monthly caps in `.env` — you may have hit them.
- Check `LEADGEN_SEND_WEEKDAYS` — by default, sends only fire Mon/Tue/Wed.
- Check the lead's local time — the scheduler waits for `LEADGEN_DEFAULT_SEND_HOUR` in their timezone.
- Tail the log: `tail -f logs/lead-gen.log`.

**`401 unauthorized` from Anthropic**
Your `ANTHROPIC_API_KEY` is missing or expired. Re-issue at console.anthropic.com.

**Dashboard shows old "Khush Mutha" name in emails**
You set the `From` address in `.env` but didn't update the in-app settings. Visit `/settings` and save your name/title/company there — those drive the signature inside the email body.

**Port 5001 already in use**
Another process is bound to it. Either kill it (`lsof -i :5001` then `kill <pid>`) or change the port in `src/web/app.py` (last line, `app.run(..., port=5001)`).

---

## Updating

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt
python -m src.web.app
```

The DB schema auto-migrates on startup; your settings, leads, and outreach log are preserved.

---

## Getting help

- Code conventions and contribution rules live in `AGENTS.md`.
- The Claude Code config for this repo is in `CLAUDE.md`.
- For anything else, ping the team on Slack or DM Khush.
