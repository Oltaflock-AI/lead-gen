"""5-step hyper-personalized email sequence engine.

Cadence (offsets from step 1 send time, span ~14 days):
  step 1  ·  day 0   — cold open with risk-reversal offer
  step 2  ·  day 3   — bump + single outcome line
  step 3  ·  day 6   — competitor / FOMO angle
  step 4  ·  day 10  — Loom value drop
  step 5  ·  day 14  — breakup with one final proof hook

Drafting is done up-front by Claude using the lead facts + per-niche offer
record (offer copy + tone notes + Loom URL). Sending is via Resend. Reply
detection (Gmail) flips status='paused' with reason='replied'.

This module owns:
  • `enqueue_lead(...)`     — bulk start from a CSV
  • `start_scheduler(...)`  — daemon thread polling for due steps
  • `process_replies(...)`  — Gmail reply detector → pause sequences
  • `/webhook/resend`       — handled by app.py, calls record_event()
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

log = logging.getLogger(__name__)

# Step day offsets from step 1 send.
STEP_OFFSETS_DAYS = {1: 0, 2: 3, 3: 6, 4: 10, 5: 14}
NUM_STEPS = 5

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


# ─────────────────────────── drafting ───────────────────────────

_SYSTEM = """You write hyper-personalized cold-outreach emails as part of a 5-step drip sequence. Each email feels hand-written for ONE specific business, not a template.

Hard rules:
- Plain text only. No markdown, no HTML, no emojis, no asterisks, no bullets, no numbered lists.
- Step 1 + step 3: 90 to 140 words. Step 4 (Loom): 70 to 110 words. Step 2 (bump) + step 5 (breakup): 35 to 70 words.
- Open with one specific observation about THIS business (use rating, review count, city, niche — never invent facts).
- Make the offer feel inevitable: clear outcome, low friction, reversible. Lead with the risk-reversal: "you don't pay until we book a job" + "if we don't book one in 30 days I send you $100 back". Steal the structure, don't quote it.
- Social-proof framing IS allowed and encouraged: "another {business_type} two suburbs over", "the tradies we work with in {city}", "a {business_type} with a similar review count". DO NOT invent specific company names, dollar figures, or precise client counts. Use only the localized stat from the niche playbook for any hard numbers.
- One soft CTA per email. Never aggressive. Never pushy.
- Never use: synergy, leverage, revolutionary, game-changing, circle back, touch base, just checking in.
- DO NOT include any signoff, sender name, "Best,", "Cheers,", or company name. The pipeline appends the structured signature from the user's settings. Anything you add creates a duplicate.
- Body ends after the CTA. Nothing else.
- Never use em dashes. Use commas, periods, or parentheses instead.

Subject-line rules (CRITICAL — make them open the email):
- Under 50 chars. Lowercase preferred (feels human, not corporate).
- Quirky, curious, pattern-interrupt. Make the recipient stop scrolling.
- Use ONE of these angles per email, vary across the 5 steps so the inbox view feels different each time:
    a) A specific observation framed as a question: "335 reviews and still missing calls?"
    b) A surprising number or comparison: "27% of your phone is voicemail"
    c) An incomplete thought / curiosity gap: "the after-hours problem at {business_name}"
    d) A direct ask in the prospect's voice: "10 mins, then I leave you alone"
    e) A scenario / micro-story: "what your competitor 3 suburbs over just did"
    f) Self-aware bump: "still on a roof?" / "third and final"
- NEVER use these stock subject patterns: "Quick idea for X", "Re: X", "Following up", "Just checking in", "Touching base", "Idea for {business_name}".
- No exclamation marks. No ALL CAPS. No emoji. No spammy punctuation. No words like "free", "guaranteed", "win".

