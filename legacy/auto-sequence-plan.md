# Auto-Sequence Rebuild — Implementation Plan

> Hand this to Claude Code in the `lead-gen` repo. Single brief, single PR.
> Goal: every cold email sent through the app enrols the lead into a 7-step
> drip automatically. No manual "enqueue" step. Each step is a different
> angle, hyper-personalized, with a Hormozi-grade offer and a quirky
> breakup. Five-to-seven touches per lead, never less.

---

## 1. The Problem to Fix

Right now there are two parallel send paths and they don't talk to each other:

| Path | What it does | What it's missing |
|---|---|---|
| `/api/outreach/send` (Outreach page) | One-shot send via Resend, writes `outreach_log` | **Does not enrol into `sequencer`. The lead never gets follow-ups.** |
| `enqueue_lead()` in `sequencer.py` (Sequences page) | Drafts 5 steps, schedules day 0/3/6/10/14 | Used only when the operator clicks "Enqueue → sequence" manually. Most leads never hit it. |

This means every campaign is leaking 70%+ of replies. Industry data: replies 2–7 in a 7-touch sequence add up to ~3× the reply rate of touch 1 alone. We're shipping touch 1 and walking away.

**The fix is one architectural change plus a content rebuild:**
1. Make `/api/outreach/send` the **only** send path. Auto-enrol on first send.
2. Bump `NUM_STEPS` from 5 to 7. Re-pace cadence over ~28 days.
3. Rewrite the per-step prompt so each email has a distinct angle and the offer hits like a grand-slam.
4. Add the "reply pizza" breakup as step 7, with optional meme image support.

---

## 2. Final Architecture (After This PR)

```
Outreach page → "Send all"
    │
    ▼
/api/outreach/send  ← single entry point for ALL sends
    │
    ├── For each lead:
    │     1. Resolve niche from CSV's Business Type column (or override)
    │     2. Check if active sequence already exists for this email → skip if yes
    │     3. Call sequencer.enqueue_lead_with_first_send(lead, draft)
    │             ├── creates sequences row
    │             ├── persists ALL 7 step drafts up-front
    │             ├── sends step 1 NOW via Resend
    │             ├── marks step 1 sent, schedules step 2 in 3 days
    │             └── writes outreach_log row (so the existing UI still works)
    │
    └── Returns same response shape as today (so Outreach.html doesn't change)

Daemon scheduler (already running) handles steps 2–7 automatically.
Reply detection (Gmail + Resend webhooks) pauses the sequence on any reply.
```

The existing `/api/sequences/start-from-csv` stays — it's now redundant for
new leads but useful for back-filling already-sent CSVs.

---

## 3. The 7-Step Cadence

| Step | Day | Word count | Angle | Subject style |
|---|---|---|---|---|
| 1 | 0 | 90–140 | Cold open + risk-reversal offer | Specific observation, lowercase |
| 2 | +3 | 35–70 | Bump with one outcome stat | Self-aware ("still on a roof?") |
| 3 | +7 | 90–140 | Competitor / FOMO ("a {{type}} two suburbs over...") | Pattern interrupt |
| 4 | +11 | 70–110 | Loom value drop | "90 seconds, then you decide" |
| 5 | +16 | 90–140 | **Grand-slam offer recap** ("would feel stupid saying no") | Outcome-led number |
| 6 | +21 | 60–100 | **Quirky pattern interrupt** (optional meme image) | Curiosity gap |
| 7 | +28 | 35–70 | **Pizza breakup** | "one word: pizza" |

Span: 28 days. Set as `STEP_OFFSETS_DAYS = {1: 0, 2: 3, 3: 7, 4: 11, 5: 16, 6: 21, 7: 28}`.

**Why these gaps:** 3-3-4-4-5-5-7 spreads density toward the end so a non-replier
gets two full weeks of breathing room before the breakup. Avoids the spam-trap
look of 5 touches in 14 days.

---

## 4. File-by-File Changes

### 4.1 `src/web/sequencer.py`

