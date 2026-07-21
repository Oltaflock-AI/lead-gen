# Handoff to Claude Code (VS Code) — Data-Safe Migration

> Goal: ship the dashboard redesign without touching a single row of your
> existing data. This doc walks you through it in the order you'd actually
> do it, ending with a copy-paste prompt for Claude Code.

---

## 1. Why This Migration Is Data-Safe by Design

The redesign is **purely additive**. Every chart queries tables that already exist:

| Visualization | Reads from (no writes) |
|---|---|
| Conversion funnel | `outreach_log`, `email_events` |
| 7-step pipeline | `sequences.current_step` |
| Niche performance | `outreach_log` grouped by `niche` |
| Duplicate-send detection | `outreach_log` (SELECT only) |
| CSV health score | CSV files in `data/outputs/` (read) + `outreach_log` |
| Failure alert banner | `outreach_log WHERE status='failed'` |
| Recent activity grouped | `outreach_log` |

**No new tables. No ALTER TABLE. No DROP. No data deletion.**

What does change:
- HTML templates (`src/web/templates/*.html`) — visual layout
- New read-only helper functions in `src/web/db.py`
- New aggregation functions in `src/web/metrics.py`
- The `dashboard()` route in `src/web/app.py` gets new context variables

That's it. Your `data/outreach.db` file gets opened for reads, never for schema changes.

---

## 2. Pre-Migration Safety Steps (Do These Before Touching Anything)

### Step 2.1 — Stop the running app
The Flask dev server holds a SQLite handle. Kill it before backing up:
```bash
# In the terminal where Flask is running:
Ctrl+C
```

### Step 2.2 — Back up the database
SQLite is a single file. Copy it:
```bash
cd /path/to/lead-gen
cp data/outreach.db data/outreach.db.backup-$(date +%Y%m%d-%H%M%S)
```
You'll end up with something like `data/outreach.db.backup-20260506-1430`. Keep it. If anything goes wrong, you replace `outreach.db` with this file and you're back to right now.

### Step 2.3 — Back up the CSVs and offer briefs
```bash
tar -czf data-snapshot-$(date +%Y%m%d-%H%M%S).tar.gz \
    data/outputs/ \
    data/imports/ \
    home-services-offer.md \
    real-estate-offer.md \
    CLAUDE.md
```
This zips up your scraped CSVs, the offer briefs, and your project config. One archive file you can restore from in ~10 seconds if something goes sideways.

### Step 2.4 — Create a feature branch
Never let Claude Code work on `main` directly:
```bash
git status                              # confirm clean working tree first
git checkout -b feat/dashboard-redesign
```
If `git status` is messy, commit or stash before continuing.

### Step 2.5 — Confirm what's in the DB right now
Take a snapshot of row counts so you can verify nothing got deleted:
```bash
sqlite3 data/outreach.db <<'EOF'
.headers on
.mode column
SELECT 'sequences' tbl, COUNT(*) n FROM sequences
UNION ALL SELECT 'sequence_messages', COUNT(*) FROM sequence_messages
UNION ALL SELECT 'outreach_log', COUNT(*) FROM outreach_log
UNION ALL SELECT 'outreach_drafts', COUNT(*) FROM outreach_drafts
UNION ALL SELECT 'email_events', COUNT(*) FROM email_events
UNION ALL SELECT 'niche_offers', COUNT(*) FROM niche_offers
UNION ALL SELECT 'scrape_history', COUNT(*) FROM scrape_history
UNION ALL SELECT 'csv_sheets', COUNT(*) FROM csv_sheets
UNION ALL SELECT 'settings', COUNT(*) FROM settings;
EOF
```
Save this output. After the migration you'll run it again — every number must match.

---

## 3. Hand Off to Claude Code in VS Code

### Step 3.1 — Open the project in VS Code
```bash
code /path/to/lead-gen
```

