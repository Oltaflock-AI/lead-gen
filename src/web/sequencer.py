"""7-step hyper-personalized email sequence engine.

Cadence (offsets from step 1 send time, span ~28 days):
  step 1  ·  day 0   — cold open with risk-reversal offer
  step 2  ·  day 3   — bump + single outcome line
  step 3  ·  day 7   — competitor / FOMO angle
  step 4  ·  day 11  — Loom value drop
  step 5  ·  day 16  — grand-slam offer recap (the math email)
  step 6  ·  day 21  — quirky pattern interrupt
  step 7  ·  day 28  — pizza breakup (PIZZA / CALL / LATER)

Drafting is done up-front by Claude using the lead facts + per-niche offer
record (offer copy + tone notes + Loom URL). Sending is via Resend. Reply
detection (Gmail) flips status='paused' with reason='replied'.

This module owns:
  • `enqueue_lead(...)`                      — bulk start from a CSV
  • `enqueue_lead_with_first_send(...)`      — auto-enrol from /api/outreach/send
  • `start_scheduler(...)`                   — daemon thread polling for due steps
  • `process_replies(...)`                   — Gmail reply detector → pause sequences
  • `/webhook/resend`                        — handled by app.py, calls record_event()
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from . import niche_briefs
from . import resend_send
from . import send_timing

log = logging.getLogger(__name__)

# Step day offsets from step 1 send.
STEP_OFFSETS_DAYS = {1: 0, 2: 3, 3: 7, 4: 11, 5: 16, 6: 21, 7: 28}
NUM_STEPS = 7

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


# ─────────────────────────── drafting ───────────────────────────

_SYSTEM = """You write hyper-personalized cold-outreach emails as part of a 7-step drip sequence. Each email feels hand-written for ONE specific business, not a template.

We sell AI services (24/7 voice-answering agent, chatbot that lives ON the prospect's existing site, missed-call recovery). We do NOT build, mock up, host, or design websites for prospects.

HARD BAN — never write in subject or body:
- "I built / mocked / designed / made you a website / site / homepage / landing page"
- "preview link", "demo link to the site", "live in X days, $0 to start"
- "noticed you don't have a website", "your business has no website"
- "$29 / small monthly fee if you keep the site" or any pricing framed around a built site
- Any sentence that implies we created or own a site for them
Always assume they already have a site. Pitch the AI layer on top, framed by niche-specific ROI (recovered after-hours calls × average job value).

Hard rules:
- Plain text only. No markdown, no HTML, no emojis, no asterisks, no bullets, no numbered lists.
- Word counts: steps 1, 3, 5 are 90 to 140 words. Step 4 (Loom) is 70 to 110 words. Step 6 (quirky) is 60 to 100 words. Steps 2 (bump) and 7 (pizza breakup) are 35 to 70 words.
- Open with one specific observation about THIS business (use rating, review count, city, niche — never invent facts).
- Make the offer feel inevitable: clear outcome, low friction, reversible. Lead with the risk-reversal: "you don't pay until we book a job" + "if we don't book one in 30 days I send you $100 back". Steal the structure, don't quote it.
- Social-proof framing IS allowed and encouraged: "another {business_type} two suburbs over", "the tradies we work with in {city}", "a {business_type} with a similar review count". DO NOT invent specific company names, dollar figures, or precise client counts. Use only the localized stat from the niche playbook for any hard numbers.
- One soft CTA per email. Never aggressive. Never pushy.
- Never use: synergy, leverage, revolutionary, game-changing, circle back, touch base, just checking in.
- DO NOT include any signoff, sender name, "Best,", "Cheers,", or company name. The pipeline appends the structured signature from the user's settings. Anything you add creates a duplicate.
- Body ends after the CTA. Nothing else.
- Never use em dashes. Use commas, periods, or parentheses instead.

Subject-line rules (CRITICAL — these decide opens):
- Under 45 chars. Lowercase preferred (feels human, not corporate).
- Quirky, curious, pattern-interrupting. Read it aloud — if it sounds like every other cold email, rewrite.
- Anchor in ONE concrete fact from THIS lead (rating, review_count, suburb, niche-specific noun). Generic = trash.
- Pick one angle per step; rotate angles across the 7 steps so the inbox view feels different each time:
    a) Number-as-question — "335 reviews and still missing calls?", "{rating}★ but Tuesday gaps?"
    b) Surprising stat from playbook Section 4 — never invent numbers.
    c) Curiosity gap — "the after-hours problem at {business_name}"
    d) Micro-ask — "10 mins, then I disappear", "one screenshot, no pitch"
    e) Competitor scenario — "what the {business_type} 2 suburbs over stopped doing"
    f) Industry insider one-liner — "still on the roof?", "another voicemail Saturday?"
    g) Number-as-cost — "$8k of leaks past 9pm"
    h) Confessional — "we tried this on three plumbers in {city}"
    i) But-flip — "73 listings, no chatbot, why?"
    j) Tiny imperative — "open this if Mondays bury you"
- BANNED subjects (no paraphrases either): "Quick idea for X", "Re: X", "Following up", "Just checking in", "Touching base", "Idea for {business_name}", "Question about {business_name}", "{business_name} + AI", anything starting with "Hi" or "Hey".
- BANNED words anywhere in subject: free, guaranteed, win, exclusive, opportunity, ROI, scale, growth, partnership.
- No exclamation marks. No ALL CAPS. No emoji. No spammy punctuation. Don't end with a period.
- If the playbook has example subjects (Section 6), treat as inspiration only — DO NOT copy verbatim.

