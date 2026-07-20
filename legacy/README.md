# legacy/ — archived local Flask stack

Archived 2026-07-20 by plan.md Phase 1.7. **Read-only reference — do not extend.**
The production stack is `api/` + `lib/` + Supabase (see `plan.md` §0).

## What's here

- `src/web/` — the retired local Flask app (`:5001`) and its modules
- `src/scrapers/`, `src/processors/` — retired scraping/processing pipeline
- Root plan/mockup docs superseded by `plan.md` (`auto-sequence-plan.md`,
  `dashboard-redesign-plan.md`, `handoff-to-claude-code.md`,
  `claude-code-prompt-dashboard-only.md`, redesign mockups)

The SQLite archive `data/outreach.db` stays where it is (read-only forever).
Google Sheets export (`sheets.py`) and Asana push (`asana.py`) are retired with
no port, per approved decision plan.md §0.2.

## Modules that later phases PORT into lib/ (do not delete)

| Legacy module | Ports to | Phase |
|---|---|---|
| `src/web/email_quality.py` | `lib/email_quality.py` | 3.2 |
| `src/web/fitcheck.py` | `lib/fitcheck.py` | 3.2 |
| `src/web/email_finder.py` (`find_business_website`) | merge into `lib/email_finder.py` | 3.2 |
| `src/web/ai_metrics.py` (scoring rubric) | merge into `lib/enrich.py` prompt | 3.2 |
| `src/web/send_timing.py` (`BUSINESS_HOURS`) | `lib/sequence.py` | 4.3 |
| `src/web/metrics.py` (`copy_performance_*`) | `learning_tick` table | 4.6 |
| bounce diagnostics (`email_suppressions` cols) | event meta + dashboard card | 4.5 |

`scripts/test_sequence_preview.py` still imports `src.web` from here (path
shimmed to `legacy/`); the other kept scripts are self-contained.
