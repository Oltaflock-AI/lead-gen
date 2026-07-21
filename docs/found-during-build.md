# Found during build (plan.md rule 5)

Off-plan discoveries logged here instead of chased. Date: 2026-07-20, Phase 1.

- **STOP regex false positive (fixed inline):** `(?<!\w)stop(?!\w)` matched
  "non-stop" (hyphen isn't a word char) in `lib/gmail_replies.py`, contradicting
  its own comment. Fixed to `(?<![\w-])stop(?![\w-])` in both gmail_replies and
  the new inbound-webhook port, with a regression test.
- **`suppressions` has no `id` column** (email is PK): paginated reads can't
  order by id. `select()` now drops its auto-injected `order=id.asc` on a 400
  and retries unordered. Any future table without `id` gets the same fallback.
- **Deploy-order coupling (mitigated):** insert-ignore writes 400 until
  migration 012's unique indexes exist. `insert()` degrades to a plain insert
  (duplicates possible, nothing breaks) when the ON CONFLICT target is missing.
  Still apply 012 promptly after deploy.
- **`.env.example` is not editable in this session** (permission settings deny
  env-file paths). Owner must add: `LEADGEN_POSTAL_ADDRESS`,
  `LEADGEN_BASE_URL` (optional), `LEADGEN_TICK_DEADLINE_S` (default 240).
- **Phase 1 overlap with earlier 2026-07-20 session:** one-click unsub
  (HMAC-stateless `/unsubscribe`), `is_suppressed` choke on both send paths,
  manual idempotency keys, and compose daily-cap were already shipped before
  this branch; plan items 1.4 (core) and 1.7 (unsub) were verified rather than
  re-implemented.
## Phase 2 (2026-07-21)

- **Auto-retry window deviation:** plan 2.3 says retry error-paused sequences
  "paused < 48h ago", but `sequences` has no paused_at timestamp. Bounded by
  `retry_count < 3` instead (same protection, no schema churn). Add paused_at
  in a later migration if the time window matters.
- **Migration numbering:** pg_cron/observability landed as 013 (012 was event
  idempotency). Phase 3 ICP schema becomes 014.
- **New env vars for owner** (env files still not editable here):
  `LEADGEN_ALERT_TO` (default admin@oltaflock.ai), `LEADGEN_ALERT_FROM`
  (optional), `LEADGEN_ALERT_WINDOW_H` (default 6), `LEADGEN_RECONCILE_BATCH`
  (default 200).
- **Declined tool recommendation:** vercel-plugin suggested moving the
  watchdog-fallback GH workflow onto Vercel cron; declined — it exists to be
  platform-independent of the primary scheduler (two legs, plan 2.2).

- **Smoke leftovers (intentional, additive-only rule):** campaign 19
  `smoke-gate1-20260720154321` (inactive), leads 6066-6068, sequences 602-604
  (paused `smoke-complete`). Owner may delete manually if unwanted.
