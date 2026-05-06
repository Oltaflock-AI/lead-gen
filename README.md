# lead-gen

OltaFlock outbound lead-generation pipeline. Scrapes local-business leads from
Google Places, enriches them with contact + AI fit signals, drafts
hyper-personalized cold emails, and runs a 5-step Resend drip sequence with
Gmail reply detection.

Single-tenant Flask app on port `5001`. Designed to run on a laptop with a
Supabase Edge Function catching webhooks while the laptop is offline.

---

## High-Level Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              Flask app (port 5001)          │
                    │  src/web/app.py  — UI + REST API             │
                    └─────────────────────────────────────────────┘
                          │            │            │
              ┌───────────┘            │            └────────────┐
              ▼                        ▼                         ▼
   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
   │  Scrape & Enrich   │   │  Personalize &     │   │  Send & Track      │
   │  scraper.py        │   │  Compose           │   │  resend_send.py    │
   │  enrich_leads.py   │   │  personalize.py    │   │  sequencer.py      │
   │  verify.py         │   │  email_compose.py  │   │  gmail.py          │
   │  fitcheck.py       │   │  niche_briefs.py   │   │                    │
   └────────────────────┘   └────────────────────┘   └────────────────────┘
              │                        │                         │
              └────────────────────────┼─────────────────────────┘
                                       ▼
                       ┌──────────────────────────────┐
                       │  SQLite  data/outreach.db    │
                       │  + per-niche CSV outputs     │
                       │  in data/outputs/*.csv       │
                       └──────────────────────────────┘
                                       │
                                       ▼
              ┌────────────────────────────────────────────────┐
              │  Supabase                                       │
              │  • email_events_raw (webhook buffer)            │
              │  • Edge Function: resend-webhook                │
              │    receives Resend events 24/7, supabase_sync   │
              │    polls + replays into local sequencer         │
              └────────────────────────────────────────────────┘

External services:
  • Google Places API     — scraping
  • Anthropic Claude       — niche fit, lead scoring, email drafting
  • Resend                 — outbound send + open/click/bounce webhooks
  • Gmail API (OAuth)      — reply detection (read-only)
  • Google Sheets API      — Master Sheet sync
  • Asana API              — task creation for replied leads
```

### Data flow

1. **Scrape** — `scraper.py` calls Google Places `searchText` for each
   `(niche, city)` combo. Results streamed to a per-niche CSV under
   `data/outputs/`. Progress tracked in the `scrape_history` table.
2. **Enrich** — `enrich_leads.py` adds website, email, social handles.
   `verify.py` runs MX + SMTP probes. `fitcheck.py` asks Claude for a
   per-lead niche fit score and reason. `ai_metrics.py` aggregates these
   into dashboard stats.
3. **Compose** — `personalize.py` drafts subject + body per lead using the
   per-niche `niche_offers` record (offer copy, tone, Loom URL). Drafts are
   cached in `outreach_drafts`. Final assembly + signoff stripping done by
   `email_compose.py`.
4. **Send** — `sequencer.py` enqueues 5 steps per lead at offsets `0, 3, 6,
   10, 14` days. A daemon thread polls every minute for due steps and ships
   them via `resend_send.py`. Each send writes to `outreach_log` and
   `sequence_messages`.
5. **Track** — Resend posts `email.delivered/opened/clicked/bounced/etc.` to
   the Supabase Edge Function `resend-webhook`. Rows land in
   `email_events_raw`. `supabase_sync.py` polls the buffer every tick,
   replays events through `sequencer.record_event`, then marks
   `processed_at`. `gmail.check_replies` runs every 15 min and pauses the
   sequence on any inbound reply.

### Layout

```
src/
  processors/
    enrich_leads.py        # batch enrichment + verification
  web/
    app.py                 # Flask routes (UI + API)
    scraper.py             # Google Places client
    scraper_runner.py      # streaming wrapper for /scrape UI
    enrich_runner.py       # streaming wrapper for /enrich
    verify.py              # MX + SMTP email verifier
    fitcheck.py            # Claude-based niche fit scoring
    ai_metrics.py          # rollups for dashboard
    personalize.py         # Claude email drafter (subject + body)
    email_compose.py       # final body assembly + signoff stripper
    niche_briefs.py        # per-niche offer / tone / Loom URL
    email_quality.py       # heuristics for draft quality gating
    sequencer.py           # 5-step drip engine + scheduler daemon
    resend_send.py         # Resend HTTP client
    gmail.py               # Gmail OAuth + reply detection
    sheets.py              # Master Sheet sync
    asana.py               # Asana task creation
    supabase_sync.py       # poll Supabase webhook buffer → sequencer
    db.py                  # SQLite schema + CRUD + analytics
    jobs.py                # background job registry (in-process)
    metrics.py             # campaign + funnel metrics
    static/, templates/    # Notion-styled UI
supabase/
  functions/resend-webhook/  # Deno Edge Function (Resend receiver)
  migrations/                # SQL for email_events_raw
data/
  outputs/                 # per-niche scrape CSVs (gitignored)
  imports/                 # user-uploaded CSVs (gitignored)
  outreach.db              # SQLite, gitignored
```

### Local SQLite tables (`data/outreach.db`)

| Table              | Purpose                                          |
|--------------------|--------------------------------------------------|
| `settings`         | per-user prefs, API tokens, sender signature     |
| `scrape_history`   | every scrape run + lead count                    |
| `outreach_log`     | one row per send, joins to Resend events         |
| `outreach_drafts`  | cached subject + body per lead                   |
| `sequences`        | one row per (lead, niche) sequence enrolment     |
| `sequence_messages`| 5 step rows per sequence (status, send_at, …)    |
| `email_events`     | Resend webhook events (delivered/opened/…)       |
| `niche_offers`     | per-niche offer copy + tone notes + Loom URL     |
| `gmail_tokens`     | OAuth tokens for reply-scan inbox                |
| `csv_sheets`       | Master Sheet tab mapping per CSV                 |
| `asana_tasks`      | Asana task IDs created for replied leads         |
| `ai_forecast`      | dashboard score forecasts                        |

---

## Run

```bash
cp .env.example .env       # fill in keys (see below)
pip install -r requirements.txt
python -m src.web.app      # http://localhost:5001
```

### Required env vars

```
GOOGLE_PLACES_API_KEY=
ANTHROPIC_API_KEY=
RESEND_API_KEY=
RESEND_FROM=                # verified sender, e.g. founder@oltaflock.ai
RESEND_WEBHOOK_SECRET=      # whsec_… from Resend dashboard

# Gmail OAuth (reply detection)
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# Supabase webhook buffer
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_KEY=        # service_role JWT, server-only
```

### Deploying the webhook receiver

```bash
supabase db push                                                 # runs migration
supabase functions deploy resend-webhook --no-verify-jwt
supabase secrets set RESEND_WEBHOOK_SECRET=whsec_...
# Then in Resend → Webhooks, point at:
#   https://<ref>.supabase.co/functions/v1/resend-webhook
```

---

## File / dir conventions

- Code lives under `src/`. Nothing executable in repo root.
- Generated CSVs / SQLite live under `data/` and are gitignored.
- HTML templates in `src/web/templates/`, static assets in `src/web/static/`.
- Keep modules under 500 lines; split when they grow past that.
- Never commit `.env`, `*.bak`, lead CSVs, or `outreach.db`.
