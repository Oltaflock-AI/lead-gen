# lead-gen

Google Places API → CSV lead scrapers. Niche × region targeting with no-website / review-count / rating filters.

> **Before you write a new scraper, read [Existing scrapers](#existing-scrapers) and [Decision tree](#decision-tree-modify-or-create). 9 times out of 10 the answer is *modify the existing one*.** Creating yet another scraper is the slow path, not the fast path.

---

## Project layout

```
lead-gen/
├── src/
│   ├── scrapers/         # one file per niche × region (production)
│   ├── processors/       # post-processing on existing CSVs (enrich_leads.py)
│   ├── web/              # Flask dashboard — canonical UI for every script
│   └── utils/            # shared helpers — only when ≥2 callers exist
├── data/
│   ├── outputs/          # CSVs produced by code in this repo (one per scraper basename)
│   ├── imports/          # external CSVs (Apollo/RocketReach dumps, manual exports) — inputs only
│   ├── cache/            # API response cache (gitignored)
│   └── outreach.db       # sqlite — outreach log + settings + Gmail token (gitignored)
├── .env                  # secrets (gitignored) — see .env.example
├── .env.example          # template for required + optional env vars
├── AGENTS.md             # file/naming/path rules + enrichment policy — read before adding files
├── README.md             # this file
└── requirements.txt
```

File/naming/path rules live in [AGENTS.md](./AGENTS.md). This README focuses on **what already exists** and **when to reuse vs create**.

---

## Existing scrapers

| File | Niche | Region | Output | Filters / notes |
|---|---|---|---|---|
| `src/scrapers/law_firms_us.py` | Law firms | US (150+ cities) | `data/outputs/law_firms_us.csv` | No-website, review-count + rating thresholds (env-tunable: `MIN_REVIEWS`, `MIN_RATING`) |
| `src/scrapers/seasonal_us.py` | Seasonal businesses (landscaping, pool, HVAC, roofing, summer/outdoor, etc.) | US (~50 cities) | `data/outputs/seasonal_us.csv` | Multi-niche list inside file |
| `src/scrapers/businesses_india.py` | Mixed local businesses (restaurants, salons, gyms, clinics, retail, services) | India (25 cities) | `data/outputs/businesses_india.csv` | Wide niche list inside file |
| `src/processors/enrich_leads.py` | **Canonical enrichment processor** | — | enriches any lead CSV in place | Full pipeline: Places lookup → phone → verify exists → confirm no website → drop closed → add `rating`/`review_count`/`full_address`/`google_maps_url`/`email` → re-index. Mandatory per [AGENTS.md § Lead enrichment](./AGENTS.md). Flags: `--keep-with-website`, `--skip-email`. |

### Existing imports (external data, not produced by this repo)

| File | Schema | Rows | Source |
|---|---|---|---|
| `data/imports/home_services_contacts_us.csv` | Apollo-style contacts (prospect + company + emails + phones + LinkedIn) | 59 | External enrichment dump, US home-services niche |
| `data/imports/home_services_businesses_us.csv` | Firmographic (company + NAICS + revenue + employee range) | 30 | External business-only dump, US home-services niche |

These feed into `enrich_leads.py`, which writes the enriched version to `data/outputs/<same_basename>.csv`. Same basename across `imports/` and `outputs/` is intentional — directory signals raw vs enriched. No `_enriched` suffix.

Source of truth for what each script actually does = the module docstring + the `CITIES` / `BUSINESS_TYPES` / threshold constants at the top of the file. **Open the file before deciding it doesn't fit.**

There is also a Flask **dashboard** under `src/web/` that wraps every script above behind a single UI: run any scraper, run enrichment, view per-CSV lead-quality scores + projected pipeline revenue, generate Claude-personalized cold emails, send via Gmail OAuth, export to Google Sheets, and create Asana tasks for low-score leads. Run with `python -m src.web.app` → http://localhost:5001. The CLI scrapers above remain the canonical batch pipeline; the dashboard is the day-to-day interface.

---

## Decision tree: modify or create

When a new lead-gen task arrives, follow this in order. **Stop at the first match.**

1. **Same niche + same region as an existing scraper?**
   → Edit that scraper. Adjust thresholds (`MIN_REVIEWS`, `MIN_RATING`, no-website flag) or extend `CITIES` / `BUSINESS_TYPES`. Do **not** create a new file.

2. **Same niche, different region (e.g. law firms in UK)?**
   → New scraper file: `<niche>_<region>.py`. Copy the closest existing one as a template, swap the city list and any region-specific filters.

3. **Different niche, same region as existing (e.g. dentists US)?**
   → If the niche is already covered inside `seasonal_us.py` or `businesses_india.py` BUSINESS_TYPES list, just run that scraper — it likely already produces those leads.
   → Otherwise new scraper file `<new_niche>_<region>.py`.

4. **Same data, just need post-processing (phone, email, website status, dedupe, format conversion)?**
   → Run `src/processors/enrich_leads.py` first — it already does Places lookup, phone, website check, closure check, and email enrichment. Only add a new processor if the transformation is genuinely outside that pipeline. Model new processors after `enrich_leads.py`.

5. **Need a one-off Google Sheets export, ad-hoc query, or visual UI run?**
   → Use `tests/web_app/`. Don't add a CLI scraper for it.

6. **None of the above?**
   → Then, and only then, create a new scraper file. Confirm the pattern with the user first if the request is ambiguous.

### Anti-patterns (do not do these)

- ❌ Creating `law_firms_us_high_reviews.py` because the threshold differs — change `MIN_REVIEWS` instead.
- ❌ Creating `law_firms_no_website_us.py` because the filter differs — filters are logic, not identity. Document in docstring, parametrize via env or constant.
- ❌ Creating a `_v2`, `_new`, `_final`, or date-stamped variant — edit in place; git tracks history.
- ❌ Creating a separate scraper to add a single city — append to the `CITIES` list.
- ❌ Putting a new script at repo root or in `tests/` when it's production logic — it goes in `src/scrapers/` or `src/processors/`.
- ❌ Writing a bespoke Places API client — reuse the request shape from an existing scraper (same `places:searchText` endpoint, same `X-Goog-FieldMask` header pattern).

---

## Conventions (summary — full rules in AGENTS.md)

- **Naming:** `<niche>_<region>.py`, lowercase snake_case. No filter adjectives, version suffixes, or dates in filenames.
- **Output:** every scraper writes to `data/outputs/<same_basename>.csv`. One scraper, one CSV.
- **Paths:** anchor everything to `PROJECT_ROOT = Path(__file__).resolve().parents[2]`. No hardcoded absolute paths, no cwd-relative paths.
- **Secrets:** `GOOGLE_PLACES_API_KEY` from `.env` via `python-dotenv`. Anchor `load_dotenv(PROJECT_ROOT / ".env")`. Fail fast if missing. Never hardcode.
- **Shared logic:** extract to `src/utils/` only after ≥2 real callers exist. No premature abstraction.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo 'GOOGLE_PLACES_API_KEY=your_key_here' > .env
```

## Running a scraper

```bash
python -m src.scrapers.law_firms_us
python -m src.scrapers.seasonal_us
python -m src.scrapers.businesses_india
```

Output lands in `data/outputs/<basename>.csv`. Re-runs resume / append (check the script's resume logic before assuming overwrite).

## Running the enrichment pipeline

Every lead CSV must pass through this before delivery (see [AGENTS.md § Lead enrichment](./AGENTS.md)).

```bash
python3 src/processors/enrich_leads.py data/outputs/law_firms_us.csv
python3 src/processors/enrich_leads.py data/imports/home_services_contacts_us.csv --skip-email
python3 src/processors/enrich_leads.py data/outputs/seasonal_us.csv --keep-with-website
```

Drops leads with no phone, no Places match, permanently closed status, or (by default) any website. Adds `rating`, `review_count`, `full_address`, `google_maps_url`, `email`. Re-indexes `row_num`.

## Dashboard (canonical UI for everything)

```bash
python -m src.web.app    # http://localhost:5001
```

Pages:

| Path | What it does |
|---|---|
| `/` | Total leads, quality scores, projected pipeline revenue, recent outreach |
| `/scrape` | Run any canonical scraper or do an interactive country×niche search (toggle no-website filter) |
| `/leads` | Every CSV in `data/imports/` + `data/outputs/` with avg score + qualified count |
| `/leads/<file>` | Per-row quality score; buttons for Enrich, Export to Sheets, Compose outreach, Bulk Asana tasks |
| `/outreach` | Pick leads with email → Claude personalized drafts → edit → send via Gmail → log |
| `/settings` | Edit close rate / avg deal value / sender name / signature; see env-var status |

Setup:

1. `pip install -r requirements.txt`
2. Copy `.env.example` → `.env`, fill in:
   - `GOOGLE_PLACES_API_KEY` — required for scrapers + enrichment
   - `FLASK_SECRET_KEY` — any random string
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — Google OAuth (web app, redirect `http://localhost:5001/auth/callback`); enable Sheets API + Gmail API in Google Cloud
   - `ANTHROPIC_API_KEY` — optional; without it cold emails fall back to a static template
   - `ASANA_PAT` — optional; PAT from `app.asana.com/0/my-apps` to enable task creation
3. `python -m src.web.app`

---

## Checklist before adding any new file

1. Read [Existing scrapers](#existing-scrapers).
2. Walk the [Decision tree](#decision-tree-modify-or-create).
3. Confirm naming and path rules in [AGENTS.md](./AGENTS.md).
4. If still creating a new file: copy the closest existing scraper as a template — same imports, same `PROJECT_ROOT` anchoring, same `.env` loading, same Places API request shape, same CSV writer pattern.