Output JSON: {"subject": "...", "body": "..."}. Nothing else.
"""

_STEP_INSTRUCTIONS = {
    1: (
        "Step 1 — first cold email (day 0, 90-140 words). Lead with a "
        "SPECIFIC observation pulled from the lead facts (rating + review "
        "count + city + niche). One short paragraph naming the missed-call "
        "pain. Deliver the offer with the risk-reversal verbatim ('you "
        "don't pay until we book a job, $100 back if we don't book one in "
        "30 days'). Soft CTA: short reply or 10-minute call."
    ),
    2: (
        "Step 2 — bump (day 3, 35-70 words). Acknowledge no reply in ONE "
        "casual line. Drop a single concrete outcome framed generically: "
        "e.g. 'a {business_type} with a similar review count went from "
        "missing 1 in 4 calls to under 1 in 30 in week one'. End with a "
        "one-line question. No re-pitch of the offer."
    ),
    3: (
        "Step 3 — competitor / FOMO angle (day 7, 90-140 words). Open with "
        "the observation that another {business_type} in the same region "
        "(do not name them) is already running an AI agent and capturing "
        "the after-hours jobs that used to slip past phones like "
        "{business_name}'s. Use the localized stat from the playbook to "
        "ground the cost. Tie back to the risk-reversal in one sentence. "
        "CTA: 'want me to send you the 90-second walkthrough?'"
    ),
    4: (
        "Step 4 — Loom value drop (day 11, 70-110 words). Paste the Loom "
        "URL on its own line. Tease ONE concrete thing the video shows for "
        "THIS niche (e.g. how the agent triages a burst pipe vs. a slow "
        "drip for a plumber). CTA: reply 'yes' to talk after watching."
    ),
    5: (
        "Step 5 — grand-slam offer recap (day 16, 90-140 words). Frame the "
        "math so plainly that NOT trying it looks like the riskier choice. "
        "Structure: (1) one line naming what they're losing per week in "
        "raw money terms using the localized job-value range. (2) the "
        "offer restated in three short lines, $0 setup, $0 monthly, "
        "$100-back guarantee. (3) one line: 'the only way you lose money "
        "is if it works and you stop us.' CTA: ten minutes this week, "
        "their pick of day. NO buzzwords. NO hype words. Just arithmetic."
    ),
    6: (
        "Step 6 — quirky pattern interrupt (day 21, 60-100 words). Drop "
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
        "Step 7 — pizza breakup (day 28, 35-70 words). Last note. Polite, "
        "short, leaves the door open. Include this exact mechanic verbatim: "
        "'Reply with one word and I'll act on it: PIZZA means stop, I won't "
        "email again. CALL means book a 10-minute slot. LATER means I "
        "circle back in 90 days.' One stat-free sentence above it framing "
        "why it still matters. Nothing else."
    ),
}


def _facts_block(lead):
    has_site = bool(lead.get("has_website")) or bool(lead.get("website"))
    site_line = (
        lead.get("website")
        or ("confirmed (assume they have a live site)" if has_site else "unknown — STILL ASSUME they have a site; never pitch building one")
    )
    return (
        f"business_name: {lead.get('business_name', '')}\n"
        f"city: {lead.get('city', '')}\n"
        f"niche: {lead.get('niche', '')}\n"
        f"google_rating: {lead.get('rating', 0)}\n"
        f"review_count: {lead.get('review_count', 0)}\n"
        f"website: {site_line}\n"
    )


def _fallback_draft(step, lead, offer, loom_url, sender_name):
    name = lead.get("business_name", "your business")
    city = lead.get("city", "")
    biz = lead.get("niche") or lead.get("business_type") or "tradies"
    where = f" in {city}" if city else ""
    if step == 1:
        body = (
            f"Hi, saw {name}{where} and wanted to reach out.\n\n"
            f"{offer}\n\n"
            "Open to a 10-minute call this week?"
        )
        subj = f"the after-hours problem at {name.lower()}"
    elif step == 2:
        body = (
            f"Bumping this once. A {biz} with a similar review count to "
            f"{name} went from missing 1 in 4 calls to under 1 in 30 in "
            "week one. Worth a quick look?"
        )
        subj = "1 in 4 -> 1 in 30 in a week"
    elif step == 3:
        body = (
            f"Another {biz} {('near ' + city) if city else 'in your region'} "
            "is already running an AI agent and now picks up the after-hours "
            f"jobs that used to ring through to phones like {name}'s. "
            "You don't pay until we book a job, and if we don't book one in "
            "30 days I send you $100 back. Want me to send the 90-second "
            "walkthrough?"
        )
        subj = "what a competitor a few suburbs over just did"
    elif step == 4:
        body = (
            f"Recorded a 90-second walkthrough showing how the agent handles "
            f"a real {biz} call vs. a quote enquiry:\n\n"
            f"{loom_url or '<loom_url not set for this niche>'}\n\n"
            "Reply 'yes' if you want to chat after watching."
        )
        subj = "90 seconds, then you decide"
    elif step == 5:
        body = (
            f"Quick math for {name}. A handful of missed after-hours calls "
            "a week, at typical job value, is the part of the P&L nobody "
            "writes down.\n\n"
            "$0 setup.\n"
            "$0 monthly.\n"
            "$100 back if we don't book a job in 30 days.\n\n"
            "The only way you lose money is if it works and you stop us. "
            "Ten minutes this week, your pick of day?"
        )
        subj = "the math on the missed calls"
    elif step == 6:
        body = (
            f"Either my emails are landing in spam or {name} doesn't "
            f"actually want more booked jobs, and I genuinely can't tell "
            "which. The agent picks up the after-hours calls so the "
            "voicemail box stops being your sales funnel.\n\n"
            "Reply yes if you want a 5-min walkthrough, reply no and I "
            "close the loop."
        )
        subj = "spam folder or not interested?"
    else:
        body = (
            "Worth remembering: most callers who hit voicemail dial the "
            "next number on Google within 90 seconds.\n\n"
            "Reply with one word and I'll act on it: PIZZA means stop, I "
            "won't email again. CALL means book a 10-minute slot. LATER "
            "means I circle back in 90 days."
        )
        subj = "one word: pizza"
    return {"subject": subj, "body": body}


def _engagement_block(prior_msgs):
    """Compact summary of opens/clicks across previously-sent steps.
    Returned as a string the LLM can use to calibrate this step's angle."""
    if not prior_msgs:
        return ""
    sent = [m for m in prior_msgs if m.get("sent_at")]
    if not sent:
        return ""
    total_opens = sum((m.get("opens") or 0) for m in sent)
    total_clicks = sum((m.get("clicks") or 0) for m in sent)
    open_steps = [m["step"] for m in sent if (m.get("opens") or 0) > 0]
    click_steps = [m["step"] for m in sent if (m.get("clicks") or 0) > 0]
    if not (total_opens or total_clicks):
        return (f"Already sent {len(sent)} email(s) "
                f"(steps {[m['step'] for m in sent]}). NO opens, NO clicks. "
                "They are ignoring you. Change the angle this email — try a "
                "stronger pattern interrupt, a different hook, or a more "
                "personal observation. DO NOT rehash the offer the same way.")
    bits = [
        f"Already sent {len(sent)} email(s) (steps {[m['step'] for m in sent]}).",
        f"opens: {total_opens} across step(s) {open_steps or 'none'}",
        f"clicks: {total_clicks} across step(s) {click_steps or 'none'}",
    ]
    if total_clicks:
        bits.append("They've engaged with a link. They're warm. This email "
                    "should acknowledge their interest subtly and propose a "
                    "concrete, low-friction next step (a 10-min slot, a "
                    "specific time). Do NOT re-pitch from scratch — they've "
                    "already heard the offer.")
    elif total_opens:
        bits.append("They open but don't reply. They're curious but unconvinced. "
                    "This email should narrow the question, drop a specific "
                    "data point or outcome, and ask a yes/no question that "
                    "is easier to answer than the prior CTAs.")
    return " ".join(bits)