Output JSON: {"subject": "...", "body": "..."}. Nothing else.
"""

_STEP_INSTRUCTIONS = {
    1: (
        "Step 1 — first cold email (day 0). Lead with a SPECIFIC observation "
        "pulled from the lead facts (rating + review count + city + niche). "
        "One short paragraph naming the missed-call pain. Deliver the offer "
        "with the risk-reversal verbatim ('you don't pay until we book a job, "
        "$100 back if we don't book one in 30 days'). Soft CTA: short reply "
        "or 10-minute call."
    ),
    2: (
        "Step 2 — bump (day 3, 35-70 words). Acknowledge no reply in ONE "
        "casual line. Drop a single concrete outcome framed generically: "
        "e.g. 'a {business_type} with a similar review count went from "
        "missing 1 in 4 calls to under 1 in 30 in week one'. End with a "
        "one-line question. No re-pitch of the offer."
    ),
    3: (
        "Step 3 — competitor / FOMO angle (day 6, 90-140 words). Open with "
        "the observation that another {business_type} in the same region "
        "(do not name them) is already running an AI agent and capturing "
        "the after-hours jobs that used to slip past phones like "
        "{business_name}'s. Use the localized stat from the playbook to "
        "ground the cost. Tie back to the risk-reversal in one sentence. "
        "CTA: 'want me to send you the 90-second walkthrough?'"
    ),
    4: (
        "Step 4 — Loom value drop (day 10, 70-110 words). Paste the Loom "
        "URL on its own line. Tease ONE concrete thing the video shows for "
        "THIS niche / business type (e.g. how the agent triages a burst "
        "pipe vs. a slow drip for a plumber). Suggest watching when "
        "convenient. CTA: reply 'yes' to talk after watching."
    ),
    5: (
        "Step 5 — breakup (day 14, 35-70 words). Polite close, leave the "
        "door open. Include ONE final reason to reconsider before they "
        "close the email: a stat from the playbook OR a generic outcome "
        "line. Make it easy to say 'not now' without guilt. No re-pitch of "
        "the offer details."
    ),
}


def _facts_block(lead):
    return (
        f"business_name: {lead.get('business_name', '')}\n"
        f"city: {lead.get('city', '')}\n"
        f"niche: {lead.get('niche', '')}\n"
        f"google_rating: {lead.get('rating', 0)}\n"
        f"review_count: {lead.get('review_count', 0)}\n"
        f"has_website: {bool(lead.get('website'))}\n"
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
    else:
        body = (
            "Last note from me. Happy to circle back later if timing isn't "
            "right. Worth remembering: most callers who hit voicemail dial "
            "the next number on Google within 90 seconds. Reply 'not now' "
            "and I'll close the loop."
        )
        subj = "third and final"
    return {"subject": subj, "body": body}


def _draft_one(step, lead, offer_record, sender_name):
    """Returns {subject, body}. Falls back to template on any LLM error."""
    offer_text = (offer_record or {}).get("offer", "") or ""
    tone = (offer_record or {}).get("tone", "") or ""
    loom_url = (offer_record or {}).get("loom_url", "") or ""

    if not ANTHROPIC_API_KEY:
        return _fallback_draft(step, lead, offer_text, loom_url, sender_name)

    try:
        from anthropic import Anthropic
    except ImportError:
        return _fallback_draft(step, lead, offer_text, loom_url, sender_name)

    user_msg = (
        f"{_STEP_INSTRUCTIONS[step]}\n\n"
        f"sender_name: {sender_name}\n"
        f"--- lead facts ---\n{_facts_block(lead)}\n"
        f"--- offer (rewrite naturally; never quote verbatim) ---\n{offer_text}\n\n"
        f"--- tone notes ---\n{tone}\n\n"
        f"--- loom_url (only used in step 3) ---\n{loom_url}\n\n"
        "Output JSON only."
    )

    brief = niche_briefs.get_brief_for_lead(lead, niche=lead.get("niche", ""))
    sys_blocks = [{"type": "text", "text": _SYSTEM,
                   "cache_control": {"type": "ephemeral"}}]
    if brief:
        sys_blocks.extend(niche_briefs.system_blocks(brief))
        user_msg += niche_briefs.user_directive(brief, lead)

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
    """Pull niche offer record and draft 4 steps. Returns list[{subject, body}]."""
    offer_record = db.get_niche_offer(niche)
    out = []
    for step in range(1, NUM_STEPS + 1):
        out.append(_draft_one(step, lead, offer_record, sender_name))
    return out


# ─────────────────────────── enqueue ───────────────────────────


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _add_days_iso(days, base=None):
    base = base or datetime.now(timezone.utc)
    return (base + timedelta(days=days)).isoformat()


def enqueue_lead(lead, csv_name="", sender_name="", start_at=None):
    """Create sequence + draft all 4 steps + schedule step 1.

    `lead` must contain at least: email, business_name, niche.
    Returns (sequence_id, status_message).
    """
    email = (lead.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None, "invalid email"

    niche = lead.get("niche", "")
    offer = db.get_niche_offer(niche)
    if not offer:
        return None, f"no offer for niche '{niche}' — set one in /offers"

    sid = db.create_sequence(
        lead_email=email,
        business_name=lead.get("business_name", ""),
        niche=niche,
        csv_name=csv_name,
        city=lead.get("city", ""),
    )
    seq = db.get_sequence(sid)
    if seq["current_step"] > 0 or seq["status"] != "active":
        return sid, "already exists"

    drafts = draft_all_steps(lead, niche, sender_name=sender_name)
    base = start_at or datetime.now(timezone.utc)
    for step in range(1, NUM_STEPS + 1):
        d = drafts[step - 1]
        scheduled = _add_days_iso(STEP_OFFSETS_DAYS[step], base)
        db.upsert_sequence_message(sid, step, d["subject"], d["body"], scheduled)

    # Schedule step 1 immediately.
    db.update_sequence(sid, next_send_at=base.isoformat())
    log.info("enqueued sequence %s for %s (niche=%s)", sid, email, niche)
    return sid, "queued"


# ─────────────────────────── send ───────────────────────────


def _send_step(seq, step):
    """Send one step. Returns (ok, error)."""
    msg = db.get_pending_message(seq["id"], step)
    if not msg:
        return False, f"no pending message for step {step}"
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
    or marks done after step 4."""
    if step >= NUM_STEPS:
        db.update_sequence(seq["id"], status="done", current_step=step,
                           next_send_at=None)
        return
    next_step = step + 1
    delay = STEP_OFFSETS_DAYS[next_step] - STEP_OFFSETS_DAYS[step]
    db.update_sequence(
        seq["id"],
        current_step=step,
        next_send_at=_add_days_iso(delay),
    )


