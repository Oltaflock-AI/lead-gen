# Claude Code Prompt — Dashboard Page Only

> Copy everything below the line into Claude Code in VS Code. The mockup
> file (`dashboard-redesign-mockup.html`) and the plan
> (`dashboard-redesign-plan.md`) should be visible in the project so
> Claude Code can read them.

---

Implement ONLY the Dashboard page redesign. Do not touch any other page,
route, or template. Use `dashboard-redesign-mockup.html` as the visual
spec — open it in a browser, click "Dashboard" in the sidebar, and match
that layout exactly. Use `dashboard-redesign-plan.md` section 3 as the
written spec where it adds detail beyond the mockup.

## SCOPE — files you may modify

ONLY these files. If you find yourself wanting to edit anything else,
stop and ask.

1. `src/web/templates/dashboard.html` — full rewrite to match the mockup
2. `src/web/app.py` — modify ONLY the `dashboard()` route to compute and
   pass new context variables. Do not change any other route, helper, or
   import block beyond what the new context requires.
3. `src/web/db.py` — APPEND new SELECT-only helper functions at the
   bottom. Do not edit any existing function, do not reformat existing
   code, do not touch `init_db()`, do not run `ALTER TABLE` anywhere.
4. `src/web/metrics.py` — APPEND new aggregation functions. Same rules
   as `db.py` — additions only, no edits to existing code.
5. `src/web/static/` — add CSS for the new dashboard components if they
   aren't already in the stylesheet. Match the CSS variables in the
   mockup file (`--bg`, `--ink`, `--accent`, etc.). Do not rename or
   delete existing CSS classes.

## SCOPE — files you must NOT touch

- `src/web/templates/base.html` — the sidebar cleanup is a SEPARATE PR.
  Leave the existing sidebar alone, including the Connections panel if
  it's there.
- `src/web/templates/outreach.html`, `sequences.html`, `leads.html`,
  `offers.html`, `settings.html`, `scrape.html`, `scrape_history.html`
- `src/web/sequencer.py`, `src/web/resend_send.py`,
  `src/web/supabase_sync.py`, `src/web/jobs.py`, `src/web/gmail.py`,
  any scraper file
- `home-services-offer.md`, `real-estate-offer.md`, `CLAUDE.md`,
  `AGENTS.md`, `README.md`
- `data/outreach.db` (READ-ONLY for this PR)
- CSV files in `data/outputs/` and `data/imports/` (READ-ONLY)
- `.env`

## DATA-SAFETY RULES

The SQLite database at `data/outreach.db` is READ-ONLY for this PR.
Forbidden operations anywhere in your changes:

- `ALTER TABLE` of any kind
- `DROP TABLE`, `DROP INDEX`, `DROP COLUMN`
- `CREATE TABLE` (except inside `init_db()` which you must not touch)
- `DELETE FROM` in any non-test code
- `UPDATE` of existing rows
- New columns on any table
- New `CHECK` constraints

The new helpers in `db.py` are pure SELECTs. If you cannot get the
data with a SELECT, ask the operator before adding a write path.

## WHAT TO BUILD — Dashboard structure

The redesigned Dashboard has exactly TWO visible regions below the page
title. Read this twice before writing any HTML.

### Region 1 (top): time-window pills + 4 KPI tiles

A row of 4 pills (Today / 7d / 30d / All) drives the data window.
Below it, exactly 4 tiles in this order:

1. Total leads — sum across CSVs · "across N CSVs" subtitle · ALWAYS all-time, never windowed
2. Sent today — count in window · "avg X/day · Y-day window" subtitle · sparkline below the meta line
3. Delivery rate — `delivered / sent` × 100 · subtitle is "X of Y delivered" with optional " · Z bounced" appended in red when bounce rate > 5%, hidden when bounces == 0
4. Open rate — `unique_opens / delivered` × 100 (denominator is delivered, NOT sent) · subtitle is "X of Y delivered · Z clicks"

Bounce rate is NOT a separate tile. Click rate is NOT a separate tile.
Replies is NOT a separate tile. The mockup is correct — match it.

### Region 2 (below KPIs): one block with three inner tabs

ONE `<div class="block">` containing a tab strip and three panels:

