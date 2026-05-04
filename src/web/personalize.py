"""Claude-powered personalization for cold outreach emails.

Pitches our AI services (voice agent, chatbot, automation) to a business given
its name, niche, location, rating, and review count. Falls back to a static
template if the API key is missing.
"""
import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = """You write short, friendly cold outreach emails to small and mid-market businesses.

You sell AI services: 24/7 voice-answering agents that book appointments, AI chatbots for the business website, and automation that captures missed-call leads.

Rules for every email you write:
- 90 to 130 words. No more.
- Plain text only. No markdown, no HTML, no emojis.
- One concrete observation about the business in the opening line (use their rating, review count, or local area — never invent facts).
- One specific pain point that AI can solve for their niche (missed after-hours calls, slow lead response, repetitive booking questions, no-show reminders).
- One soft CTA: a short reply or a 10-minute call. Never aggressive.
- Never use words like "synergy", "leverage", "revolutionary", "game-changing".
- Sign off with the sender_name provided.
- Output a JSON object: {"subject": "...", "body": "..."}. Subject under 60 chars, no spammy ALL CAPS or excessive punctuation.
"""


def _template(lead, sender_name=""):
    name = lead.get("business_name", "your business")
    city = lead.get("city", "")
    city_str = f" in {city}" if city else ""
    rating = lead.get("rating", 0)
    reviews = lead.get("review_count", 0)
    intro = ""
    if rating and reviews:
        intro = f"Saw {name} has a {rating}★ average across {reviews} reviews{city_str} — congrats on the reputation. "
    else:
        intro = f"Came across {name}{city_str} and wanted to reach out. "

    sign_off = f"Best,\n{sender_name}" if sender_name else "Best,"
    body = (
        f"{intro}"
        "Quick question: what happens to calls or website chats that come in after hours, "
        "or when your team is heads-down with customers?\n\n"
        "We help businesses like yours capture those with a 24/7 AI voice agent and a website chatbot "
        "that book appointments, answer common questions, and route real opportunities to a human. "
        "Most clients see a noticeable lift in booked jobs within the first month.\n\n"
        "Open to a 10-minute call this week? Happy to share a 60-second demo first if easier.\n\n"
        f"{sign_off}"
    )
    subject = f"Quick idea for {name}"
    return {"subject": subject, "body": body, "personalized": False}


def draft_email(lead, sender_name=""):
    """Return {subject, body, personalized: bool}.

    `lead` is a normalized dict (see metrics.normalize_lead).
    """
    if not ANTHROPIC_API_KEY:
        return _template(lead, sender_name)

    try:
        from anthropic import Anthropic
    except ImportError:
        return _template(lead, sender_name)

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    facts = (
        f"business_name: {lead.get('business_name', '')}\n"
        f"city: {lead.get('city', '')}\n"
        f"address: {lead.get('address', '')}\n"
        f"business_type / niche: {lead.get('business_type', '')}\n"
        f"google_rating: {lead.get('rating', 0)}\n"
        f"review_count: {lead.get('review_count', 0)}\n"
        f"has_website: {bool(lead.get('website'))}\n"
        f"sender_name: {sender_name}\n"
    )

    user_msg = (
        "Write a personalized cold outreach email pitching our AI services "
        "(voice agent, chatbot, missed-call recovery) to this business. "
        "Use only the facts below. Output JSON only.\n\n"
        f"{facts}"
    )

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        fallback = _template(lead, sender_name)
        fallback["error"] = str(e)
        return fallback

    import json as _json
    import re as _re

    m = _re.search(r"\{.*\}", text, _re.DOTALL)
    if not m:
        return _template(lead, sender_name)

    try:
        parsed = _json.loads(m.group(0))
        return {
            "subject": parsed.get("subject", f"Quick idea for {lead.get('business_name', '')}"),
            "body": parsed.get("body", ""),
            "personalized": True,
        }
    except Exception:
        return _template(lead, sender_name)