**Change `NUM_STEPS` and cadence:**
```python
STEP_OFFSETS_DAYS = {1: 0, 2: 3, 3: 7, 4: 11, 5: 16, 6: 21, 7: 28}
NUM_STEPS = 7
```

**Add new `_STEP_INSTRUCTIONS` for steps 5, 6, 7.** Replace the dict wholesale.
Step 4 stays Loom (currently step 4). Step 5/6/7 are net-new angles.

```python
_STEP_INSTRUCTIONS = {
    1: (
        "Step 1 — first cold email (day 0, 90–140 words). Lead with a "
        "SPECIFIC observation pulled from the lead facts (rating + review "
        "count + city + niche). One short paragraph naming the missed-call "
        "pain. Deliver the offer with the risk-reversal verbatim ('you "
        "don't pay until we book a job, $100 back if we don't book one in "
        "30 days'). Soft CTA: short reply or 10-minute call."
    ),
    2: (
        "Step 2 — bump (day 3, 35–70 words). Acknowledge no reply in ONE "
        "casual line. Drop a single concrete outcome framed generically: "
        "e.g. 'a {business_type} with a similar review count went from "
        "missing 1 in 4 calls to under 1 in 30 in week one'. End with a "
        "one-line question. No re-pitch of the offer."
    ),
    3: (
        "Step 3 — competitor / FOMO angle (day 7, 90–140 words). Open with "
        "the observation that another {business_type} in the same region "
        "(do not name them) is already running an AI agent and capturing "
        "the after-hours jobs that used to slip past phones like "
        "{business_name}'s. Use the localized stat from the playbook to "
        "ground the cost. Tie back to the risk-reversal in one sentence. "
        "CTA: 'want me to send you the 90-second walkthrough?'"
    ),
    4: (
        "Step 4 — Loom value drop (day 11, 70–110 words). Paste the Loom "
        "URL on its own line. Tease ONE concrete thing the video shows for "
        "THIS niche (e.g. how the agent triages a burst pipe vs. a slow "
        "drip for a plumber). CTA: reply 'yes' to talk after watching."
    ),
    5: (
        "Step 5 — grand-slam offer recap (day 16, 90–140 words). Frame the "
        "math so plainly that NOT trying it looks like the riskier choice. "
        "Structure: (1) one line naming what they're losing per week in "
        "raw money terms using the localized job-value range. (2) the "
        "offer restated in three short lines, $0 setup, $0 monthly, "
        "$100-back guarantee. (3) one line: 'the only way you lose money "
        "is if it works and you stop us.' CTA: ten minutes this week, "
        "their pick of day. NO buzzwords. NO hype words. Just arithmetic."
    ),
    6: (
        "Step 6 — quirky pattern interrupt (day 21, 60–100 words). Drop "
        "tone. Open with a self-aware one-liner that admits they've been "
        "ignoring the thread, e.g. 'either my emails are landing in spam "
        "or {business_name} doesn't actually want more booked jobs, and I "
        "genuinely can't tell which.' Then ONE crisp benefit line tied to "
        "the niche. CTA must be a binary low-effort reply: 'reply yes if "
        "you want a 5-min walkthrough, reply no and I close the loop'. "
        "This is the email the operator may attach a meme image to "
        "(handled at send time, not in this draft)."
    ),
    7: (
        "Step 7 — pizza breakup (day 28, 35–70 words). Last note. Polite, "
        "short, leaves the door open. Include this exact mechanic verbatim: "
        "'Reply with one word and I'll act on it: PIZZA means stop, I won't "
        "email again. CALL means book a 10-minute slot. LATER means I "
        "circle back in 90 days.' One stat-free sentence above it framing "
        "why it still matters. Nothing else."
    ),
}
```

**Critical:** the `_SYSTEM` prompt currently says "5-step drip sequence". Change
that string to "7-step drip sequence".

**Add new function: `enqueue_lead_with_first_send`**

This is the new public API. It does what `enqueue_lead` does PLUS sends step 1
synchronously and returns the Resend ID so the caller can write `outreach_log`:

```python
def enqueue_lead_with_first_send(lead, csv_name="", sender_name="",
                                  override_subject=None, override_body=None):
    """Create sequence, draft all 7 steps, send step 1 NOW, schedule 2-7.

    If override_subject/override_body are provided (i.e. the operator
    edited the draft on the Outreach page), step 1 uses those instead of
    re-drafting. Steps 2-7 are still LLM-drafted up front.

    Returns dict:
      {
        "sequence_id": int | None,
        "resend_id": str | None,
        "status": "queued" | "already_active" | "skipped" | "error",
        "error": str | None,
      }
    """
    email = (lead.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return {"status": "error", "error": "invalid email",
                "sequence_id": None, "resend_id": None}

    # Idempotency: if an active sequence already exists, do not re-enrol.
    existing = db.get_active_sequence_by_email(email)  # NEW helper, see db.py
    if existing:
        return {"status": "already_active",
                "sequence_id": existing["id"], "resend_id": None,
                "error": None}

    niche = lead.get("niche", "")
    if not db.get_niche_offer(niche):
        return {"status": "error",
                "error": f"no offer for niche '{niche}'",
                "sequence_id": None, "resend_id": None}

    sid = db.create_sequence(
        lead_email=email, business_name=lead.get("business_name", ""),
        niche=niche, csv_name=csv_name, city=lead.get("city", ""),
    )

    # Draft all 7 steps. Step 1 may be overridden by operator-edited copy.
    drafts = draft_all_steps(lead, niche, sender_name=sender_name)
    if override_subject and override_body:
        drafts[0] = {"subject": override_subject, "body": override_body}

    base = datetime.now(timezone.utc)
    for step in range(1, NUM_STEPS + 1):
        d = drafts[step - 1]
        scheduled = _add_days_iso(STEP_OFFSETS_DAYS[step], base)
        db.upsert_sequence_message(sid, step, d["subject"], d["body"], scheduled)

    # Send step 1 synchronously so the operator sees instant feedback.
    seq = db.get_sequence(sid)
    ok, err = _send_step(seq, 1)
    if not ok:
        return {"status": "error", "error": err,
                "sequence_id": sid, "resend_id": None}

    # Pull the resend_id we just stamped onto the message row.
    msg1 = db.get_sequence_message(sid, 1)
    advance_after_send(seq, 1)

    return {"status": "queued", "sequence_id": sid,
            "resend_id": msg1.get("resend_id"), "error": None}
```

**Keep the old `enqueue_lead` for the Sequences page back-fill flow.**
It's not deleted, just no longer the primary path.

### 4.2 `src/web/db.py`

Add one helper:

```python
def get_active_sequence_by_email(email):
    """Return the currently-active sequence row for this email, or None."""
    if not email: return None
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequences "
            "WHERE lower(lead_email) = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row else None


def get_sequence_message(sequence_id, step):
    """Return the message row for one (sequence_id, step), or None."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sequence_messages "
            "WHERE sequence_id = ? AND step_number = ?",
            (sequence_id, step),
        ).fetchone()
        return dict(row) if row else None
```

**Schema migration check:** the existing `sequence_messages` table
already supports arbitrary `step_number`, so no DDL change needed for
going 5→7. Just verify with `PRAGMA table_info(sequence_messages);` and
make sure no CHECK constraint caps step_number ≤ 5. If there is one,
ALTER it out.

### 4.3 `src/web/app.py` — `/api/outreach/send`

This is the heart of the change. Replace the loop body:

