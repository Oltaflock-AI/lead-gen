"""Phase 4 — stateless email sequencer for the Vercel cron.

Ports the proven logic from the old Flask sequencer:
  - 7-step drip cadence {1:0, 2:3, 3:7, 4:11, 5:16, 6:21, 7:28} days
  - deterministic per-lead subject-angle rotation (kills batch-identical subjects)
  - open gate: steps 1-3 to everyone, steps 4+ only to confirmed openers
  - open-triggered ACCELERATION: engaged leads get a hot, back-to-back cadence
  - Claude-drafted subject+body personalized from lead signals + offer brief
  - send via Resend with {sequence_id, step} tags so the webhook can correlate

All state lives in Supabase. No threads, no SQLite, no module-level workers.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from lib import supabase as sb

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "outreach@oltaflock.ai")
SENDER_NAME = os.environ.get("SENDER_NAME", "Khush")
BOOKING_LINK = os.environ.get("BOOKING_LINK", "https://cal.com/khush0030/oltaflock-ai-demo")
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://oltaflock.ai")

OPEN_GATE_FROM_STEP = int(os.environ.get("LEADGEN_OPEN_GATE_FROM_STEP", "4"))
MAX_STEP = 7

# Default offer used when a campaign has no offer_brief. Oltaflock's horizontal
# AI operations-audit play with the 2-to-3-month ROI guarantee. No em/en dashes.
DEFAULT_OFFER = (
    "Oltaflock runs a free AI operations audit for your business. We map where time and "
    "money leak (manual admin, slow lead follow-up, repetitive back-office work, scheduling, "
    "quoting), then design and run the AI systems that remove that waste. You see measurable "
    "ROI within 2 to 3 months of going live or you do not pay. No long contracts."
)

# Cold cadence — absolute days from step 1.
STEP_OFFSETS_DAYS = {1: 0, 2: 3, 3: 7, 4: 11, 5: 16, 6: 21, 7: 28}
# Hot cadence — hours to wait before the next step once a lead has opened.
# Aggressive, back-to-back while engagement is fresh.
HOT_GAP_HOURS = {1: 20, 2: 24, 3: 30, 4: 36, 5: 42, 6: 48, 7: 54}

# Tue / Wed / Thu, 10am local (proven send window for cold sends).
ALLOWED_WEEKDAYS = {int(x) for x in os.environ.get("LEADGEN_SEND_WEEKDAYS", "1,2,3").split(",") if x.strip()}
PREFERRED_HOUR = int(os.environ.get("LEADGEN_DEFAULT_SEND_HOUR", "10"))

REGION_TZ = {
    "us": "America/New_York", "united states": "America/New_York",
    "canada": "America/Toronto", "ca": "America/Toronto",
    "uk": "Europe/London", "united kingdom": "Europe/London",
    "australia": "Australia/Sydney", "au": "Australia/Sydney",
    "new zealand": "Pacific/Auckland", "nz": "Pacific/Auckland",
    "india": "Asia/Kolkata", "in": "Asia/Kolkata",
}


# ─────────── Subject angle rotation ───────────
SUBJECT_ANGLES = [
    ("number-as-question", "lead with rating/review_count/scale framed as a question"),
    ("surprising-stat", "open with a believable stat on hours lost to manual admin"),
    ("curiosity-gap", "tease the operational leak without naming the fix"),
    ("micro-ask", "ask for one tiny low-commitment unit of time"),
    ("competitor-scenario", "name a hypothetical local competitor already using AI"),
    ("industry-insider", "a self-aware line only someone in their trade would say"),
    ("number-as-cost", "quantify the wasted time or money using a believable range"),
    ("but-flip", "two facts in contradiction (busy team, manual back office, why)"),
    ("tiny-imperative", "a two-to-four word command that sets up the body"),
    ("time-of-day", "anchor on when the repetitive work piles up"),
]


def angle_for(email: str, step: int) -> tuple[str, str]:
    seed = f"{email}|{step}"
    idx = sum(ord(c) for c in seed) % len(SUBJECT_ANGLES)
    return SUBJECT_ANGLES[idx]


# ─────────── Step instructions (proven copy) ───────────
STEP_INSTRUCTIONS = {
    1: ("Step 1, first cold email (day 0, 90-140 words). Lead with a SPECIFIC observation pulled "
        "from the lead facts (business name, city, niche, and scale signals like review count). One "
        "short paragraph naming a likely operational drain for a business this size: hours lost to "
        "manual admin, slow lead follow-up, repetitive back-office work, scheduling or quoting by hand. "
        "Introduce the offer: a free AI operations audit that pinpoints where time and money leak, then "
        "we build and run the AI systems that remove that waste. Deliver the risk reversal verbatim "
        "('you see measurable ROI within 2 to 3 months of going live or you do not pay'). Soft CTA: a "
        "short reply or a 15-minute call."),
    2: ("Step 2, bump (day 3, 35-70 words). Acknowledge no reply in ONE casual line. Drop a single "
        "concrete outcome framed generically: e.g. 'a similar business cut roughly 12 hours a week of "
        "manual admin in the first month after the audit'. End with a one-line question. No re-pitch of "
        "the offer."),
    3: ("Step 3, competitor / FOMO angle (day 7, 90-140 words). Open with the observation that another "
        "business in the same space (do not name them) is already using AI to absorb the repetitive work "
        "and is moving faster on the same headcount. Use a believable stat to ground the cost of staying "
        "fully manual. Tie back to the risk reversal in one sentence. CTA: 'want me to send the 90-second "
        "overview of what we would audit first?'"),
    4: ("Step 4, value drop (day 11, 70-110 words). Tease ONE concrete thing the audit surfaces for THIS "
        "niche (e.g. how many hours per week go to quoting, scheduling, and follow-up that AI can absorb). "
        "If a walkthrough or booking link is provided in the offer, paste it on its own line; otherwise "
        "offer to send it. CTA: reply 'yes' to talk it through."),
    5: ("Step 5, grand-slam recap (day 16, 90-140 words). Frame the math so plainly that NOT trying it "
        "looks like the riskier choice. Structure: (1) one line naming what staying manual costs per month "
        "in believable time and money terms. (2) the offer restated in three short lines: free audit, we "
        "design and run the systems, you only pay once you see ROI in 2 to 3 months. (3) one line: 'the "
        "only way this costs you is if it works and you keep it.' CTA: 15 minutes this week, their pick of "
        "day. NO buzzwords, NO hype words. Just arithmetic."),
    6: ("Step 6, quirky pattern interrupt (day 21, 60-100 words). Drop tone. Open with a self-aware "
        "one-liner that admits they have been ignoring the thread, e.g. 'either my emails are landing in "
        "spam or the manual grind is not actually bothering you, and I genuinely cannot tell which.' Then "
        "ONE crisp benefit line tied to the niche. CTA must be a binary low-effort reply: 'reply yes for a "
        "5-min walkthrough, reply no and I close the loop.'"),
    7: ("Step 7, breakup (day 28, 35-70 words). Last note. Polite, short, leaves the door open. Include "
        "this exact mechanic verbatim: 'Reply with one word and I will act on it: STOP means I will not "
        "email again. CALL means book a 15-minute slot. LATER means I circle back in 90 days.' One "
        "stat-free sentence above it framing why it still matters. Nothing else."),
}

SUBJECT_MANDATE = (
    "SUBJECT RULES (hard): must contain ONE concrete identifier from this lead "
    "(business name, city, rating, or review count); under 45 characters; lowercase preferred; "
    "use the assigned angle below; never reuse the body's first sentence as the subject; "
    "no generic ratio patterns like 'X in N -> Y in M'; "
    "never use em dashes or en dashes.\n"
    "Assigned angle: {angle_name}, {angle_desc}"
)


# ─────────── Dash sanitizer (hard brand rule: no em/en dashes, ever) ───────────
def strip_dashes(text: str) -> str:
    """Remove em/en/other long dashes from any lead-facing copy.

    Em dash and figure/quote dashes become a comma+space (clause break).
    En dash becomes a hyphen (it usually joins a range like '2-3').
    Then we clean up the punctuation artifacts that creates.
    """
    if not text:
        return text
    for ch in ("—", "―", "‒", "⸺", "⸻"):  # em + figure/horizontal/two-three-em dashes
        text = text.replace(f" {ch} ", ", ").replace(ch, ", ")
    text = text.replace("–", "-")  # en dash -> hyphen
    # Collapse artifacts: ", ," / " ," / space before comma / doubled commas/periods.
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\.\s*,", ". ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ─────────── Scheduling ───────────
def _tz_for(region: str | None) -> ZoneInfo:
    key = (region or "").strip().lower()
    name = REGION_TZ.get(key)
    if not name:
        for k, v in REGION_TZ.items():
            if k in key:
                name = v
                break
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def next_send_at(step: int, opens: int, region: str | None, *, from_time: datetime | None = None) -> datetime:
    """Compute when the NEXT step should send.

    step = the step we just sent (or 0 if none). We schedule step+1.
    opens > 0 → hot, accelerated, weekday gate bypassed.
    """
    now = from_time or datetime.now(timezone.utc)
    nxt = step + 1
    tz = _tz_for(region)

    if opens > 0:
        # Hot path — back-to-back. Respect only the preferred hour, any weekday.
        gap_h = HOT_GAP_HOURS.get(step, 36)
        target = now + timedelta(hours=gap_h)
        local = target.astimezone(tz)
        local = local.replace(hour=PREFERRED_HOUR, minute=0, second=0, microsecond=0)
        if local < target.astimezone(tz):
            local += timedelta(days=1)
        return local.astimezone(timezone.utc)

    # Cold path — absolute offset, snap to Tue/Wed/Thu @ preferred hour local.
    gap_days = STEP_OFFSETS_DAYS.get(nxt, 0) - STEP_OFFSETS_DAYS.get(step, 0)
    target = now + timedelta(days=max(gap_days, 0))
    local = target.astimezone(tz).replace(hour=PREFERRED_HOUR, minute=0, second=0, microsecond=0)
    for _ in range(14):
        if local.weekday() in ALLOWED_WEEKDAYS and local.astimezone(timezone.utc) >= now:
            break
        local += timedelta(days=1)
        local = local.replace(hour=PREFERRED_HOUR, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


# ─────────── Drafting ───────────
def _facts_block(lead: dict) -> str:
    s = lead.get("signals") or {}
    facts = {
        "business": lead.get("business"),
        "city": lead.get("city"),
        "country": lead.get("country"),
        "website": lead.get("website") or "no website",
        "rating": s.get("rating"),
        "review_count": s.get("user_rating_count") or s.get("review_count"),
        "business_type": s.get("business_type"),
    }
    return "\n".join(f"- {k}: {v}" for k, v in facts.items() if v is not None)


def _engagement_block(seq: dict) -> str:
    opens = seq.get("opens", 0)
    clicks = seq.get("clicks", 0)
    step = seq.get("current_step", 0)
    if step == 0:
        return "No prior emails sent."
    parts = [f"{step} email(s) sent so far."]
    if opens:
        parts.append(f"They have OPENED {opens} time(s) — they are warm, be more direct and time-sensitive.")
    if clicks:
        parts.append(f"They CLICKED {clicks} time(s) — high intent.")
    if not opens:
        parts.append("No opens yet — keep it short and pattern-interrupting.")
    return " ".join(parts)


def _fallback_draft(lead: dict, step: int, angle_name: str) -> dict:
    biz = lead.get("business", "your team")
    city = lead.get("city") or ""
    subs = {
        1: f"quick one about {biz}".lower()[:45],
        2: f"following up on {biz}".lower()[:45],
        3: f"what {city or 'a competitor'} is trying".lower()[:45],
        4: f"60-sec overview for {biz}".lower()[:45],
        5: f"the math on {biz}".lower()[:45],
        6: f"last useful idea for {biz}".lower()[:45],
        7: f"closing the loop, {biz}".lower()[:45],
    }
    body = (f"Hi,\n\nReaching out about {biz}"
            + (f" in {city}" if city else "")
            + ". We run a free AI operations audit that finds where time and money leak in "
              "businesses like yours, then build the AI systems to fix it. You see measurable "
              "ROI within 2 to 3 months or you do not pay.\n\n"
              "Worth a quick 15-minute call?\n\n" + SENDER_NAME)
    subject = strip_dashes(subs.get(step, f"re: {biz}".lower()[:45]))
    return {"subject": subject, "body": strip_dashes(body), "angle": angle_name, "model": "fallback"}


def draft_one(lead: dict, seq: dict, step: int, offer_brief: str | None) -> dict:
    email = lead.get("email") or ""
    angle_name, angle_desc = angle_for(email, step)

    if not ANTHROPIC_API_KEY:
        return _fallback_draft(lead, step, angle_name)

    instruction = STEP_INSTRUCTIONS.get(step, STEP_INSTRUCTIONS[1])
    mandate = SUBJECT_MANDATE.format(angle_name=angle_name, angle_desc=angle_desc)

    system = (
        "You write cold outreach emails that book qualified meetings. Plain, specific, "
        "human, never corporate or buzzwordy. You return ONLY valid JSON: "
        '{"subject": "...", "body": "..."}. The body is plain text, no signature '
        "(it is appended later). Use real line breaks.\n\n"
        "HARD STYLE RULE: never use em dashes or en dashes anywhere in the subject or body. "
        "Use commas, periods, or parentheses instead. This is a strict brand rule, no exceptions.\n\n"
        f"OFFER / CONTEXT:\n{offer_brief or DEFAULT_OFFER}"
    )

    user = (
        f"STEP {step} OF 7 — INSTRUCTION:\n{instruction}\n\n"
        f"LEAD FACTS:\n{_facts_block(lead)}\n\n"
        f"ENGAGEMENT:\n{_engagement_block(seq)}\n\n"
        f"{mandate}\n\n"
        "Return ONLY the JSON object."
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 900,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            return _fallback_draft(lead, step, angle_name)
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0) if m else text)
        subject = strip_dashes((data.get("subject") or "").strip())[:120]
        body = strip_dashes((data.get("body") or "").strip())
        if not subject or not body:
            return _fallback_draft(lead, step, angle_name)
        return {"subject": subject, "body": body, "angle": angle_name, "model": MODEL}
    except Exception:
        return _fallback_draft(lead, step, angle_name)


# ─────────── Compose + send ───────────
def _signature() -> str:
    lines = [f"\n\n{SENDER_NAME}"]
    if WEBSITE_URL:
        lines.append(WEBSITE_URL)
    if BOOKING_LINK:
        lines.append(f"Book a time: {BOOKING_LINK}")
    return "\n".join(lines)


def _html_body(body: str) -> str:
    esc = (body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    # Wrap bare URLs in anchors so Resend's click tracking can rewrite them.
    # Without a real <a href>, clicks are never tracked.
    esc = re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', esc)
    html = esc.replace("\n", "<br>")
    return f'<div style="font-family:Georgia,serif;font-size:15px;line-height:1.55;color:#111;">{html}</div>'


def send_email(lead: dict, draft: dict, seq_id: int, step: int) -> dict:
    """Send via Resend with correlation tags. Returns {resend_id} or {error}."""
    if not RESEND_API_KEY:
        return {"error": "RESEND_API_KEY missing"}
    to = lead.get("email")
    if not to:
        return {"error": "lead has no email"}

    body = draft["body"] + _signature()
    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": draft["subject"],
        "text": body,
        "html": _html_body(body),
        "tags": [
            {"name": "sequence_id", "value": str(seq_id)},
            {"name": "step", "value": str(step)},
        ],
    }
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    if r.status_code >= 400:
        return {"error": f"resend {r.status_code}: {r.text[:200]}"}
    return {"resend_id": (r.json() or {}).get("id")}


def send_manual(to_email: str, subject: str, body: str, seq_id: int,
                *, append_signature: bool = True, signature: str | None = None,
                from_email: str | None = None, from_name: str | None = None) -> dict:
    """One-off composer send (Gmail-style). Same Resend pipe as the sequence so
    opens/clicks/replies still correlate via the sequence_id tag. `seq_id` is a
    real sequence row id used only for tracking — the tick never touches it.

    from_email / from_name override the sender (per-user identity); `signature`
    overrides the default sign-off block. Returns {resend_id} or {error}."""
    if not RESEND_API_KEY:
        return {"error": "RESEND_API_KEY missing"}
    if not to_email:
        return {"error": "no recipient"}
    if append_signature:
        sig = ("\n\n" + signature.strip()) if signature else _signature()
    else:
        sig = ""
    text = strip_dashes(body) + sig
    addr = from_email or RESEND_FROM
    frm = f"{from_name} <{addr}>" if from_name else addr
    payload = {
        "from": frm,
        "to": [to_email],
        "subject": strip_dashes(subject),
        "text": text,
        "html": _html_body(text),
        "tags": [
            {"name": "sequence_id", "value": str(seq_id)},
            {"name": "step", "value": "0"},
            {"name": "kind", "value": "manual"},
        ],
    }
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    if r.status_code >= 400:
        return {"error": f"resend {r.status_code}: {r.text[:200]}"}
    return {"resend_id": (r.json() or {}).get("id")}


# ─────────── Gate ───────────
def passes_open_gate(seq: dict, next_step: int) -> bool:
    if next_step < OPEN_GATE_FROM_STEP:
        return True
    return (seq.get("opens", 0) or 0) > 0
