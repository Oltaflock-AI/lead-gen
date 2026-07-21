# AGENTS.md — file & organization rules

Rules for any agent (human or AI) editing this repo. Read before adding or moving files.

## Layout

- `src/scrapers/` — scraper scripts. One file per niche/region pair. Production canon.
- `src/processors/` — post-processing scripts (create when needed; do not preempt).
- `src/web/` — Flask dashboard. Canonical UI: wraps every scraper + enricher, owns Google OAuth (Sheets + Gmail send), Claude personalization, Asana task creation, and the SQLite outreach log. Run with `python -m src.web.app`.
- `src/utils/` — shared helpers (create only when ≥2 callers exist).
- `data/outputs/` — CSVs produced by scrapers/processors in this repo. Flat. No date or geography subfolders.
- `data/imports/` — externally-sourced CSVs (Apollo/RocketReach dumps, manual exports, third-party leads). Inputs to processors, not produced by code in this repo.
- `data/cache/` — API response cache, if needed. Gitignored.
- `data/outreach.db` — SQLite owned by `src/web/` (outreach log + settings + Gmail token). Gitignored. Created on first dashboard launch.
- `tests/` — test and dev scaffolding. Not production code.
- `.env` — secrets. Project root only. Gitignored. See `.env.example` for the full list.
- `.claude/`, `.gitignore`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `requirements.txt`, `vercel.json` — repo metadata.
- `<niche>-offer.md` (e.g. `home-services-offer.md`, `real-estate-offer.md`) — offer briefs. Project root only; `src/web/niche_briefs.py` resolves them there at runtime. Not junk — do not move or delete.
- `OF_FAVICON.png` — project root only; bundled by `vercel.json` includeFiles and read by `api/index.py`.
- `scripts/cleanup_junk.sh` — junk auto-cleanup (caches, `.DS_Store`, old `.bak`/rotated logs). Runs on every Claude Code session start via `.claude/settings.json` SessionStart hook.

## Naming

### Scrapers
- Pattern: `<niche>_<region>.py`. Lowercase, snake_case.
- Examples: `law_firms_us.py`, `seasonal_us.py`, `businesses_india.py`.
- ❌ No `_v2`, `_new`, `_final` suffixes — edit in place, use git for history.
- ❌ No date in filename — `leads_2026_04_30.py` is wrong.
- ❌ No filter adjectives in name — `law_firms_no_website_us.py` is wrong. Filter is logic, not identity. Document filter in module docstring.

### Outputs
- Pattern: `<same_basename_as_scraper>.csv`.
- One scraper writes to exactly one CSV with the matching basename.
- Example: `src/scrapers/law_firms_us.py` → `data/outputs/law_firms_us.csv`.
- Processors preserve the input basename. `enrich_leads.py data/imports/home_services_contacts_us.csv` → `data/outputs/home_services_contacts_us.csv`. Directory (imports vs outputs) signals raw vs enriched; basename stays constant through the pipeline.
- ❌ No `_enriched`, `_processed`, `_final` suffixes on output files. The directory carries that meaning.

### Directories
- snake_case.
- Group by function (`scrapers/`), not by region (`us_scrapers/`).

## Path handling in scripts

Always anchor paths to project root via:

```python
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = PROJECT_ROOT / "data" / "outputs" / "<basename>.csv"
```

❌ No hardcoded absolute paths (`/Users/khush/...`).
❌ No `~` expansion for project files (only for true home-dir resources).
❌ No cwd-relative paths.

## Secrets

- Read API keys from `.env` via `python-dotenv`.
- Anchor `load_dotenv` to `PROJECT_ROOT / ".env"`, not cwd.
- Raise on missing key — fail fast.
- ❌ Never hardcode keys in source. If found, rotate immediately and switch to env loading.

## When to add a new file

- New niche or region → new scraper file in `src/scrapers/`.
- ❌ Do NOT branch an existing scraper with a flag (`--india`). One file per target.
- New transformation step → new file in `src/processors/`.
- Shared logic used in ≥2 scrapers → extract to `src/utils/`.

## When NOT to add files

- ❌ Versioned copies (`scraper_v2.py`).
- ❌ Date-stamped scripts.
- ❌ Per-run output folders. Resume logic in the scraper handles incremental saves.
- ❌ Scripts at repo root. Code goes in `src/` or `tests/`.
- ❌ Production code in `tests/`. Test scaffolding only — promote to `src/` if it becomes canon.

## Lead enrichment

Every lead CSV must pass through `src/processors/enrich_leads.py` before being considered done. The pipeline:

1. Google Places lookup for each business (name + region)
2. Add phone number — remove leads with no phone
3. Verify business exists — remove not-found entries
4. Confirm no website — remove leads that have one (unless `--keep-with-website`)
5. Remove permanently closed businesses
6. Add enrichment columns: `verified_address`, `rating`, `review_count`, `google_maps_url`
7. Re-index `row_num`

Usage: `python3 src/processors/enrich_leads.py <csv_path>`

❌ Never deliver a raw/unenriched CSV.
❌ Never skip verification steps — partial enrichment (e.g. phone only) is not enough.

## Maintenance

- Rename early. The cost of a rename grows with the number of references.
- When moving a file, grep for its old path and patch all refs in the same change.
- Keep `README.md` layout block in sync with reality.
- Memory entries that reference old paths will go stale; do not chase them.