```python
@app.route("/api/outreach/send", methods=["POST"])
def api_outreach_send():
    if not resend_send.is_configured():
        return jsonify({"error": "Resend not configured."}), 400

    data = request.json or {}
    sends = data.get("sends", [])
    csv_path = data.get("csv_path", "")
    settings = db.get_settings()
    sender = settings.get("sender_name", "")

    # Load the CSV once so we can map email -> full lead row (for niche
    # detection, rating, review count, city — all needed by the sequencer
    # to draft steps 2-7 with personalization).
    leads_by_email = {}
    if csv_path:
        leads_by_email = _load_leads_by_email(csv_path)  # NEW small helper

    results = []
    for s in sends:
        to_addr = s.get("to") or s.get("email")
        subject = s.get("subject", "")
        body = s.get("body", "")
        business = s.get("business_name", "")
        if not (to_addr and subject and body):
            results.append({"to": to_addr, "ok": False,
                            "error": "missing fields"})
            continue

        # Build the lead dict for the sequencer. Pull from CSV if available.
        lead = leads_by_email.get(to_addr.lower(), {})
        lead = {**lead,
                "email": to_addr,
                "business_name": business or lead.get("business_name", ""),
                "niche": lead.get("niche") or lead.get("business_type", ""),
                "city": lead.get("city", ""),
                "rating": lead.get("rating", 0),
                "review_count": lead.get("review_count", 0)}

        result = sequencer.enqueue_lead_with_first_send(
            lead, csv_name=csv_path, sender_name=sender,
            override_subject=subject, override_body=body,
        )

        if result["status"] in ("queued", "already_active"):
            # Mirror to outreach_log so the existing campaign analytics work.
            if result["resend_id"]:
                db.log_outreach(
                    to_addr, business, csv_path, subject, body,
                    gmail_message_id=f"resend:{result['resend_id']}",
                    status="sent", resend_id=result["resend_id"],
                )
            results.append({
                "to": to_addr, "ok": True,
                "message_id": result["resend_id"],
                "sequence_id": result["sequence_id"],
                "note": "enrolled in 7-step sequence"
                        if result["status"] == "queued"
                        else "already in active sequence",
            })
        else:
            db.log_outreach(to_addr, business, csv_path, subject, body,
                            gmail_message_id=None, status="failed")
            results.append({"to": to_addr, "ok": False,
                            "error": result["error"]})

    return jsonify({"results": results})
```

**Helper to add at the top of `app.py`:**
```python
def _load_leads_by_email(csv_name):
    """Index a CSV by lowercased email so /api/outreach/send can pull
    rating, review count, city, business type for the sequencer."""
    p = _resolve_csv_path(csv_name)
    if not p: return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            em = (row.get("Email") or "").strip().lower()
            if not em: continue
            out[em] = {
                "email": em,
                "business_name": row.get("Business Name", ""),
                "city": row.get("City", ""),
                "business_type": row.get("Business Type", ""),
                "niche": row.get("Niche") or row.get("Business Type", ""),
                "rating": float(row.get("Rating") or 0),
                "review_count": int(float(row.get("Reviews") or 0)),
                "website": row.get("Website", ""),
            }
    return out
```

### 4.4 Optional: meme image support for step 6

The Resend send already accepts `html_body`. To attach a meme inline:

1. Add a `meme_url` column to `niche_offers` (nullable). On the `/offers`
   drawer, add a "Step 6 meme image URL (optional)" field next to Loom URL.
2. In `_send_step`, if `step == 6` and `meme_url` is set, append to `html_body`:
   ```html
   <p><img src="{meme_url}" alt="" style="max-width:480px;border-radius:8px;"></p>
   ```
3. Plain-text body stays clean (no image markup).

Keep this behind the offer record so the operator opts in per-niche. Memes
that don't fit the niche kill more deals than they save.

---

## 5. Copy Standards (Update `home-services-offer.md` and the system prompt)

Add a new Section 9 to `home-services-offer.md`:

> ### 9. Sequence Step Copy Principles
>
> The 7-step drip is governed by two laws on top of everything in sections 1–8:
>
> **Law 1: Every step solves "why now."** Step 1 sells the problem. Step 3
> sells loss aversion. Step 5 sells arithmetic. Step 7 sells closure. The
> reader must finish each email knowing why they should reply *today*, not
> next quarter.
>
> **Law 2: The offer compounds, not the pressure.** By step 5 the
> risk-reversal should feel inevitable, not desperate. Frame: "the only way
> you lose money is if it works and you stop us." That sentence ships in
> step 5 verbatim. Do not weaken it.
>
> Step 6 is the only step that breaks tone. It is allowed to be self-aware
> and slightly funny. The pizza breakup in step 7 is the second tone break
> and the only one with a fixed mechanic (reply PIZZA / CALL / LATER).
> Both are deliberate pattern-interrupts after five formal touches.

