# Dashboard Redesign — Implementation Plan

> Hand to Claude Code. Open `dashboard-redesign-mockup.html` in a browser
> first; it shows every change in this plan visually. The mockup is the
> spec — this doc is the file-by-file translation of it.

---

## 1. The Real Problems This Redesign Solves

Pulled from your actual screenshots, not theory:

1. **Failure rate is hidden.** 18 of 24 sends failed (75%) and the dashboard reports "16.7% open rate" without flagging that 75% never delivered. The math is also wrong — 4 opens / 24 sent = 16.7%, but real open rate on *delivered* mail is 4/6 = **66%**. That's a dramatically different story.

2. **Duplicate sends are happening and there's no visibility.** Sterling Real Estate, Sarasota Realty, Calvin Realty, etc. each show up 3× in the outreach log within the same minute (08:04, 08:05, 08:06). This is the duplicate-send bug from the auto-sequence rebuild — the dashboard should detect and surface it.

3. **Sidebar clutter.** The Connections panel (Google / Claude / Resend / Asana / Sign out) renders on every page. It's only useful on Settings.

4. **No data viz anywhere.** Numbers in tiles, tables in tables. No funnels, no sparklines, no per-step pipeline counts, no per-niche comparison.

5. **Real Estate offer card has a copy bug.** The description text reads "The AI writer uses this whenever you scrape **Home Services** leads" — it should say Real Estate.

---

## 2. Sidebar Cleanup (Affects Every Page)

### Files
- `src/web/templates/base.html` (the layout shared across all pages)

### Change
Delete the entire `CONNECTIONS` block from the sidebar:

```
<!-- DELETE this whole section from base.html -->
CONNECTIONS
  Google
  Claude
  Resend
  Asana
  Sign out of Google
```

