"""Claude-powered personalization for cold outreach emails.

Pitches our AI services (voice agent, chatbot, automation) to a business given
its name, niche, location, rating, and review count. Falls back to a static
template if the API key is missing.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from . import niche_briefs

load_dotenv()

log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
# Faster model for batch preview drafts. Haiku is plenty for 100-word emails.
BATCH_MODEL = os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001")
BATCH_WORKERS = int(os.getenv("OUTREACH_BATCH_WORKERS", "8"))
BATCH_MAX_TOKENS = int(os.getenv("OUTREACH_BATCH_MAX_TOKENS", "400"))

SYSTEM_PROMPT = """You write short, friendly cold outreach emails to small and mid-market businesses.

You sell AI services: 24/7 voice-answering agents that book appointments, AI chatbots for the business website, and automation that captures missed-call leads.

Output format (STRICT — every email follows the same shape):
- Plain text only. No markdown, no HTML, no emojis, no asterisks, no hyphen-bullets, no numbered lists.
- 80 to 120 words. No more.
- Exactly 3 short paragraphs separated by ONE blank line:
    1. Greeting on its own line: "Hi {first_name}," or "Hi there," — followed by a one-sentence specific observation about THIS business.
    2. Pain point + how it costs them (use the localized stat from the niche playbook when present).
    3. The offer in plain language. Lead with the risk-reversal: "you don't pay until we book a job" + "if we don't book one in 30 days I send you $100 back". End with ONE soft CTA (short reply or 10-min call).
- DO NOT include a signoff, sign-off, "Best,", "Cheers,", sender name, or company name. The email pipeline appends the structured signature from the user's settings (name, title, company, website, booking link). Anything you add will create a duplicate signature.
- Body ends after the CTA. Nothing else.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Never use buzzwords: synergy, leverage, revolutionary, game-changing, unlock, supercharge, transform.
- One concrete observation in the opener (rating, review count, or local area). Never invent facts.
Subject-line rules (CRITICAL — make them open the email):
- Under 50 chars. Lowercase preferred (feels human, not corporate).
- Quirky, curious, pattern-interrupt. Make the recipient stop scrolling.
- Use ONE of these angles, picked to fit THIS prospect:
    a) Specific number framed as a question: "335 reviews and still missing calls?"
    b) Surprising stat: "27% of your phone is voicemail"
    c) Curiosity gap: "the after-hours problem at {business_name}"
    d) Direct micro-ask: "10 mins, then I leave you alone"
    e) Scenario: "what your competitor 3 suburbs over just did"
    f) Self-aware: "still on a roof?"
- NEVER use stock subjects: "Quick idea for X", "Re: X", "Following up", "Just checking in", "Touching base", "Idea for {business_name}".
- No exclamation marks. No ALL CAPS. No emoji. No words like "free", "guaranteed", "win".

Output a JSON object only: {"subject": "...", "body": "..."}.
Inside `body`, separate paragraphs with "\\n\\n" exactly (one blank line). No trailing whitespace, no signoff lines.
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

    body = (
        f"{intro}"
        "Quick question: what happens to calls or website chats that come in after hours, "
        "or when your team is heads-down with customers?\n\n"
        "We help businesses like yours capture those with a 24/7 AI voice agent and a website chatbot "
        "that book appointments, answer common questions, and route real opportunities to a human. "
        "You don't pay until we book your next job, and if we don't book one in 30 days, "
        "I send you $100 back.\n\n"
        "Open to a 10-minute call this week? Happy to share a 60-second demo first if easier."
    )
    subject = f"Quick idea for {name}"
    return {"subject": subject, "body": body, "personalized": False}


def draft_email(lead, sender_name="", model=None, max_tokens=600, client=None):
    """Return {subject, body, personalized: bool}.

    `lead` is a normalized dict (see metrics.normalize_lead).
    `model` defaults to MODEL (Sonnet). Pass BATCH_MODEL for fast preview runs.
    `client` lets callers reuse one Anthropic client across many calls.
    """
    if not ANTHROPIC_API_KEY:
        return _template(lead, sender_name)

    try:
        from anthropic import Anthropic
    except ImportError:
        return _template(lead, sender_name)

    if client is None:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
    chosen_model = model or MODEL

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

    brief = niche_briefs.get_brief_for_lead(lead)
    system_blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if brief:
        system_blocks.extend(niche_briefs.system_blocks(brief))
        user_msg += niche_briefs.user_directive(brief, lead)

    try:
        resp = client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            system=system_blocks,
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


def draft_emails_batch(leads, sender_name=""):
    """Draft N emails fast. Strategy:

      1. Warm prompt cache with one sequential call (so the niche brief block
         is a cache HIT for everyone else — saves tokens + latency).
      2. Fan out remaining leads via a thread pool (Anthropic SDK is
         thread-safe; HTTP-bound so threads work fine).
      3. Use Haiku 4.5 (BATCH_MODEL) — plenty for 100-word cold emails.
      4. Reduced max_tokens (BATCH_MAX_TOKENS).

    Returns a list of {subject, body, personalized, lead} in the original order.
    """
    if not leads:
        return []

    if not ANTHROPIC_API_KEY:
        return [{**_template(l, sender_name), "lead": l} for l in leads]

    try:
        from anthropic import Anthropic
    except ImportError:
        return [{**_template(l, sender_name), "lead": l} for l in leads]

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    results = [None] * len(leads)

    def _one(i, lead):
        d = draft_email(lead, sender_name=sender_name,
                        model=BATCH_MODEL, max_tokens=BATCH_MAX_TOKENS,
                        client=client)
        d["lead"] = lead
        return i, d

    # Warm the prompt cache with the first lead.
    i0, first = _one(0, leads[0])
    results[i0] = first

    rest = list(enumerate(leads))[1:]
    if rest:
        with ThreadPoolExecutor(max_workers=min(BATCH_WORKERS, len(rest))) as ex:
            futures = [ex.submit(_one, i, l) for i, l in rest]
            for fut in as_completed(futures):
                try:
                    i, d = fut.result()
                    results[i] = d
                except Exception as e:
                    log.exception("batch draft failed")
                    # Fill any missing slot with a template so UI never breaks.
                    for j, slot in enumerate(results):
                        if slot is None:
                            tmpl = _template(leads[j], sender_name)
                            tmpl["lead"] = leads[j]
                            tmpl["error"] = str(e)
                            results[j] = tmpl
                            break
    return results