- Activity tab — chip filter row + table of recent sends with status chips. Show 8–10 rows, end with a "View all N →" link that goes to `/outreach`. Filter chips: All / Opened / Clicked / Sent / Bounced / Replied. Status chips reuse the existing `.chip` classes — do not introduce new colors.
- Pipeline tab — 7-step pipeline grid (Day 0 Cold → Day 28 Pizza) showing current counts. Below it, a "Next 24 hours" timeline that renders ONLY when there's something in the next 24h. If empty, the entire sub-section disappears (no empty-state placeholder).
- Issues tab — stack of issue cards with severity colors. Three issue types per the plan section 3: bounce rate over threshold (red), suspicious leads in CSV (amber), no duplicate sends in 24h (green ✓ "all clear" — always renders, but doesn't count toward the tab badge).

The tab strip count badges are live. Issues tab badge gets the warn
class (red) only when issues with severity != "ok" exist; otherwise
renders as a normal tab with a neutral count.

### What the OLD dashboard had that must NOT appear in the new one

These are deliberate removals. Do not implement any of them:

- ❌ Standalone "Conversion funnel" block
- ❌ Standalone "Today's queue" empty-state block
- ❌ Standalone "Active sequences pipeline" block (it moves into the Pipeline tab)
- ❌ Standalone "Performance by niche" block
- ❌ Standalone "Recent activity" table block (it moves into the Activity tab)
- ❌ "Per-CSV breakdown" expandable footer
- ❌ Bounce rate tile (folded into Delivery)
- ❌ Click rate tile (folded into Open)
- ❌ Replies tile (visible via chip filter in Activity)
- ❌ The "Showing metrics for · funnel + niche + sparkline use this window" loose text line

If your draft has any of those, you've drifted from the spec. Re-read
the mockup.

## NEW HELPER FUNCTIONS TO ADD

Append to `src/web/db.py` (SELECT-only):

```python
def detect_duplicate_sends(window_minutes=10):
    """Return list of (lead_email, count, first, last) for emails sent
    2+ times to the same recipient within the window. Used by the
    Issues tab to surface the duplicate-send bug."""

def count_recent_failures(hours=24):
    """Count outreach_log rows with status='failed' in last N hours."""

def recent_bounces(window_minutes=1440):
    """Return list of recently bounced leads with email + business_name."""

def detect_likely_fake_prospects(window_minutes=1440):
    """Heuristic match against accessibility@, info@<edu domain>, and
    other role-account patterns. Returns list of recently emailed leads
    that look misclassified. Read-only SELECT against outreach_log."""

def sequence_step_distribution():
    """Return {step_number: active_lead_count} for the 7 steps. Reads
    sequences.current_step where status='active'."""

def activity_feed(window_start_iso, limit=10, status_filter=None):
    """Recent outreach with status chips for the Activity tab."""
```

Append to `src/web/metrics.py`:

```python
def daily_sends_last_n(n=7):
    """Return list of (date, count) for the sparkline. Used by the
    Sent today tile."""

def bounce_rate(window_minutes=1440):
    """Return float 0.0–1.0. Used by Delivery tile bounce-detail and
    Issues tab threshold check."""

def open_rate_on_delivered(window_minutes=1440):
    """unique_opens / delivered_count. Returns 0.0 when delivered == 0.
    NEVER divide by sent — that's the old buggy metric."""
```

## DASHBOARD ROUTE CHANGES

In `src/web/app.py`, the `dashboard()` route needs new context fields.
Add them, do not remove existing fields (other templates may reference
them — though only `dashboard.html` should after this PR, leaving them
costs nothing). New context:

```python
window = request.args.get('window', 'today')  # today | 7d | 30d | all
window_start = _resolve_window_start(window)

context.update({
    "window": window,
    "kpis": {
        "total_leads": metrics.total_leads_all_time(),
        "sent_in_window": db.count_outreach_since(window_start),
        "delivery_rate": metrics.delivery_rate(window_start),
        "bounce_count": db.count_bounced_since(window_start),
        "open_rate": metrics.open_rate_on_delivered(window_start),
        "click_count": db.count_clicks_since(window_start),
        "sparkline_points": metrics.daily_sends_last_n(7),
    },
    "activity": db.activity_feed(window_start, limit=10),
    "activity_counts_by_status": db.activity_counts_by_status(window_start),
    "pipeline_distribution": db.sequence_step_distribution(),
    "next_24h_queue": db.upcoming_sends(hours=24),
    "issues": detect_issues(),  # see plan section 3, Tab C
})
```

`_resolve_window_start` is a small new helper at module level — convert
the window string to an ISO timestamp. Keep it private.

## WORKFLOW

1. Read `dashboard-redesign-mockup.html` end to end. Open it in a browser.
2. Read `dashboard-redesign-plan.md` section 3 fully.
3. Implement in this order, committing after each step:
   a. `db.py` and `metrics.py` additions (no UI yet)
   b. `dashboard()` route updates in `app.py`
   c. CSS additions in `static/`
   d. `dashboard.html` rewrite — Region 1 first (KPIs), then Region 2 (tabbed block)
4. Run the app locally after step (a) to confirm helpers don't crash on
   real data. Run after step (d) to confirm the page renders.
5. Click through every other page in the app and confirm none of them
   broke. None of them should have changed visually.

## VERIFICATION BEFORE YOU CONSIDER YOURSELF DONE

Run the row count snapshot the operator gave you, exact same query as
pre-migration. Every count must match. Then walk these checks:

- [ ] Open `/dashboard` — see exactly 4 KPI tiles + ONE tabbed block.
      No standalone funnel, queue, niche, or activity blocks.
- [ ] Click each of the 4 window pills — KPI numbers update.
- [ ] Click Activity / Pipeline / Issues tabs — content swaps.
- [ ] Open `/outreach`, `/sequences`, `/leads`, `/offers`,
      `/scrape`, `/scrape-history`, `/settings` — all unchanged from
      before this PR.
- [ ] `git diff main...HEAD --stat` shows changes ONLY in:
      dashboard.html, app.py, db.py, metrics.py, static/. Anything
      else in the diff is a violation of scope.
- [ ] Schema diff is empty:
      `diff <(sqlite3 data/outreach.db.backup-* '.schema') \
            <(sqlite3 data/outreach.db '.schema')`

If any check fails, revert and ask. Don't "fix forward" on scope drift.