def _no_website_pivot(lead):
    return ""


def _draft_one(step, lead, offer_record, sender_name, prior_msgs=None):
    """Returns {subject, body}. Falls back to template on any LLM error.
    `prior_msgs` is a list of already-sent steps' rows (with opens/clicks)
    used to bake engagement context into the LLM prompt."""
    offer_text = (offer_record or {}).get("offer", "") or ""
    tone = (offer_record or {}).get("tone", "") or ""
    loom_url = (offer_record or {}).get("loom_url", "") or ""

    if not ANTHROPIC_API_KEY:
        return _fallback_draft(step, lead, offer_text, loom_url, sender_name)

    try:
        from anthropic import Anthropic
    except ImportError:
        return _fallback_draft(step, lead, offer_text, loom_url, sender_name)

    engagement = _engagement_block(prior_msgs or [])
    pivot = _no_website_pivot(lead)

    user_msg = (
        f"{_STEP_INSTRUCTIONS[step]}\n\n"
        f"sender_name: {sender_name}\n"
        f"--- lead facts ---\n{_facts_block(lead)}\n"
        f"--- offer (rewrite naturally; never quote verbatim) ---\n{offer_text}\n\n"
        f"--- tone notes ---\n{tone}\n\n"
        f"--- loom_url (only used in step 3) ---\n{loom_url}\n\n"
        + (f"--- prior engagement on this prospect ---\n{engagement}\n\n"
           if engagement else "")
        + pivot
        + "Output JSON only."
    )

    brief = niche_briefs.get_brief_for_lead(lead, niche=lead.get("niche", ""))
    sys_blocks = [{"type": "text", "text": _SYSTEM,
                   "cache_control": {"type": "ephemeral"}}]
    if brief:
        sys_blocks.extend(niche_briefs.system_blocks(brief))
        user_msg += niche_briefs.user_directive(brief, lead)

    # Self-improvement: feed proven subject lines (open rate >= 20%, 2+ sends)
    # into the prompt so steps 2-7 also converge on what's getting opened.
    try:
        winners = db.top_performing_subjects(min_sends=2, min_open_rate=20, limit=6)
    except Exception:
        winners = []
    if winners:
        ex = "\n".join(f"- {w['subject']}  ({w['open_rate']}% open)"
                       for w in winners if w.get("subject"))
        user_msg += (
            "\n\nPROVEN WINNERS — past subjects from our own sends with "
            "good open rates. Match the style + voice; do NOT copy:\n" + ex
        )

    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=sys_blocks,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("no JSON in LLM output")
        parsed = json.loads(m.group(0))
        return {
            "subject": (parsed.get("subject") or f"For {lead.get('business_name','')}")[:120],
            "body": parsed.get("body", "").strip(),
        }
    except Exception as e:
        log.warning("draft step=%s failed for %s: %s — using fallback",
                    step, lead.get("business_name"), e)
        fb = _fallback_draft(step, lead, offer_text, loom_url, sender_name)
        fb["error"] = str(e)
        return fb