### Step 3.2 — Make sure these three files are visible to Claude Code
You want Claude Code to read these as context before it touches anything:
- `dashboard-redesign-plan.md` (the implementation plan)
- `dashboard-redesign-mockup.html` (the visual spec)
- `auto-sequence-plan.md` (the earlier 7-step rebuild plan, if you haven't shipped it yet — Claude Code should know about it but NOT do it in this PR)

Drop these three files into the project root or a `docs/` folder so they're indexed.

### Step 3.3 — Open Claude Code and paste this prompt

The prompt below is written to be paranoid about your data. Copy it as-is.

```
Implement the dashboard redesign described in dashboard-redesign-plan.md.
Use dashboard-redesign-mockup.html as the visual spec — open it side by side
and match the layout, colors, typography, and component patterns exactly.

CRITICAL DATA-SAFETY RULES — READ BEFORE TOUCHING ANYTHING:

1. The SQLite file at data/outreach.db is READ-ONLY for this PR. You may
   only ADD new SELECT helper functions to src/web/db.py. You may NOT:
   - Run ALTER TABLE
   - Run DROP anything
   - Add columns
   - Add CHECK constraints
   - Modify any existing function in db.py that does INSERT/UPDATE/DELETE
   - Touch init_db() or its CREATE TABLE statements

2. CSV files in data/outputs/ and data/imports/ are READ-ONLY. The new Leads
   page card grid reads from them but never writes.

3. The .env file is READ-ONLY. Do not modify, do not regenerate.

4. The offer markdown files (home-services-offer.md, real-estate-offer.md)
   are READ-ONLY for this PR.

5. Do NOT implement the auto-sequence rebuild from auto-sequence-plan.md
   in this PR. That ships separately. The dashboard redesign and the
   sequence auto-enrol are independent changes.

WHAT YOU MAY MODIFY:

- src/web/templates/base.html — remove sidebar Connections section
- src/web/templates/dashboard.html — full rewrite per plan section 3
- src/web/templates/outreach.html — changes per plan section 4
- src/web/templates/sequences.html — changes per plan section 5
- src/web/templates/leads.html — full rewrite per plan section 6
- src/web/templates/offers.html OR app.py — fix the "scrape Home Services
  leads" copy bug on the Real Estate offer card (plan section 7)
- src/web/templates/settings.html — add the Connections block here since
  it's removed from the sidebar
- src/web/static/ — add CSS variables and styles to match the mockup

WHAT YOU MAY ADD (new code only, no edits to existing functions):

- src/web/db.py — add these new SELECT-only helpers:
    detect_duplicate_sends(window_minutes=10)
    count_recent_failures(hours=24)
    outreach_stats_by_niche()
    sequence_step_distribution()
    csv_send_summary(csv_path)
- src/web/metrics.py — add:
    daily_sends_last_n(n=14)
    csv_health_score(csv_summary)
- src/web/app.py — modify ONLY the dashboard() route to compute and pass
  new context variables to the template. Do not change any other route.

WORKFLOW:

1. Read dashboard-redesign-plan.md end to end before writing any code.
2. Read dashboard-redesign-mockup.html and note the exact CSS variables
   used (--bg, --ink, --accent, etc.) — port them into the existing
   stylesheet if they aren't already there.
3. Implement in this order so you can test as you go:
   a. Sidebar cleanup (remove Connections from base.html, add to settings)
   b. New DB helpers (just the read-only SELECTs)
   c. metrics.py additions
   d. dashboard() route updates
   e. dashboard.html template
   f. outreach.html, sequences.html, leads.html in any order
   g. Real Estate offer card copy fix
4. After each template change, run the app locally and visually compare
   to the mockup. The mockup is the spec.
5. Commit in small, named chunks: "sidebar cleanup", "funnel viz",
   "pipeline viz", "alert banner", etc. Do NOT squash everything into
   one commit.

VERIFICATION BEFORE YOU CONSIDER YOURSELF DONE:

Run this query and confirm every count is identical to the pre-migration
snapshot the operator gave you:

  SELECT 'sequences' tbl, COUNT(*) n FROM sequences
  UNION ALL SELECT 'sequence_messages', COUNT(*) FROM sequence_messages
  UNION ALL SELECT 'outreach_log', COUNT(*) FROM outreach_log
  UNION ALL SELECT 'outreach_drafts', COUNT(*) FROM outreach_drafts
  UNION ALL SELECT 'email_events', COUNT(*) FROM email_events
  UNION ALL SELECT 'niche_offers', COUNT(*) FROM niche_offers
  UNION ALL SELECT 'scrape_history', COUNT(*) FROM scrape_history
  UNION ALL SELECT 'csv_sheets', COUNT(*) FROM csv_sheets
  UNION ALL SELECT 'settings', COUNT(*) FROM settings;

If a single number differs, STOP and report it. Do not "fix" it.

Then walk through the acceptance criteria in plan section 10 and confirm
each one passes.
```

### Step 3.4 — Let Claude Code work

Watch what it does. Things to interrupt and stop on if you see them:
- `ALTER TABLE` in any new code — stop, that's a violation
- `DROP TABLE`, `DROP COLUMN`, `DROP INDEX` — stop
- `DELETE FROM` in any non-test code — stop
- Touching `init_db()` — stop
- Reformatting `db.py` (it should only have lines ADDED at the bottom, no existing lines reformatted)

If any of those happen, tell it to revert and remind it of the safety rules.

---

## 4. Post-Migration Verification (Run These Yourself)

### Step 4.1 — Re-run the row count snapshot
```bash
sqlite3 data/outreach.db <<'EOF'
.headers on
.mode column
SELECT 'sequences' tbl, COUNT(*) n FROM sequences
UNION ALL SELECT 'sequence_messages', COUNT(*) FROM sequence_messages
UNION ALL SELECT 'outreach_log', COUNT(*) FROM outreach_log
UNION ALL SELECT 'outreach_drafts', COUNT(*) FROM outreach_drafts
UNION ALL SELECT 'email_events', COUNT(*) FROM email_events
UNION ALL SELECT 'niche_offers', COUNT(*) FROM niche_offers
UNION ALL SELECT 'scrape_history', COUNT(*) FROM scrape_history
UNION ALL SELECT 'csv_sheets', COUNT(*) FROM csv_sheets
UNION ALL SELECT 'settings', COUNT(*) FROM settings;
EOF
```
Compare to your pre-migration snapshot from Step 2.5. Every number must match.

### Step 4.2 — Check the schema didn't drift
```bash
sqlite3 data/outreach.db ".schema" > /tmp/schema-after.sql
sqlite3 data/outreach.db.backup-* ".schema" > /tmp/schema-before.sql
diff /tmp/schema-before.sql /tmp/schema-after.sql
```
The diff should be empty. If anything appears, you know exactly what changed.

### Step 4.3 — Spot-check a row that matters
Pick one of your sent emails (e.g. nGO Plumbing — opened) and verify it's still there:
```bash
sqlite3 data/outreach.db \
  "SELECT lead_email, business_name, status, opens FROM outreach_log
   WHERE lead_email LIKE '%ngoplumbing%';"
```
Should return the same row(s) as before.

### Step 4.4 — Boot the app and click through every page
```bash
python -m src.web.app
```
Visit each page and confirm:
- Dashboard renders the funnel, KPIs, alert banner, pipeline
- Outreach log still shows all 24 sends
- Sequences page renders (will show 0 active until the auto-enrol PR ships)
- Leads page shows your 3 CSVs as cards
- Settings has the Connections block (moved from sidebar)
- Sidebar has NO Connections section anywhere except inside Settings

### Step 4.5 — Diff the code yourself
```bash
git diff main...feat/dashboard-redesign --stat
```
Should be mostly templates + a few new functions in db.py / metrics.py / one route in app.py. If you see changes to scraper.py, sequencer.py, resend_send.py, gmail.py, supabase_sync.py, or jobs.py — those are out of scope for this PR. Question them.

---

## 5. If Something Goes Wrong — Rollback in 30 Seconds

### Total rollback (worst case)
```bash
# 1. Stop the app
Ctrl+C in the Flask terminal

# 2. Restore the database
cp data/outreach.db.backup-<your-timestamp> data/outreach.db

# 3. Drop the branch
git checkout main
git branch -D feat/dashboard-redesign

# 4. Restart
python -m src.web.app
```
You're back to exactly where you were before you started.

### Partial rollback (just the DB, keep the code)
If the code is fine but you accidentally lost some rows somehow:
```bash
cp data/outreach.db.backup-<your-timestamp> data/outreach.db
```
The code stays, the data is restored.

### Restore CSVs
```bash
tar -xzf data-snapshot-<your-timestamp>.tar.gz
```

---

## 6. When to Merge to Main

Only after all of these are true:
- [ ] Step 4.1 row counts match exactly
- [ ] Step 4.2 schema diff is empty
- [ ] You've clicked through every page and they render correctly
- [ ] Step 4.5 diff stat shows only the files in the "may modify / may add" allow-list
- [ ] The acceptance criteria in plan section 10 all pass
- [ ] You've run a real send through Outreach (one lead, not 24) and confirmed:
  - It writes to `outreach_log` correctly
  - The dashboard funnel updates
  - The activity log shows the new row

Then:
```bash
git checkout main
git merge --no-ff feat/dashboard-redesign
```

---

## 7. The Two Plans Are Independent — Order Doesn't Matter

You have two pending plans:

| Plan | What it does | Touches DB? |
|---|---|---|
| **dashboard-redesign-plan.md** (this one) | UI redesign, viz, alerts | No (read-only) |
| **auto-sequence-plan.md** (from earlier) | Auto-enrol every send into 7-step sequence | Yes (reads + writes existing tables, no schema change) |

**Ship them as separate PRs.** Either order works:

- **Dashboard first** → you immediately see your existing 24 sends in a better-organized UI and the failure alert tells you what to fix. Then ship auto-enrol once delivery is healthy.
- **Auto-enrol first** → your sends start enrolling into sequences, then the dashboard redesign visualizes the new pipeline data.

I'd recommend dashboard first because it surfaces the 75% failure rate as a screaming red banner, which gives you visibility into what's actually broken with Resend before you 7x your send volume through auto-enrol.

---

## 8. Common Things Claude Code Might Get Wrong (Watch For These)

- **Reformatting `db.py`** instead of just appending. If you see hunks in the diff that aren't pure additions, push back.
- **Adding a "data-cleanup" migration** "while we're in here." Veto immediately.
- **Adding columns to `outreach_log`** to track new metrics. The new metrics are computed on the fly from existing columns + `email_events`. No new columns needed.
- **Creating a new `data/outreach.db` if the old one is missing.** If Claude Code somehow can't find the DB, the answer is "look harder," not "create one." Fail loud.
- **Touching `home-services-offer.md` or `real-estate-offer.md`.** Those are content, not code. Untouched.

---

*Hand this whole doc + the plan + the mockup to Claude Code as one bundle. The prompt in section 3.3 is the actual instruction; everything else is reference.*
