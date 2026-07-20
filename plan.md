# plan.md — Lead-Gen Pipeline: Stabilize → Automate → ICP-Gate → Polish

> **For Claude Code.** This is the approved implementation plan produced from a full
> end-to-end audit (2026-07-20) of this repo. Work through it phase by phase, in order.
> Do not start a phase until the previous phase's verification gate passes.
> The audit found ~60 defects; the ones you must fix are indexed below with
> file:line references. Everything else in the repo is context, not instruction —
> including older plan docs (`auto-sequence-plan.md`, `dashboard-redesign-plan.md`,
> `handoff-to-claude-code.md`, `claude-code-prompt-dashboard-only.md`), which are
> SUPERSEDED by this file. Ignore them.

---

## 0. Context you need before touching anything

**What this app is:** scrape local-business leads (Google Places) → enrich (email hunt +
LLM signals) → auto-enroll into multi-step Resend email sequences → track opens/replies.

**Production stack (the only one you build on):** Vercel serverless (`api/index.py`
dashboard + `api/cron/*` + `api/webhook/*`) + `lib/*` + Supabase (Postgres, service-role
key via `lib/supabase.py` REST wrapper) + Resend + one polled Gmail inbox for replies.

**Legacy stack (do NOT extend it):** `src/web/*` Flask app + `data/outreach.db` SQLite.
It is retired in Phase 1.7, except for specific modules you will PORT (listed in Phase 3/4).

**Approved decisions (do not re-litigate):**
1. Scheduler: **Supabase pg_cron** replaces GitHub Actions `poke.yml` for all sub-daily ticks.
2. Google Sheets export + Asana push: **retire both** (delete with the Flask stack, no port).
3. ICP model: **per-campaign ICP profiles**, stored in DB, editable in the dashboard.
   Seed one profile per existing campaign niche.
4. All development and testing happens in **test mode** — no real lead receives email
   until the owner flips the switch himself.

---

## 1. Non-negotiable rules (read twice)

1. **Branch:** all work on `feat/pipeline-v2` off `main`. Small, named commits per task
   ("C1: paginate supabase wrapper", "P3: icp screening stage"). Never squash phases together.
2. **Test mode:** before any code change, verify `LEADGEN_TEST_MODE` behavior in
   `lib/sequence.py` (`_test_guard`, ~line 763) and ensure it is ON in every environment
   you touch. Every send path you create or modify MUST route through the test guard.
   If you find a send path that bypasses `_test_guard`, that is a bug — fix it.