def draft_all_steps(lead, niche, sender_name=""):
    """Pull niche offer record and draft all 7 steps. Returns list[{subject, body}]."""
    offer_record = db.get_niche_offer(niche)
    out = []
    for step in range(1, NUM_STEPS + 1):
        out.append(_draft_one(step, lead, offer_record, sender_name))
    return out


def _has_offer_or_brief(lead, niche):
    """A lead is sendable if EITHER a DB niche_offer row exists OR an on-disk
    brief matches its niche/business_type. Disk briefs include offer copy +
    tone + templates so they're a complete substitute for the DB record."""
    if niche and db.get_niche_offer(niche):
        return True
    try:
        return bool(niche_briefs.get_brief_for_lead(lead, niche=niche))
    except Exception:
        return False


def _lead_snapshot(lead):
    """Slim dict persisted on the sequence row so each step can be redrafted
    later with the same lead facts the original draft used."""
    return {
        "email": lead.get("email", ""),
        "business_name": lead.get("business_name", ""),
        "city": lead.get("city", ""),
        "niche": lead.get("niche", ""),
        "rating": lead.get("rating", 0),
        "review_count": lead.get("review_count", 0),
        "website": lead.get("website", ""),
        "business_type": lead.get("business_type", ""),
    }


def _refresh_step_with_engagement(seq, step):
    """Just before sending step N (where N > 1), draft or redraft the step.

    Two paths:
    1. Pending body is empty (lazy-enrolled placeholder) — draft it from
       scratch with whatever prior signals exist. This is the lazy-draft
       path used by enqueue_lead_with_first_send to avoid burning ~6 LLM
       calls per lead at Send-All time.
    2. Pending body already drafted — redraft with open/click signals from
       prior sent steps. No-op if no prior steps yet.
    """
    if step <= 1:
        return None
    facts_json = seq.get("lead_facts_json") or ""
    if not facts_json:
        return None
    try:
        lead = json.loads(facts_json)
    except Exception:
        return None
    pending = db.get_pending_message(seq["id"], step)
    needs_draft = bool(pending and not (pending.get("body") or "").strip())
    prior = db.list_sent_sequence_messages(seq["id"], before_step=step)
    if not needs_draft and not prior:
        return None  # already drafted and no engagement signals to incorporate
    offer_record = db.get_niche_offer(lead.get("niche", "") or seq.get("niche", ""))
    sender_name = db.get_setting("sender_name", "") or ""
    redraft = _draft_one(step, lead, offer_record, sender_name,
                         prior_msgs=prior or None)
    if pending and redraft.get("body"):
        db.update_sequence_message(pending["id"], redraft["subject"], redraft["body"])
        if needs_draft:
            log.info("seq %s step %s lazy-drafted (prior_steps=%d)",
                     seq["id"], step, len(prior))
        else:
            log.info("seq %s step %s redrafted with engagement context "
                     "(opens=%s clicks=%s on prior steps)",
                     seq["id"], step,
                     sum((m.get("opens") or 0) for m in prior),
                     sum((m.get("clicks") or 0) for m in prior))
    return redraft


# ─────────────────────────── enqueue ───────────────────────────


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _add_days_iso(days, base=None):
    base = base or datetime.now(timezone.utc)
    return (base + timedelta(days=days)).isoformat()


def _schedule_for_step(step, lead, base):
    """Compute the scheduled-for ISO timestamp for a sequence step.

    Step 1 fires immediately (the operator just clicked Send).
    Steps 2-7 are snapped to the next preferred-hour weekday in the
    PROSPECT'S local timezone via send_timing.next_send_at(), so a
    Sydney plumber gets step 2 at Tue 10am Sydney regardless of when
    the operator (in IST) clicked Send.
    """
    day_offset = STEP_OFFSETS_DAYS[step]
    earliest = (base or datetime.now(timezone.utc)) + timedelta(days=day_offset)
    if step == 1:
        return earliest.isoformat()
    country = ""
    try:
        country = niche_briefs.infer_country(lead) or ""
    except Exception:
        pass
    return send_timing.next_send_at(country, earliest).isoformat()