This file is loaded as a cached system block on every Claude draft call, so
the new section flows into all step generations automatically.

---

## 6. UI Changes (Minimal)

### `outreach.html`
- Above the "Send all" button, add a small line:
  > Sending also enrols each lead in a 7-step sequence (steps 2–7 sent automatically over the next 28 days).
- After send, the response toast already shows "Sent N, failed M". Append:
  > N enrolled in sequence. View at /sequences.

### `sequences.html`
- Update the page subtitle from "4-step nurture" to "7-step nurture".
- Update the step progress dots from `●●○○` to `●●●○○○○` (7 dots).
- The "Enqueue → sequence" button stays — useful for back-filling old CSVs
  whose first email was sent before this PR shipped.

### `dashboard.py` (the today's-queue widget)
- Update `step_labels` dict:
  ```python
  step_labels = {1: "Cold", 2: "Bump", 3: "FOMO",
                 4: "Loom", 5: "Math", 6: "Quirky", 7: "Breakup"}
  ```

---

## 7. Migration & Back-fill

After deploy:

1. **Existing 5-step sequences in flight:** leave alone. They finish on the
   old cadence. The scheduler tick already respects each sequence's own
   `current_step` and `next_send_at`, so nothing breaks.
2. **CSVs already partially sent (one-shot, no sequence):** run the existing
   `/api/sequences/start-from-csv` endpoint manually for each. It will skip
   leads that already replied and enrol the rest with `start_at` = now, so
   they get a fresh 7-step run starting at step 1. (Acceptable double-tap;
   the value is worth the small awkwardness.)

---

## 8. Acceptance Criteria

A reviewer should be able to verify all of these in one local session:

- [ ] Click "Send all" on Outreach for a 3-lead CSV. All 3 leads now appear
      on `/sequences` as `active`, `current_step=1`, with rows for steps
      1–7 in `sequence_messages`. Step 1 is `status=sent` with a Resend ID.
- [ ] `outreach_log` has 3 fresh rows, status `sent`, Resend IDs match the
      step-1 message rows.
- [ ] Force-tick the scheduler with the next-send-at moved 3 days back via
      a SQL update on one row. Step 2 fires. Body is distinct from step 1.
- [ ] Reply to one of the test sends from Gmail. Within 15 minutes the
      Gmail reply scanner pauses that sequence with `paused_reason=replied`.
      Steps 2–7 do not fire for that lead.
- [ ] Send a duplicate "Send all" for the same CSV. The API response notes
      "already in active sequence" for each lead. No new sequences created,
      no duplicate sends.
- [ ] Open step 5 in the DB. The body contains the phrase "the only way
      you lose money is if it works and you stop us." (verbatim check).
- [ ] Open step 7 in the DB. The body contains all three of: PIZZA, CALL,
      LATER (uppercase, verbatim).
- [ ] Word-count check across 50 generated emails: no step 1/3/5 over 140
      words, no step 7 over 70 words. No em dashes anywhere. No banned
      buzzwords (synergy, leverage, etc.).

---

## 9. Out of Scope for This PR

- LinkedIn follow-ups (different channel, different rules per CLAUDE.md §8).
- A/B testing of subject lines (do once we have ≥500 sends through the new
  pipeline).
- Per-niche cadence overrides (some niches probably want shorter gaps —
  ship one cadence first, measure, then split).
- Send-time optimization (Tuesday 10am vs whenever). Day offset is enough
  for v1.

---

## 10. One-line summary for the PR title

> Auto-enrol every cold send in a 7-step drip; rebuild step copy with grand-slam offer recap, quirky pattern interrupt, and pizza breakup.