def tick():
    """Process all sequences whose next_send_at has elapsed.

    Returns dict with counts.
    """
    now = _now_iso()
    due = db.due_sequences(now, limit=50)
    sent, failed = 0, 0
    for seq in due:
        next_step = seq["current_step"] + 1
        if next_step > NUM_STEPS:
            db.update_sequence(seq["id"], status="done", next_send_at=None)
            continue
        ok, err = _send_step(seq, next_step)
        if ok:
            advance_after_send(seq, next_step)
            sent += 1
        else:
            failed += 1
            # Permanent error? Pause so we don't hammer.
            if err and ("not configured" in err or "INVALID" in err.upper()):
                db.update_sequence(seq["id"], status="paused",
                                   paused_reason=f"send error: {err[:120]}",
                                   next_send_at=None)
    return {"due": len(due), "sent": sent, "failed": failed}


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
    db.log_email_event(resend_id, event, json.dumps(payload)[:8000])

    paused_sid = None
    metric = _RESEND_EVENT_TO_METRIC.get(event)
    if metric:
        db.increment_message_metric(resend_id, metric)
    if event in ("email.bounced", "email.complained") and resend_id:
        msg = db.get_message_by_resend_id(resend_id)
        if msg:
            seq = db.get_sequence(msg["sequence_id"])
            if seq and seq["status"] == "active":
                db.update_sequence(seq["id"], status="paused",
                                   paused_reason=event.replace("email.", ""),
                                   next_send_at=None)
                paused_sid = seq["id"]

    # One-shot outreach analytics: bumps opens/clicks/bounced + status.
    outreach_updated = db.update_outreach_event(resend_id, event)

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