def enqueue_lead(lead, csv_name="", sender_name="", start_at=None):
    """Create sequence + draft all 7 steps + schedule step 1.

    `lead` must contain at least: email, business_name, niche.
    Returns (sequence_id, status_message).
    """
    email = (lead.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None, "invalid email"

    niche = lead.get("niche", "")
    if not _has_offer_or_brief(lead, niche):
        return None, (f"no offer for niche '{niche}' — set one in /offers "
                      "or drop a matching .md brief in the project root")

    sid = db.create_sequence(
        lead_email=email,
        business_name=lead.get("business_name", ""),
        niche=niche,
        csv_name=csv_name,
        city=lead.get("city", ""),
        lead_facts=_lead_snapshot(lead),
    )
    seq = db.get_sequence(sid)
    if seq["current_step"] > 0 or seq["status"] != "active":
        return sid, "already exists"

    drafts = draft_all_steps(lead, niche, sender_name=sender_name)
    base = start_at or datetime.now(timezone.utc)
    for step in range(1, NUM_STEPS + 1):
        d = drafts[step - 1]
        scheduled = _schedule_for_step(step, lead, base)
        db.upsert_sequence_message(sid, step, d["subject"], d["body"], scheduled)

    # Schedule step 1 immediately.
    db.update_sequence(sid, next_send_at=base.isoformat())
    log.info("enqueued sequence %s for %s (niche=%s)", sid, email, niche)
    return sid, "queued"


def enqueue_lead_with_first_send(lead, csv_name="", sender_name="",
                                 override_subject=None, override_body=None):
    """Auto-enrol path used by /api/outreach/send.

    Creates the sequence row, drafts all 7 steps up-front, sends step 1
    SYNCHRONOUSLY via Resend, schedules steps 2-7. If override_subject and
    override_body are provided (operator edited the draft on the Outreach
    page), step 1 uses those instead of the auto-drafted copy. Steps 2-7
    are still LLM-drafted from the lead facts.

    Returns dict:
      {
        "sequence_id": int | None,
        "resend_id":  str | None,
        "status":     "queued" | "already_active" | "error",
        "error":      str | None,
      }
    """
    email = (lead.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return {"status": "error", "error": "invalid email",
                "sequence_id": None, "resend_id": None}

    # Idempotency: if an active sequence already exists, do not re-enrol.
    existing = db.get_active_sequence_by_email(email)
    if existing:
        return {"status": "already_active",
                "sequence_id": existing["id"], "resend_id": None,
                "error": None}

    niche = lead.get("niche", "")
    if not _has_offer_or_brief(lead, niche):
        return {"status": "error",
                "error": (f"no offer for niche '{niche}' — set one in /offers "
                          "or drop a matching .md brief in the project root"),
                "sequence_id": None, "resend_id": None}

    sid = db.create_sequence(
        lead_email=email, business_name=lead.get("business_name", ""),
        niche=niche, csv_name=csv_name, city=lead.get("city", ""),
        lead_facts=_lead_snapshot(lead),
    )
    # If create_sequence returned a stale (done/paused/cancelled) row for
    # this email, reset it to a fresh active enrollment.
    db.update_sequence(sid, status="active", current_step=0,
                       paused_reason=None, next_send_at=None)

    # Lazy-draft path: when the operator already has step 1 drafted (the UI
    # always passes override_subject/body), skip the ~6 upfront LLM calls
    # for steps 2-7. Steps 2-7 are stored as empty placeholders and drafted
    # just-in-time inside tick() via _refresh_step_with_engagement.
    if override_subject and override_body:
        drafts = [{"subject": override_subject, "body": override_body}]
        for _ in range(2, NUM_STEPS + 1):
            drafts.append({"subject": "[draft pending]", "body": ""})
    else:
        drafts = draft_all_steps(lead, niche, sender_name=sender_name)

    base = datetime.now(timezone.utc)
    for step in range(1, NUM_STEPS + 1):
        d = drafts[step - 1]
        scheduled = _schedule_for_step(step, lead, base)
        db.upsert_sequence_message(sid, step, d["subject"], d["body"], scheduled)

    # Send step 1 synchronously so the operator sees instant feedback.
    seq = db.get_sequence(sid)
    ok, err = _send_step(seq, 1)
    if not ok:
        return {"status": "error", "error": err,
                "sequence_id": sid, "resend_id": None}

    msg1 = db.get_sequence_message(sid, 1) or {}
    advance_after_send(seq, 1)
    log.info("auto-enrolled sequence %s for %s (niche=%s, step 1 sent)",
             sid, email, niche)
    return {"status": "queued", "sequence_id": sid,
            "resend_id": msg1.get("resend_id"), "error": None}


# ─────────────────────────── send ───────────────────────────


def _send_step(seq, step):
    """Send one step. Returns (ok, error)."""
    msg = db.get_pending_message(seq["id"], step)
    if not msg:
        return False, f"no pending message for step {step}"
    if not (msg.get("subject") and (msg.get("body") or "").strip()):
        # Lazy-draft missing or LLM redraft failed — leave pending so the
        # next tick retries instead of shipping an empty email.
        return False, f"step {step} draft incomplete — will retry next tick"
    if not resend_send.is_configured():
        return False, "resend not configured (RESEND_API_KEY/RESEND_FROM)"

    # Wrap the body in Georgia 12pt + append the structured signature from
    # settings (name, title, company, website, booking link).
    from .email_compose import compose as compose_email
    text_body, html_body = compose_email(msg["body"], db.get_settings())

    try:
        rid = resend_send.send_email(
            to_addr=seq["lead_email"],
            subject=msg["subject"],
            text_body=text_body,
            html_body=html_body,
        )
    except Exception as e:
        log.exception("send failed seq=%s step=%s", seq["id"], step)
        db.mark_sequence_message_failed(msg["id"], e)
        return False, str(e)

    db.mark_sequence_message_sent(msg["id"], rid)
    log.info("sent seq=%s step=%s resend_id=%s to=%s",
             seq["id"], step, rid, seq["lead_email"])
    return True, None


def _text_to_html(text):
    """Plain text → minimal HTML preserving line breaks."""
    import html as _h
    safe = _h.escape(text or "")
    return "<div style=\"font-family: -apple-system, sans-serif; font-size:14px; line-height:1.5;\">" \
           + safe.replace("\n", "<br>") + "</div>"


def advance_after_send(seq, step):
    """Update sequence pointer after a successful send. Schedules next step,
    or marks done after the final step."""
    if step >= NUM_STEPS:
        db.update_sequence(seq["id"], status="done", current_step=step,
                           next_send_at=None)
        return
    next_step = step + 1
    delay_days = STEP_OFFSETS_DAYS[next_step] - STEP_OFFSETS_DAYS[step]
    earliest = datetime.now(timezone.utc) + timedelta(days=delay_days)
    # Snap to business hours in the prospect's tz. Pull country from the
    # stored lead snapshot when present; fall back to UTC.
    country = ""
    facts_json = seq.get("lead_facts_json") or ""
    if facts_json:
        try:
            country = niche_briefs.infer_country(json.loads(facts_json)) or ""
        except Exception:
            pass
    next_iso = send_timing.next_send_at(country, earliest).isoformat()
    db.update_sequence(seq["id"], current_step=step, next_send_at=next_iso)


def tick():
    """Process all sequences whose next_send_at has elapsed. Daily/monthly
    quota is reported for telemetry but does not block sends — the worker
    keeps sending until Resend itself rate-limits a request.

    Returns dict with counts.
    """
    now = _now_iso()
    due = db.due_sequences(now, limit=50)
    sent, failed, skipped_quota, skipped_no_opens = 0, 0, 0, 0
    # Steps 1..(GATE-1) reach everyone delivered. From GATE onward we only
    # email leads who opened at least one prior step — keeps the long tail
    # focused on engaged prospects. Tunable via env.
    open_gate_from_step = int(os.getenv("LEADGEN_OPEN_GATE_FROM_STEP", "4"))

    # Quota is advisory — read for telemetry but don't block. We send
    # everything that's due and let Resend's own rate limiter push back
    # via per-send errors (which auto-pause the sequence below).
    daily_cap = int(os.getenv("LEADGEN_DAILY_CAP", "100"))
    monthly_cap = int(os.getenv("LEADGEN_MONTHLY_CAP", "3000"))
    quota = db.send_quota_status(daily_cap=daily_cap, monthly_cap=monthly_cap)

    for seq in due:
        next_step = seq["current_step"] + 1
        if next_step > NUM_STEPS:
            db.update_sequence(seq["id"], status="done", next_send_at=None)
            continue
        # Open-gate: from `open_gate_from_step` onward, only continue the
        # sequence for leads that have opened at least one prior email.
        # Non-engaged leads are marked done (no further sends).
        if next_step >= open_gate_from_step:
            prior = db.list_sent_sequence_messages(seq["id"], before_step=next_step)
            # FIX 4: use confirmed_open (passes 30s bot filter) instead of
            # raw opens count, which includes scanner/bot pixel hits.
            confirmed_opens = sum(1 for m in prior if m.get("confirmed_open"))
            if confirmed_opens == 0:
                db.update_sequence(
                    seq["id"], status="done", next_send_at=None,
                    paused_reason=f"stopped before step {next_step} — no opens through step {next_step - 1}",
                )
                skipped_no_opens += 1
                log.info("seq %s gated at step %s — no opens on steps 1..%s",
                         seq["id"], next_step, next_step - 1)
                continue
        # Redraft step >1 with engagement context (opens/clicks from prior
        # steps) so the email reflects what the prospect has actually done.
        try:
            _refresh_step_with_engagement(seq, next_step)
        except Exception:
            log.exception("redraft failed seq=%s step=%s — sending pre-drafted version",
                          seq["id"], next_step)
        ok, err = _send_step(seq, next_step)
        if ok:
            advance_after_send(seq, next_step)
            sent += 1
        else:
            failed += 1
            if err and ("not configured" in err or "INVALID" in err.upper()):
                db.update_sequence(seq["id"], status="paused",
                                   paused_reason=f"send error: {err[:120]}",
                                   next_send_at=None)
    return {"due": len(due), "sent": sent, "failed": failed,
            "skipped_quota": skipped_quota,
            "skipped_no_opens": skipped_no_opens, "quota": quota}


# ─────────────────────────── scheduler thread ───────────────────────────

_scheduler_thread = None
_scheduler_stop = threading.Event()
SCHEDULER_INTERVAL_SEC = int(os.getenv("LEADGEN_TICK_SEC", "60"))
# Gmail reply scan cadence — runs in the same daemon thread, every Nth tick.
REPLY_SCAN_INTERVAL_SEC = int(os.getenv("LEADGEN_REPLY_SCAN_SEC", "900"))  # 15 min
_LAST_REPLY_SCAN = 0.0


def _scan_replies_quietly():
    """Periodic background reply scan: pauses replied sequences AND flips
    outreach_log rows to status='replied'. Soft-fails when Gmail not
    connected — the thread continues."""
    global _LAST_REPLY_SCAN
    now = time.time()
    if now - _LAST_REPLY_SCAN < REPLY_SCAN_INTERVAL_SEC:
        return
    _LAST_REPLY_SCAN = now
    try:
        creds = db.load_token()
    except Exception:
        return
    if not creds:
        return  # Google not connected yet; nothing to scan
    try:
        from .gmail import check_replies as gmail_check_replies
        out = gmail_check_replies(creds, days=14)
        seq = process_replies(creds, days=14)
        log.info("periodic reply scan: outreach=%s sequences=%s", out, seq)
    except Exception:
        log.exception("periodic reply scan crashed")


def start_scheduler():
    """Idempotent. Spawns a daemon thread that calls tick() forever and
    periodically scans Gmail for replies."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return _scheduler_thread

    def loop():
        log.info("sequencer scheduler started (tick=%ds, reply scan=%ds)",
                 SCHEDULER_INTERVAL_SEC, REPLY_SCAN_INTERVAL_SEC)
        while not _scheduler_stop.is_set():
            try:
                stats = tick()
                if stats["due"]:
                    log.info("tick: %s", stats)
            except Exception:
                log.exception("scheduler tick crashed")
            try:
                _scan_replies_quietly()
            except Exception:
                log.exception("reply scan dispatch crashed")
            try:
                from . import supabase_sync
                if supabase_sync.is_configured():
                    s = supabase_sync.sync_once()
                    if s.get("pulled"):
                        log.info("supabase sync: %s", s)
            except Exception:
                log.exception("supabase sync crashed")
            _scheduler_stop.wait(SCHEDULER_INTERVAL_SEC)

    _scheduler_thread = threading.Thread(target=loop, daemon=True,
                                         name="sequencer-scheduler")
    _scheduler_thread.start()
    return _scheduler_thread


# ─────────────────────────── webhook ingest ───────────────────────────

# Resend event names we care about, mapped to message metric column.
_RESEND_EVENT_TO_METRIC = {
    "email.opened":     "opens",
    "email.clicked":    "clicks",
    "email.bounced":    "bounced",
    "email.complained": "bounced",
    "email.delivery_delayed": None,
    "email.delivered":  None,
    "email.sent":       None,
}


def record_event(payload):
    """Persist a Resend webhook event and roll it onto BOTH:
      - sequence_messages (multi-step sequencer)
      - outreach_log      (one-shot outreach sends)

    Returns dict {ok, event, resend_id, paused_sequence, outreach_updated}.
    """
    event = payload.get("type") or payload.get("event") or ""
    data = payload.get("data") or {}
    resend_id = data.get("email_id") or data.get("id") or ""
    # Resend events carry their own created_at — use it for replay dedupe.
    created_at = (
        payload.get("created_at")
        or data.get("created_at")
        or payload.get("createdAt")
        or ""
    )

    is_new = db.log_email_event(
        resend_id, event, json.dumps(payload)[:8000], created_at=created_at,
    )

    if not is_new:
        # Replay of an event we already processed. Don't re-increment
        # counters — but still let status promote (idempotent UPDATE).
        log.info("resend event REPLAY skipped counters rid=%s event=%s ts=%s",
                 resend_id, event, created_at)
        return {"ok": True, "event": event, "resend_id": resend_id,
                "replay": True, "outreach_updated": False,
                "paused_sequence": None}

    paused_sid = None
    bot_filtered = False
    metric = _RESEND_EVENT_TO_METRIC.get(event)

    # FIX 1: 30-second open time threshold — filter bot/scanner opens that
    # fire within seconds of send (Gmail image proxy, Barracuda, etc.).
    if event == "email.opened" and resend_id:
        msg_row = db.get_message_by_resend_id(resend_id)
        if msg_row and msg_row.get("sent_at"):
            try:
                sent_dt = datetime.fromisoformat(msg_row["sent_at"])
                event_dt = (datetime.fromisoformat(created_at)
                            if created_at else datetime.now(timezone.utc))
                # Ensure both are tz-aware for comparison.
                if sent_dt.tzinfo is None:
                    sent_dt = sent_dt.replace(tzinfo=timezone.utc)
                if event_dt.tzinfo is None:
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
                delta_s = (event_dt - sent_dt).total_seconds()
                if delta_s < 30:
                    bot_filtered = True
                    log.debug("Filtered bot open for %s: %.1fs after send",
                              resend_id, delta_s)
                    db.mark_event_filtered(resend_id, event, created_at)
                    # Skip metric increment and outreach status update below,
                    # but still let the raw event remain in email_events for audit.
            except (ValueError, TypeError) as exc:
                log.warning("open-filter timestamp parse failed rid=%s: %s",
                            resend_id, exc)

    if metric and not bot_filtered:
        db.increment_message_metric(resend_id, metric)
        # FIX 3: set confirmed_open when a real (non-bot) first open passes
        if event == "email.opened":
            db.set_confirmed_open(resend_id)
    if event in ("email.bounced", "email.complained") and resend_id:
        # Resend's bounce payload carries the WHY we need for diagnosis.
        # `data.bounce` exists on email.bounced; complaints rarely have it.
        bounce_obj = data.get("bounce") or {}
        b_type = (bounce_obj.get("type") or "").strip() or None
        b_subtype = (bounce_obj.get("subType")
                     or bounce_obj.get("subtype") or "").strip() or None
        b_diag = (bounce_obj.get("message")
                  or bounce_obj.get("diagnosticCode")
                  or bounce_obj.get("diagnostic_code") or "").strip() or None
        if event == "email.complained" and not b_type:
            b_type = "Complaint"

        # Pin the diagnosis on the outreach_log row so the per-lead UI
        # can show "why did this one bounce?" without touching the raw payload.
        try:
            db.annotate_bounce(resend_id, b_type, b_subtype, b_diag)
        except Exception:
            log.exception("annotate_bounce failed rid=%s", resend_id)

        # Resolve recipient address from payload so we can suppress it on
        # all FUTURE sends (not just this campaign). Permanent bounces and
        # complaints both go on the list — Resend will block us anyway if
        # we keep mailing them.
        recipient = ""
        to_field = data.get("to") or []
        if isinstance(to_field, list) and to_field:
            recipient = (to_field[0] or "").strip().lower()
        elif isinstance(to_field, str):
            recipient = to_field.strip().lower()
        # Soft (Transient) bounces are NOT auto-suppressed — those are
        # mailbox-full / greylisted / temp-failed and may recover. We only
        # block permanent + complaint events.
        is_permanent = (b_type or "").lower() in ("permanent", "complaint", "")
        if recipient and (event == "email.complained" or is_permanent):
            try:
                db.upsert_suppression(
                    recipient,
                    reason=event.replace("email.", ""),
                    bounce_type=b_type, bounce_subtype=b_subtype,
                    diagnostic=b_diag,
                )
            except Exception:
                log.exception("upsert_suppression failed for %s", recipient)

        msg = db.get_message_by_resend_id(resend_id)
        if msg:
            seq = db.get_sequence(msg["sequence_id"])
            if seq and seq["status"] == "active":
                db.update_sequence(seq["id"], status="paused",
                                   paused_reason=event.replace("email.", ""),
                                   next_send_at=None)
                paused_sid = seq["id"]

    # One-shot outreach analytics: bumps opens/clicks/bounced + status.
    # Skip outreach status promotion for bot-filtered opens.
    if bot_filtered:
        outreach_updated = False
    else:
        outreach_updated = db.update_outreach_event(resend_id, event)

    # Mirror engagement onto the Supabase leads_master profile so the
    # long-term store reflects opens/clicks/replies for cohort analysis.
    # Skip for bot-filtered opens — don't pollute the leads_master profile.
    if bot_filtered:
        log.info("resend event %s rid=%s BOT-FILTERED (skipped counters + outreach)",
                 event, resend_id)
        return {"ok": True, "event": event, "resend_id": resend_id,
                "bot_filtered": True, "paused_sequence": None,
                "outreach_updated": False}
    try:
        from . import supabase_leads
        if supabase_leads.is_configured() and resend_id:
            recipient = ""
            to_field = data.get("to") or []
            if isinstance(to_field, list) and to_field:
                recipient = (to_field[0] or "").strip().lower()
            elif isinstance(to_field, str):
                recipient = to_field.strip().lower()
            short = event.replace("email.", "")
            if recipient and short in {"delivered", "opened", "clicked",
                                       "bounced", "failed", "complained"}:
                supabase_leads.mark_event(
                    recipient,
                    "bounced" if short == "complained" else short,
                )
    except Exception:
        log.exception("supabase_leads event mirror failed")

    log.info("resend event %s rid=%s paused_sid=%s outreach_updated=%s",
             event, resend_id, paused_sid, outreach_updated)
    return {"ok": True, "event": event, "resend_id": resend_id,
            "paused_sequence": paused_sid,
            "outreach_updated": outreach_updated}


# ─────────────────────────── reply detection ───────────────────────────


def process_replies(creds_dict, days=14):
    """Scan Gmail for replies from any address in `sequences`. Pause matches.

    Returns dict {scanned, paused}. Soft-fails when Gmail unavailable.
    """
    try:
        from googleapiclient.discovery import build
        from .sheets import get_credentials_from_dict
    except Exception as e:
        return {"error": f"gmail deps unavailable: {e}", "paused": 0}

    active = db.list_sequences(status="active", limit=2000)
    addrs = sorted({s["lead_email"] for s in active if s["lead_email"]})
    if not addrs:
        return {"scanned": 0, "paused": 0, "addresses": 0}

    try:
        creds = get_credentials_from_dict(creds_dict)
        svc = build("gmail", "v1", credentials=creds)
    except Exception as e:
        log.warning("gmail build failed: %s", e)
        return {"error": str(e), "paused": 0}

    paused = 0
    chunk = 30
    matched_senders = set()
    for i in range(0, len(addrs), chunk):
        batch = addrs[i:i + chunk]
        q = " OR ".join(f"from:{a}" for a in batch) + f" newer_than:{days}d"
        try:
            resp = svc.users().messages().list(userId="me", q=q, maxResults=100).execute()
            for m in resp.get("messages", []):
                meta = svc.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From"]).execute()
                hdrs = {h["name"]: h["value"] for h in
                        meta.get("payload", {}).get("headers", [])}
                frm = hdrs.get("From", "")
                m2 = re.search(r"<([^>]+)>", frm) or re.search(
                    r"[\w.+-]+@[\w-]+\.[\w.-]+", frm)
                if not m2:
                    continue
                addr = (m2.group(1) if m2.lastindex else m2.group(0)).strip().lower()
                matched_senders.add(addr)
        except Exception as e:
            log.warning("gmail list failed: %s", e)
            continue

    for addr in matched_senders:
        n = db.pause_sequence_for_email(addr, "replied")
        paused += n

    log.info("replies scan: addrs=%d matched=%d paused=%d",
             len(addrs), len(matched_senders), paused)
    return {"scanned": len(addrs), "matched": len(matched_senders), "paused": paused}
