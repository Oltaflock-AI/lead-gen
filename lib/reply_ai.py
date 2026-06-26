"""Phase 4 — reply intelligence: classify a lead's reply and draft a response.

The Gmail poller captures inbound replies; this module asks Claude to (1) label
the intent and (2) draft a tailored response the operator can send in one click
from the dashboard inbox. We reuse the sequencer's brand rules (no em/en dashes,
sell the outcome not the AI, no booking links — CTAs live in the body as a plain
ask for availability) and the offer context, so replies sound like the same
person who sent the cold email.

Best-effort: if the API key is missing or Claude fails, we fall back to a safe
generic acknowledgement so a reply is never dropped on the floor.
"""
import json
import os
import re

import requests

from lib import sequence as seq  # reuse MODEL, key, offer, strip_dashes, sender name

INTENTS = {"interested", "objection", "not_now", "stop", "other"}

_GUIDANCE = {
    "interested": ("They are interested or asking to learn more. Be warm and concrete, match their "
        "energy, and move to a call: ask which day/time works this week (do NOT paste any booking "
        "link, just ask for their availability). Confirm one clear next step."),
    "objection": ("They raised an objection or hesitation (price, timing, doubt, 'we already have X'). "
        "Acknowledge it honestly in one line, address THAT specific concern, and lean on the risk "
        "reversal (measurable ROI in 2 to 3 months or they do not pay). Low pressure, end with one "
        "easy question."),
    "not_now": ("Not the right time. Be gracious, no pressure. Agree to circle back, and ask when a "
        "better time would be (or offer to follow up in about 90 days). Leave the door wide open."),
    "stop": ("They want no further contact. Draft a one-line polite acknowledgement that we will not "
        "email again. Nothing else, no pitch."),
    "other": ("Intent is unclear or it's a logistical/neutral reply. Write a short, helpful, human "
        "response that moves the conversation forward with one simple question."),
}


def _system(offer_brief: str | None) -> str:
    return (
        "You handle replies to cold outreach for Oltaflock. You write like a real founder: plain, "
        "specific, warm, never corporate. You return ONLY valid JSON: "
        '{"intent": "...", "subject": "...", "body": "..."}. intent is exactly one of '
        "interested, objection, not_now, stop, other. The body is plain text, no signature "
        "(it is appended later), and uses real line breaks.\n\n"
        "HARD RULES: never use em dashes or en dashes (use commas, periods, parentheses). Never paste "
        "a booking or scheduling link; if proposing a call, just ask for their availability. Sell the "
        "business outcome (revenue, hours saved, closings), never lead with the AI mechanism.\n\n"
        f"OFFER / CONTEXT:\n{offer_brief or seq.DEFAULT_OFFER}"
    )


def _facts(lead: dict) -> str:
    bits = {
        "business": lead.get("business"),
        "city": lead.get("city"),
        "country": lead.get("country"),
    }
    return ", ".join(f"{k}: {v}" for k, v in bits.items() if v) or "(no extra facts)"


def _fallback(intent: str, lead: dict) -> dict:
    biz = lead.get("business") or "there"
    body = (f"Thanks for the reply. Happy to share more on how we'd help {biz} specifically. "
            "Would a quick 15 minute call work this week? If so, what times suit you?")
    return {"intent": intent if intent in INTENTS else "other",
            "subject": "re: your note", "body": seq.strip_dashes(body), "model": "fallback"}


def classify_and_draft(in_body: str, in_subject: str | None, lead: dict,
                       offer_brief: str | None = None, hint_stop: bool = False) -> dict:
    """Return {intent, subject, body, model}. `hint_stop` biases toward 'stop'
    when the regex pre-filter already flagged opt-out language."""
    pre = "stop" if hint_stop else "other"
    if not seq.ANTHROPIC_API_KEY or not (in_body or "").strip():
        return _fallback(pre, lead)

    reply_subject = (in_subject or "").strip() or "(no subject)"
    user = (
        f"LEAD: {_facts(lead)}\n\n"
        f"THEIR REPLY (subject: {reply_subject}):\n\"\"\"\n{in_body.strip()[:4000]}\n\"\"\"\n\n"
        "First classify the intent, then follow the matching guidance:\n"
        + "\n".join(f"- {k}: {v}" for k, v in _GUIDANCE.items())
        + ("\n\nNote: an opt-out pre-filter already flagged this as likely STOP; "
           "lean to 'stop' unless the text clearly says otherwise." if hint_stop else "")
        + "\n\nDraft a reply subject (keep their thread, e.g. prefix 're:') and body. "
          "Return ONLY the JSON object."
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": seq.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": seq.MODEL,
                "max_tokens": 700,
                "system": [{"type": "text", "text": _system(offer_brief),
                            "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            return _fallback(pre, lead)
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0) if m else text)
        intent = (data.get("intent") or "").strip().lower()
        if intent not in INTENTS:
            intent = pre
        subject = seq.strip_dashes((data.get("subject") or "").strip())[:160] or "re: your note"
        body = seq.strip_dashes((data.get("body") or "").strip())
        if not body:
            return _fallback(intent, lead)
        return {"intent": intent, "subject": subject, "body": body, "model": seq.MODEL}
    except Exception:
        return _fallback(pre, lead)