### Replace with
Move the connection status to the Settings page (it's already partly there). Add a single "Sign out" link in the Settings page footer instead of the sidebar.

### Why
Connection status is set-once-and-forget. Showing it on every page wastes ~120px of vertical sidebar real estate and reads like always-on noise. The mockup removes it cleanly — sidebar now goes Pulse → Pipeline → System and that's it.

### One bonus addition
Add small status badges next to nav items where it matters:
- `Outreach <badge class="warn">18 fail</badge>` when failures > 5 in last 24h
- `Sequences <badge>0</badge>` showing active count

That's the only place in the sidebar where live state should leak.

---

## 3. Dashboard Redesign

### Files
- `src/web/templates/dashboard.html`
- `src/web/app.py` (the `dashboard()` route — needs new context fields)
- `src/web/metrics.py` (new aggregations described below)

### Block-by-block spec

#### Block 1: Critical alert banner (NEW, top of page)

Conditional, rendered only when one of these is true:

```python
# In app.py dashboard route
delivery_rate = (delivered / sent) if sent else 1.0
duplicates = db.detect_duplicate_sends(window_minutes=10)  # NEW helper
recent_failures = db.count_recent_failures(hours=24)

show_alert = delivery_rate < 0.7 or recent_failures > 5 or duplicates
```

Render markup matches mockup:
- Red background (`var(--warn-soft)`)
- Title: "75% of recent sends failed" (compose from data)
- Body: list the specific issue (low delivery / duplicates detected / specific recipients)
- Three actions: `Investigate failures` / `Pause new sends` / `Dismiss`

The duplicate detection query for `db.py`:
```python
def detect_duplicate_sends(window_minutes=10):
    """Return list of (lead_email, send_count) where same email
    received 2+ sends within the time window. Catches the auto-enrol
    duplicate-send bug."""
    with _conn() as c:
        return c.execute("""
            SELECT lead_email, COUNT(*) as n,
                   MIN(sent_at) as first, MAX(sent_at) as last
            FROM outreach_log
            WHERE sent_at >= datetime('now', ? || ' minutes')
            GROUP BY lead_email
            HAVING n >= 2
            ORDER BY n DESC
            LIMIT 20
        """, (-window_minutes,)).fetchall()
```

#### Block 2: KPI grid (REWORK)

Replace the current 4 tiles with these 4, in this order:

| Tile | Number | Subtitle | Style |
|---|---|---|---|
| Total leads | sum of CSVs | "across N CSVs · +X this week" | normal |
| Sent today | count where `sent_at >= today` | "across N niches · avg X/day" | normal + 14-day sparkline |
| **Delivery rate** | `delivered / sent` | "X of Y delivered · target ≥95%" | red if < 70% |
| Replies | `replied` count | "X% rate" | normal |

The Delivery rate tile is the new addition. Right now there's no place on the dashboard that says "of the things you sent, this many actually got there." That metric is the canary.

The 14-day sparkline is a 12-line SVG, no library needed:
```html
<svg viewBox="0 0 200 24" preserveAspectRatio="none">
  <polyline fill="none" stroke="var(--accent)" stroke-width="1.5"
            points="{daily_send_points}"/>
</svg>
```

`metrics.daily_sends_last_n(14)` returns the points string.

#### Block 3: Conversion funnel (NEW)

Six rows, each with a label, horizontal bar, count, and percent. Bars descend from 100% down. Match the mockup colors: green for healthy steps, red for the broken step (delivery), grey for the inactive tail.

Data:
```python
funnel = {
    "scraped":    metrics.total_leads_all_time(),
    "with_email": db.count(where="email IS NOT NULL"),
    "sent":       db.count_outreach(),
    "delivered":  db.count_outreach(status="sent") + db.count_with_event("delivered"),
    "opened":     db.unique_opens(),
    "replied":    db.count_outreach(status="replied"),
}
```

Each bar's width is `(value / max_value) * 100%`. Each row is clickable and filters the outreach log to that status.

Below the funnel, render the projected-open-rate insight box (yellow note bar in mockup):
> **Real open rate is 66%, not 16.7%.** The dashboard divides opens by sent, but should divide by delivered. Once delivery is fixed, projected open rate stays in the 60s.

#### Block 4: Today's queue (REWORK)

Currently shows "Nothing queued" as a giant empty block. Replace with a vertical timeline (matches mockup):

```
● 2 min ago    Last send: Calvin Realty · step 1 · failed
○ in 4h 12m    Step 2 bump → Plumb Medic
○ in 4h 12m    Step 2 bump → Victorian Plumbing
○ tomorrow 09:00   Daily Resend cap resets · 100 sends
```

Filled dot for events that have happened, hollow for queued. Live (current) item gets a green halo.

Data already exists in the `dashboard()` route's `today_queue` — just present it as a timeline not a table.

#### Block 5: 7-step pipeline (NEW)

Seven cards in a row, one per step, showing how many leads currently sit at each step:

```
Day 0    Day 3    Day 7    Day 11   Day 16   Day 21   Day 28
Cold     Bump     FOMO     Loom     Math     Quirky   Pizza
  0        0        0        0        0        0        0
```

Query:
```python
pipeline = db.sequence_step_distribution()
# returns {1: 12, 2: 8, 3: 5, 4: 3, 5: 2, 6: 0, 7: 0}
```

Active steps (count > 0) get the green-tinted background; zero-count steps stay grey. This is the single most useful at-a-glance visual — "where is my pipeline?"

#### Block 6: Performance by niche (NEW)

Two-column grid of niche cards, comparing active campaigns side-by-side. Each card shows: niche name, country, sent / open rate / replied. Footnote line in red if there's a problem with the niche.

Data:
```python
niche_perf = db.outreach_stats_by_niche()
# returns [{niche, country, sent, opened, replied, delivery_rate, ...}]
```

When delivery rate < 50% on a niche, the footnote turns red and surfaces the issue.

#### Block 7: Recent activity grouped by status (REWORK)

Current dashboard shows the last 10 outreach rows in a flat list. The new version groups them by status:

```
⚠ Failed · 18    ← group header
  [row, row, row]
✓ Sent + opened · 4
  [row, row, row]
✓ Sent · 2
  [row, row]
```

Sort group order by severity: failed first, then opened, then plain sent, then queued. Inside each group, sort by recency.

Status chips use the mockup's colors:
- failed: red on light-red
- sent: green on light-green
- opened: blue on light-blue
- replied: gold on cream
- bounced: red on light-red

Every row gets a hover state that opens a side panel with the full email body and the raw Resend response (currently you have to dig into SQLite to see why something failed).

---

## 4. Outreach Page Redesign

### Files
- `src/web/templates/outreach.html`

### Changes

1. **Add the same critical-alert banner at the top** (same component as Dashboard).

2. **Replace the 5-tile campaign analytics block** with a two-column row:
   - Left: funnel chart (sent → delivered → opened → replied)
   - Right: 14-day daily-volume bar chart with today highlighted in red

3. **Outreach log gets filter chips**:
   ```
   [All 24] [Failed 18] [Opened 4] [Sent 2] [Replied 0] [Bounced 0]   [Today] [7d] [30d]
   ```
   Filter chips replace the current "last 50" hard limit.

4. **Duplicate detection inline**: when the same recipient appears 2+ times in the visible window within 10 minutes, render an inset note row:
   ```
   ↳ Same recipient, sent 3× at 08:04, 08:05, 08:06 — duplicate-send bug
   ```
   Mockup shows this on the Sterling Real Estate row.

5. **Row hover → side panel** with full email body + Resend response JSON (so you can finally see *why* a send failed without opening a SQL client).

---

## 5. Sequences Page Redesign

### Files
- `src/web/templates/sequences.html`

### Changes

1. **Top stat row**: keep Active / Replies / Sent / Open rate but use the same KPI card style as Dashboard (Fraunces serif numbers, mono labels).

2. **Add cadence timeline (NEW)**: SVG showing the 7-step path with day offsets and the "what each step sells" subtext (Sells problem / Sells loss / Sells math / Sells closure). See mockup.

3. **Add step distribution (NEW)**: same 7-card pipeline as Dashboard block 5, but bigger.

4. **Sequence list table**:
   - Replace the text-based step column with the 7-dot progress indicator (●●●○○○○ pattern). Filled dot for completed, halo dot for current, empty for upcoming.
   - Empty state when no sequences: large centered message with Fraunces title "No active sequences" and a CTA pointing to Outreach.

---

## 6. Leads Page Redesign

### Files
- `src/web/templates/leads.html`

### Changes

Current: flat table with CSV name, source, rows, avg score, qualified, w/ email, w/ phone columns.

New: card grid, one card per CSV. Each card shows:
- CSV filename (mono, can wrap)
- Source · row count · age
- **Health score** (big number, color-coded green / amber / red)
- Four mini progress bars: w/email, w/phone, avg score, qualified
- Footer line: send activity for that CSV with status hint

### Health score formula
```python
def csv_health_score(csv):
    email_pct  = csv.with_email / max(csv.rows, 1)
    phone_pct  = csv.with_phone / max(csv.rows, 1)
    score_pct  = csv.avg_score / 100
    fresh      = 1.0 if csv.scraped_within_7d else 0.6
    return round(40*email_pct + 25*phone_pct + 25*score_pct + 10*fresh)
```

Bands:
- 85+ excellent (green)
- 70–84 fine (amber)
- below 70 needs review (red)

### Why this matters here
Right now `interactive_real_estate_us.csv` has 7/11 emails (and one of those is `accessibility@ubc.ca` — which is why the Real Estate batch failed). A card with a red 58 score and "email gap" footnote tells you that before you click "Send all" against a half-broken CSV.

---

## 7. Offers Page — One-Line Bug Fix

### File
- `src/web/templates/offers.html` (or wherever the on-disk brief description is rendered — possibly in `app.py` route)

### Change
The Real Estate card currently reads:

> "Markdown playbook shipped with the project (32.5 KB). The AI writer uses this whenever you scrape **Home Services** leads."

Should read:

> "Markdown playbook shipped with the project (32.5 KB). The AI writer uses this whenever you scrape **Real Estate** leads."

Likely a hardcoded string that wasn't templated when the second on-disk brief was added. Search for "scrape Home Services leads" — should be one or two hits.

---

## 8. New / Updated DB Helpers

Add to `src/web/db.py`:

```python
def detect_duplicate_sends(window_minutes=10):
    """Find emails sent 2+ times to the same recipient within window."""
    # SQL above

def count_recent_failures(hours=24):
    """Count outreach_log rows with status='failed' in last N hours."""
    
def outreach_stats_by_niche():
    """Group outreach_log + sequences by niche, return per-niche
    sent/delivered/opened/replied counts plus delivery rate."""

def sequence_step_distribution():
    """Return {step_number: active_count} for the pipeline visual."""

def csv_send_summary(csv_path):
    """For a CSV, return {sent, delivered, failed, opened, replied}
    so the Leads card footer can show send health."""
```

Add to `src/web/metrics.py`:

```python
def daily_sends_last_n(n=14):
    """Return list of {date, count} for the sparkline."""

def csv_health_score(csv_summary):
    """Health score formula above."""
```

---

## 9. Design System Notes (Match the Mockup Exactly)

The mockup uses these CSS variables — port them into the existing stylesheet if they're not already there:

```css
--bg:#faf9f6;        --bg-soft:#f3f1ec;    --card:#ffffff;
--ink:#1a1a1a;       --ink-soft:#4a4a4a;   --ink-mute:#8a8a8a;
--ink-faint:#b8b8b8; --line:#e8e6e1;       --line-soft:#f0eee8;
--accent:#2d5a3f;    --accent-soft:#e8f0ea;
--warn:#c44536;      --warn-soft:#fdecea;  --warn-line:#f5c6c0;
--info:#3b6ea8;      --info-soft:#eaf1f9;  --good:#3d7a52;
--font-serif:'Fraunces',Georgia,serif;
--font-mono:'JetBrains Mono',ui-monospace,monospace;
--font-sans:'Inter',-apple-system,sans-serif;
--radius:8px;
```

Typography rules:
- Page H1 → Fraunces 36/500/-0.02em
- Block titles → Fraunces 18/500/-0.01em
- KPI numbers → Fraunces 32/500/-0.02em
- Section labels → JetBrains Mono 10/uppercase/0.08em letter-spacing
- Body → Inter 14/450
- Mono technical text → JetBrains Mono 12/400

Block pattern (used everywhere):
```html
<div class="block">
  <div class="block-head">
    <div>
      <div class="block-title">Title</div>
      <div class="block-sub">Optional subtitle</div>
    </div>
    <div class="block-actions"><button class="alert-btn">Action</button></div>
  </div>
  <div class="block-body">...</div>
</div>
```

No nested boxes-within-boxes. Type carries hierarchy.

---

## 10. Acceptance Criteria

A reviewer should be able to verify all of these in a single browse session:

- [ ] Sidebar shows Pulse / Pipeline / System and nothing else. No Connections panel anywhere except Settings.
- [ ] Dashboard renders the red alert banner when delivery rate is below 70%, with "Investigate failures" / "Pause sends" / "Dismiss" actions.
- [ ] The 4 KPI tiles are: Total leads, Sent today (with sparkline), Delivery rate (red when low), Replies — in that order.
- [ ] Conversion funnel renders 6 rows with correct percentages relative to "Scraped". Delivery row is red when below 70%.
- [ ] The 7-step pipeline shows correct counts per step (will be 0 across the board until the auto-enrol PR ships).
- [ ] Niche performance cards render side-by-side with sent/open/replied per niche.
- [ ] Recent activity is grouped by status with chip-colored headers.
- [ ] Outreach page filter chips show correct counts and filter the table when clicked.
- [ ] Outreach page detects and inlines duplicate-send notes for repeat recipients.
- [ ] Sequences page renders the cadence timeline SVG and step distribution.
- [ ] Sequence rows use the 7-dot progress indicator instead of "Step 3 of 7" text.
- [ ] Leads page is a card grid, not a flat table. Each card shows a health score with the right color band.
- [ ] Real Estate offer card description references "Real Estate" not "Home Services".
- [ ] All four KPI tiles, the funnel, the pipeline, and the niche grid use Fraunces for numbers and JetBrains Mono for labels.

---

## 11. Out of Scope for This PR

- Scrape page redesign (it works — the form pattern is fine)
- Scrape history redesign (it works — the table is fine)
- Mobile responsiveness (the app is desktop-only by design)
- Dark mode
- Real-time websocket updates (current 5-second auto-refresh is fine)
- The auto-sequence-rebuild from the prior plan (separate PR — they can ship independently)

---

## 12. One-line PR title

> Dashboard redesign: kill sidebar Connections, add funnel + pipeline + niche viz, surface failed-delivery and duplicate-send alerts, replace flat tables with grouped activity views.