3. **Data safety:** Supabase migrations are ADDITIVE only — new tables, new columns,
   new indexes, new constraints (constraints may be `NOT VALID`-then-validate if data
   would fail them). No `DROP TABLE`, no `DROP COLUMN`, no `DELETE FROM` on existing
   data outside of explicitly listed cleanup tasks. SQLite `data/outreach.db` is
   READ-ONLY forever (it's an archive; Phase 1.7 exports from it, never writes).
4. **Secrets:** never print, commit, or copy env values. `.env` is read-only.
   New env vars go in `.env.example` with comments.
5. **Scope discipline:** if you discover a bug not listed here, note it in
   `docs/found-during-build.md` and continue — don't chase it unless it blocks your task.
6. **Verification:** each phase ends with its Verification Gate (section 7). Run it,
   paste results into the phase's commit message, then stop for owner review before
   the next phase.
7. **Don't guess Supabase state:** before writing migration `011+`, list applied
   migrations (`supabase migration list` or ask the owner). Migrations 001–010 exist.

---

## 2. PHASE 1 — Correctness + compliance (stop the bleeding)

### 1.1 Fix the 1000-row truncation family (C1 — most important bug in the repo)
`lib/supabase.py:24-30` — the `select()` wrapper passes `limit` but PostgREST caps
responses at `db-max-rows` (default 1000) and nothing paginates.

- Rewrite `select()` to auto-paginate with `Range`/`Range-Unit: items` headers (or
  `offset` loop) whenever the caller asks for more than one page; loop until short page.
- Audit EVERY call site that passes `limit` ≥ 1000 and decide: paginate, or better,
  push the filter into SQL so the big read disappears. Known dangerous sites:
  - `api/cron/sequencer_tick.py:70-72` `_autocreate` loads all `sequences.lead_id` to
    diff in Python → replace with a SQL anti-join (see 1.2).
  - `api/cron/sequencer_tick.py:92-95` `_suppressed()` loads the whole suppression
    table → replace with per-batch `email=in.(...)` checks or an RPC (see 1.4).
  - `lib/gmail_replies.py:129` `_contacted_map` — paginate.
  - `lib/learning.py:39-49`, `api/index.py:364,406,1851`, `api/cron/daily_digest.py:29-38`
    — paginate or convert to SQL aggregates (a `count=exact` head request or an RPC).

### 1.2 Stop `_autocreate` resurrecting finished sequences (C1a)
`api/cron/sequencer_tick.py:58-84`: today it upserts `sequences` with
`on_conflict=lead_id` → merge-duplicates UPDATE, which resets `done/replied/paused`
sequences to step 0 whenever the dedup set is truncated (or ever races).
- Replace with plain INSERT + `Prefer: resolution=ignore-duplicates` (or
  `on_conflict=lead_id` + ignore), so an existing row is NEVER modified by enrollment.
- Enrollment eligibility becomes an explicit filter (expanded in Phase 3): lead has
  non-null email, is not suppressed, has no existing sequence row.
- First send must respect the send window: set `next_send_at` via the same
  `next_send_at()` logic steps 2+ use (`lib/sequence.py`), not `now` (fixes B9c).

### 1.3 Stop the daily scrape wiping enrichment (C3)
`api/cron/daily_scrape.py:53-58` + `lib/scrape.py:70-86`: re-found businesses are
upserted with `enrichment_status='pending'` and fresh `signals`, clobbering enrich output.
- On upsert, EXCLUDE `enrichment_status`, `signals`, `email`, `enriched_at`, `intent_score`,
  `revenue_band`, `research`, `researched_at` from the update payload for existing rows
  (insert-only columns). Simplest: switch to `resolution=ignore-duplicates` and count
  true inserts; re-scrape adds only NEW businesses.
- Fix `scraped_count` to count actual new rows, not upsert attempts.
- Same fix on CSV upload paths `api/index.py:846-859` and `:1263-1270`.

### 1.4 Enforce suppression on EVERY send path (C4 — CAN-SPAM)
- Add a single choke-point check inside `lib/sequence.py` at the bottom of the send
  funnel (both `send_email` and `send_manual`): query `suppressions` for the recipient
  (lowercased) immediately before the Resend call; if present, skip + return a
  `suppressed` result. This automatically covers `/compose/send` (`api/index.py:1575-1670`),
  `/replies/send` (`api/index.py:2241+`), and the sequencer.
- Add the daily-cap check to manual blasts: `/compose/send` must count against and
  respect `LEADGEN_DAILY_CAP` (reject or truncate recipient lists that exceed remaining).
- `api/webhook/inbound.py:57-83`: on STOP-intent (port the regex set from
  `lib/gmail_replies.py:87-105`), also insert into `suppressions` (today it only pauses).
- `api/webhook/resend.py:207-216`: bounces/complaints on sequence-less sends currently
  return early before the suppression branch — restructure so bounce/complaint ALWAYS
  suppresses regardless of whether `sequence_id` resolved (fixes H7/B12).

### 1.5 Per-item error isolation + idempotency (C7, H3)
- `api/cron/sequencer_tick.py:202`: wrap `_process_one` per item in try/except; a failed
  item logs to `system_events` (created in Phase 2, use print-logging until then),
  pauses only that sequence with `paused_reason='send-error'`, and the loop continues.
- `lib/sequence.py:851-855` `send_manual`: add an `Idempotency-Key` derived from a
  caller-supplied token (generate a UUID per compose-form render, pass it through the
  POST) so double-submits can't double-send.
- Add a wall-clock deadline to `sequencer_tick` (like `lib/research.py`'s `DEADLINE_S`):
  stop claiming new items after ~240s so Vercel's 300s `maxDuration` never kills mid-send (B23).

### 1.6 Event idempotency in Postgres (H1) — migration 011
- `email_events_raw`: add unique index on `(resend_id, event_type)` (dedupe key);
  Edge Function/webhook inserts become upsert-ignore.
- `sequence_events`: add unique partial index on `(resend_id, event_type)` where
  `resend_id is not null`; webhook insert becomes insert-ignore, and counter increments
  (`increment_sequence_counter` RPC) only run when the event insert actually inserted.
- CHECK constraints (as `NOT VALID`, then validate) on `sequences.status`,
  `leads.enrichment_status`, `sequence_events.event_type` enumerations.

### 1.7 Compliance + hygiene batch
- **Unsubscribe one-click (H5):** migration 011 adds `unsub_tokens (token pk, email, created_at)`
  or derive HMAC tokens statelessly from `LEADGEN_SECRET`. New public route
  `GET/POST /unsub/<token>` in `api/index.py` (exempt from auth gate at `api/index.py:64-72`)
  that inserts into `suppressions` (reason=unsubscribe) and shows a plain confirmation
  page. Every outgoing email gets headers `List-Unsubscribe: <https://…/unsub/<token>>, <mailto:…>`
  and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, plus a footer link.
- **Postal address (H5):** at import time in `lib/sequence.py`, if `LEADGEN_POSTAL_ADDRESS`
  is empty AND `LEADGEN_TEST_MODE` is off, refuse to send (raise a clear error into the
  tick result). Add the var to `.env.example`.
- **Supabase Edge Function (H2):** delete `supabase/functions/resend-webhook/` (superseded
  by `api/webhook/resend.py`). Tell the owner in the phase summary to remove its endpoint
  from Resend's webhook config if still registered.
- **Legacy retirement:** move `src/web/`, `src/scrapers/`, `src/processors/`, dead root
  HTML/plan files into `legacy/` (git mv — keep history; do NOT delete yet). EXCEPT:
  leave `scripts/maharera_scrape.py`, `scripts/scrape_eprocure.py`,
  `scripts/preview_travel_drip.py`, `scripts/test_sequence_preview.py` in place.
  Write `legacy/README.md` stating what's archived and which modules Phase 3/4 port.
- **Suppression export:** one-off script `scripts/export_legacy_suppressions.py` that
  reads `data/outreach.db` `email_suppressions` (20 rows, includes bounce diagnostics)
  and upserts into Supabase `suppressions`. Run it, show the count.
- **Dead code:** remove `ADMIN_KEY` (`api/index.py:26`); fix `_fire` hardcoded URL
  (`api/index.py:27,1915-1920`) → use `VERCEL_PROJECT_PRODUCTION_URL` env or a
  `LEADGEN_BASE_URL` var, and log failures instead of `except: pass`.

---

## 3. PHASE 2 — Real scheduling + observability (pg_cron + alerting)

### 2.1 pg_cron scheduler — migration 012
- Enable `pg_cron` + `pg_net` extensions in Supabase.
- Store `CRON_SECRET` and the Vercel base URL in Supabase Vault (`vault.create_secret`);
  the SQL reads them at call time — never hardcode either in the migration.
- Schedule via `cron.schedule(...)` + `net.http_post` (or `http_get`) with
  `Authorization: Bearer <secret>`:
  - `sequencer_tick` — `*/5 * * * *`
  - `enrich_tick` — `*/10 * * * *`
  - `replies_tick` — `*/15 * * * *`
  - `research_tick` — `*/15 * * * *`
  - `learning_tick` — `0 6 * * *`
  (Keep `daily_scrape`/`daily_digest` on Vercel cron — they already work there.)
- Add a `cron_heartbeats (job text pk, last_ok timestamptz, last_status text, last_body text)`
  table; every cron handler writes its heartbeat row at start and on success/failure
  (small helper in `lib/`, call it from each `api/cron/*` handler).
- **Delete `.github/workflows/poke.yml`** in the same commit that the owner confirms
  pg_cron fired each job at least once (check `cron.job_run_details` + heartbeats).

### 2.2 Alerting — the "you find out at 8am, not never" system
- Migration 012 also adds `system_events (id, ts, level, source, message, meta jsonb)`.
- Small `lib/ops.py`: `log_event(level, source, message, meta)` writing to that table
  (never raises), plus `alert(...)` which ALSO sends an email via Resend to
  `LEADGEN_ALERT_TO` (default admin@oltaflock.ai) — rate-limited to max 1 email per
  source per 6h (check last alert row before sending).
- Wire alerts into: per-item send failure (1.5), LLM fallback (`lib/llm.py` — on any
  non-200/exception, `log_event` with the status; `lib/sequence.py:697-704` alert when
  falling back to canned copy), enrichment batch failures, webhook signature failures,
  bounce-rate spike (see 2.4).
- New cron `api/cron/watchdog.py` scheduled in pg_cron `0 */6 * * *`: checks
  `cron_heartbeats` — any job silent for 3× its interval → alert("scheduler dead: <job>").
  This is the dead-man's switch: pg_cron runs it, but if pg_cron itself dies, keep ONE
  minimal GitHub Actions workflow (daily) whose only job is to curl the watchdog — two
  independent legs.
- Fix silent LLM model foot-gun (B13): `lib/llm.py:60` — use `max_completion_tokens`
  for models that require it, and on startup validate `OPENAI_MODEL` is in
  `ALLOWED_MODELS`, alerting otherwise.

### 2.3 Retry + recovery
- `lib/supabase.py`: retry with backoff (3 attempts, 0.5/2/8s, on 5xx/timeout/connection
  errors) for idempotent requests (GET; PATCH/POST only where the operation is idempotent —
  document per call).
- Auto-retry error-paused sequences: in `sequencer_tick`, pick up sequences with
  `paused_reason='send-error'` paused < 48h ago, at most 3 retries (`meta` counter on the
  sequence row or a new `retry_count` column in migration 012), then leave paused + alert.
- Fix due-queue starvation (B8): push the active-campaign filter into the DB query
  (join/`in.(...)` on active campaign ids) so paused campaigns' rows never occupy the batch.
- Fix cap race (B7): claim-then-count — after claiming a row, re-check remaining cap from
  a `sends_today` counter incremented atomically via RPC, not a once-per-tick snapshot.
- Orphan-event reconciler: extend an existing 15-min cron (or watchdog) to sweep
  `email_events_raw` unprocessed rows: match by `resend_id` against `sequence_events`
  /`blast_recipients`, apply what matches, mark processed; rows older than 30 days →
  mark processed with `note='unmatched'`. Table stops growing unboundedly (H7).

---

## 4. PHASE 3 — The ICP engine (scrape → screen → enroll, hands-off)

### 3.1 Schema — migration 013
- `icp_profiles`: `id, campaign_id fk unique, name,` and a `rules jsonb` with a defined
  schema: `{geography: {countries[], regions[]}, business: {types[], min_rating, min_reviews,
  max_reviews, require_website: bool|null, require_after_hours_gap: bool}, email:
  {reject_role_prefixes: bool, reject_freemail: bool, reject_gov_edu: bool,
  reject_guessed_patterns: bool}, scoring: {min_fit_score: int, min_intent_score: int},
  disqualifiers: {domains[], keywords[]}}` — plus `updated_at`.
- `leads` additive columns: `icp_verdict text` (accepted/rejected/review), `icp_reasons jsonb`,
  `fit_score int`, `email_grade text` (a/b/c/reject), `screened_at timestamptz`.
- Index: `(campaign_id, icp_verdict) where icp_verdict is not null`, and partial index
  on `enrichment_status='enriched' and screened_at is null` for the screening scan.
- Seed one `icp_profiles` row per existing campaign, derived from its niche + the
  campaign `notes` scrape filters (write the seeder as part of the migration or a script).

### 3.2 Port the legacy quality modules into `lib/`
Source files are in `legacy/src/web/` after Phase 1.7. Port as new `lib/` modules with
tests; fix known defects while porting:
- `email_quality.py` → `lib/email_quality.py`: role/freemail/disposable/guessed-pattern
  classification + scoring. This is what rejects `premier@ontario.ca`, `accessibility@ubc.ca`,
  `profiles@birdeye.com`, `jdoe@…`. Extend role-prefix list with gov/edu detection
  (`.gov`, `.edu`, `.ac.*`, `.gc.ca`, `ontario.ca`-style government TLD patterns).
- `fitcheck.py` → `lib/fitcheck.py`: deterministic Places-based fit score (after-hours
  gap, review volume, rating, completeness, no-website). Weights come from the ICP
  profile's `scoring` block instead of hardcoded.
- `email_finder.py`'s `find_business_website` (website discovery w/ confidence) →
  merge into `lib/email_finder.py`. Also fix B35 there: an MX-check DNS exception must
  mark the lead for RE-CHECK (leave `enrichment_status='pending'` with a retry counter),
  not silently discard the found email.
- `ai_metrics.py` scoring rubric → merge its calibrated prompt bands into
  `lib/enrich.py`'s extraction prompt so `intent_score` has an actual rubric.
  (Known bug in legacy `agent_tools.py:164-168` — `score_email` returns a tuple, not a
  dict; don't reproduce it.)

### 3.3 The screening stage
- New `lib/screening.py`: `screen_lead(lead, icp_profile) -> (verdict, reasons, fit_score,
  email_grade)`. Pure function, deterministic first (geography, business rules, email
  grade, disqualifiers), LLM-assisted only for `scoring.min_intent_score` (already
  computed by enrich). Every rejection carries machine-readable reasons.
- New cron `api/cron/screen_tick.py` (pg_cron `*/10`): batch-screens enriched,
  unscreened leads; writes verdict columns.
- `_autocreate` (from 1.2) now enrolls ONLY `icp_verdict='accepted'` leads (email
  non-null, unsuppressed, no existing sequence). `verdict='review'` goes to a queue.
- Manual enroll route gets the same gate with an explicit `?override=1` escape hatch
  that logs a `system_events` entry.

### 3.4 Dashboard surface
- New page `/icp` (per campaign): view/edit the ICP profile (form over the `rules` jsonb),
  live counts (accepted / rejected by reason / review), and a "Review queue" list where
  the owner can accept/reject individual leads (writes verdict + reason `manual`).
- `/leads` list: verdict chip + top rejection reason per lead; filter by verdict.
- Keep it in the existing server-rendered style of `api/index.py` (no new framework).

---

## 5. PHASE 4 — Quality & scale polish

1. **Open-gate rework (H9):** gate on filtered ("confirmed") opens only; bot filter must
   compare against Resend's event `created_at`, not webhook arrival (`api/webhook/resend.py:94-107`);
   clicks (non-bot) count as strong engagement. Hot cadence requires 2+ distinct-day opens
   or 1 click; hot state decays after 14 days without engagement. `_accelerate_on_open`
   respects per-campaign `max_step(config)` (B17).
2. **Manual/drip isolation (H8):** stop `_manual_seq_id` reusing/creating rows in
   `sequences` (`api/index.py:1435-1449`). Track manual sends only in `blasts`/
   `blast_recipients`; webhook events for manual sends must not touch `sequences`
   counters. Backfill-fix: delete `status='manual'` sequence rows (they block enrollment)
   after confirming counts with the owner — this is the one approved DELETE.
3. **Timezone correctness (B16):** port `legacy/src/web/send_timing.py`'s 20-country
   `BUSINESS_HOURS` table into `lib/sequence.py`; replace substring matching with exact
   normalized lookup + explicit alias map. Parse `city`/`country` from Places
   `formattedAddress` in `lib/scrape.normalize` so `{city}` personalization works.
4. **Reply robustness (B18):** skip messages with `Auto-Submitted`/`Precedence: auto-reply`/
   `X-Autoreply` headers; out-of-office no longer pauses sequences.
5. **Bounce circuit breaker:** watchdog computes per-campaign bounce rate over trailing
   100 sends; >5% → auto-pause campaign + alert. Port legacy bounce diagnostics
   (type/subtype/diagnosticCode persisted on the event + a dashboard breakdown card).
6. **Copy telemetry port:** subject/first-line open-rate aggregation (legacy
   `metrics.copy_performance_*`) recomputed by `learning_tick` into a new table;
   top performers injected into the drafting prompt alongside the angle bandit.
7. **Multi-inbox STOP coverage (H4):** either per-user Gmail tokens (extend
   `GMAIL_TOKEN_JSON` to a JSON map keyed by sender) or instruct the owner to alias
   vineet@/amaan@/admin@/unsubscribe@ into the polled inbox — implement the map,
   document the alias fallback in the phase summary.
8. **Session hardening (H11/B31):** add issued-at + expiry (30 days) to the auth cookie,
   verify on every request; `setup_vercel_env.sh` — stop pushing secrets to
   development/preview targets (production only).
9. **Retention:** watchdog purges processed `email_events_raw` > 90 days and trims
   `replies` bodies > 180 days.

---

## 6. Explicitly OUT of scope (do not do)

- No framework migrations (no Next.js/React rewrite), no new SaaS multi-tenancy,
  no billing. "SaaS-grade" here means reliability, not productization.
- No live sending. Do not set `LEADGEN_TEST_MODE=0` anywhere, ever.
- No changes to DNS, Resend domain config, or the Gmail OAuth app (owner handles those;
  he has been told separately to revoke the token in `data/outreach.db`).
- Do not implement `auto-sequence-plan.md` or `dashboard-redesign-plan.md` — superseded.
- Do not touch `agent-reach/` (vendored separate project) or `.claude/` config.

---

## 7. Verification Gates (run at the end of each phase)

**Every phase:** `python -m pytest tests/ -q` green (write tests as you go — each fixed
bug gets a regression test; `lib/` modules get unit tests with mocked Supabase/Resend).
No secrets in `git diff`. `git diff main --stat` touches only in-scope files.

**Gate 1:** Unit tests prove: paginated `select()` returns >1000 rows correctly (mock);
`_autocreate` never updates an existing sequence row; scrape upsert preserves enrichment
fields; suppressed address is skipped by BOTH `send_email` and `send_manual`; one failing
item doesn't kill a tick; unsub endpoint suppresses and confirms. Then a live test-mode
smoke: create a throwaway campaign, upload a 3-row CSV (only admin@oltaflock.ai variants),
run the tick locally (`vercel dev` or direct handler invocation), confirm sends went to
the test redirect only, and row counts of pre-existing tables are unchanged.

**Gate 2:** `select * from cron.job` shows the 5 jobs + watchdog; `cron.job_run_details`
shows successful runs; heartbeats table populating; kill test — temporarily point one
job at a 404 and confirm an alert email arrives at LEADGEN_ALERT_TO within its window;
poke.yml deleted.

**Gate 3:** seeded ICP profiles exist for every campaign; screening tick processes the
backlog; the known-bad historical addresses (`premier@ontario.ca`, `accessibility@ubc.ca`,
`profiles@birdeye.com`, `jdoe@thecleanplumbers.com`) all screen as REJECTED with correct
reasons when replayed through `screen_lead()` in a test; `_autocreate` enrolls only
accepted leads; `/icp` page renders and saves.

**Gate 4:** regression suite green; simulated Apple-MPP open (webhook event 2 min post-send,
marked via Resend timestamp) does NOT trigger hot cadence; auto-reply email does not
pause a sequence; bounce-rate breaker pauses a test campaign; cookie expiry enforced.

---

## 8. Bug index (audit IDs referenced above)

C1 supabase 1000-row truncation (`lib/supabase.py:24`) · C1a autocreate resurrection
(`sequencer_tick.py:70`) · C3 scrape resets enrichment (`daily_scrape.py:53`) ·
C4 manual sends skip suppressions (`index.py:1575,2241`; `sequence.py:810`) ·
C5 no enrollment gate (`sequencer_tick.py:63`) · C6 silent LLM fallback (`llm.py:46`;
`sequence.py:697`) · C7 tick aborts on one failure (`sequencer_tick.py:202`) ·
H1 event/counter idempotency (migrations) · H2 edge function fails open
(`supabase/functions/resend-webhook/index.ts:50`) · H3 send_manual no idempotency key
(`sequence.py:851`) · H4 single-inbox STOP coverage (`gmail_replies.py`) · H5 postal
address + one-click unsub (`sequence.py:39,733`) · H7 orphan events never reconciled
(`resend.py:207`) · H8 manual/drip contamination (`index.py:1435`) · H9 open-gate/bot
filter (`sequence.py:394,862`; `resend.py:94`) · H10 due-queue starvation + cap race
(`sequencer_tick.py:98,195`) · H11 forever-cookies + preview secrets (`users.py:144`;
`setup_vercel_env.sh:11`) · B8/B9/B12/B16/B17/B18/B23/B30/B31/B35 as referenced inline.

---

*End of plan. Phase 1 starts now: `git checkout -b feat/pipeline-v2`.*
