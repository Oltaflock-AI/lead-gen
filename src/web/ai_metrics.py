"""Claude-driven pipeline forecast + per-lead scoring.

Two entry points:

  forecast_pipeline(per_csv_summaries, niche_hint=None)
    → asks Claude to estimate realistic monthly + annual revenue from the
      current lead pool, given typical close rates for AI-services cold outreach
      and per-niche fit.

  score_leads(leads)
    → scores up to 50 leads at a time on a 0-100 scale with a one-line reason
      each, optimized for "would this business pay $200-1500/mo for an AI voice
      agent or chatbot?".

Both fall back gracefully when ANTHROPIC_API_KEY is missing.
"""
import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

SYSTEM_FORECAST = """You are a sales operations analyst forecasting revenue for an AI services agency that sells:
- 24/7 AI voice agents that answer calls and book appointments ($300-1500/month subscriptions)
- AI chatbots embedded on the customer's website ($200-800/month)
- Missed-call recovery + appointment booking automation ($150-500/month)

Given a pool of cold-outreach leads with quality signals (rating, review count, AI fit score, geography, niche), estimate realistic monthly recurring revenue. Be conservative.

Cold outreach close rates for this segment are typically 0.5%-3% from contacted leads to closed customer. Most signed deals start at the lower end of the price band.

Output strict JSON only, no prose:
{
  "projected_monthly_mrr": <float, USD>,
  "projected_annual_revenue": <float, USD>,
  "expected_close_rate": <float between 0 and 1>,
  "expected_deal_value_monthly": <float, USD per closed deal per month>,
  "expected_deals": <int>,
  "confidence": "low" | "medium" | "high",
  "reasoning": "<60-100 word paragraph explaining the assumptions>",
  "per_csv": [
    {"name": "<csv name>", "expected_deals": <int>, "expected_mrr": <float>}
  ]
}
"""

SYSTEM_SCORE_LEADS = """You score cold-outreach leads on TWO independent dimensions, each 0-100, for our AI services (24/7 voice agent, website chatbot, missed-call recovery + booking automation).

The lead pool is mostly small-to-mid local service businesses (typically 20-300 reviews, 4.0-5.0 stars). Calibrate so a *typical solid local business* (4.6 stars, 60 reviews, email + phone) lands in 65-75 — NOT 30-45. Reserve 85+ for strong leads and 30 or below for weak ones. Use the full 0-100 range.

DIMENSION 1 — niche_fit (0-100)
How well does this BUSINESS NICHE match what our AI services solve?
- 80-100: appointment-driven service business with heavy phone bookings (plumbers, HVAC, electricians, roofers, dentists, medical clinics, lawyers, salons, auto repair, veterinarians, fitness studios)
- 60-79: hybrid business with some booking flow (real estate, restaurants taking reservations, hotels, photographers)
- 40-59: service business but lower phone-booking volume (consultants, agencies)
- 0-39: pure retail / e-commerce / one-time transactional (clothing store, grocery, food truck without booking)

DIMENSION 2 — lead_score (0-100)
Will THIS SPECIFIC business respond to cold email and become a customer?

Anchor bands (calibrated for local SMB pool — most leads should NOT score below 50 unless genuinely weak):
- 85-100: rating >= 4.5 AND reviews >= 100 AND has email AND has phone AND established
- 70-84: rating >= 4.3 AND reviews >= 30 AND has email AND has phone (typical solid local biz)
- 55-69: rating >= 4.0 AND reviews >= 15 AND (has email OR has phone) (decent local biz)
- 40-54: thinner signal — low review count (5-15), or missing one contact channel, or rating 3.5-4.0
- 25-39: very few reviews (<5), no contact info at all, or low rating with many reviews (chronic complaints)
- 0-24: no rating data, ghost listing, or 3-star with hundreds of reviews

Boost: both email AND phone present, no website (more receptive to AI booking).
Penalize: no contact channel, rating < 3.5 with high review count, too small to afford $200-1500/mo.

Output a JSON array with EXACTLY one entry per input lead, in the same order:
[{"niche_fit": <int 0-100>, "lead_score": <int 0-100>, "reason": "<15-25 word justification naming the key signals — rating, review count, contact channels, niche>"}]
"""


def is_enabled():
    return bool(ANTHROPIC_API_KEY)


def _client():
    from anthropic import Anthropic
    return Anthropic(api_key=ANTHROPIC_API_KEY)


def _extract_json(text, container):
    """container is '{' or '['."""
    end = '}' if container == '{' else ']'
    start = text.find(container)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == container:
            depth += 1
        elif text[i] == end:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def forecast_pipeline(per_csv_summaries):
    """Returns dict with the Claude forecast or {error: ...} on failure."""
    if not is_enabled():
        return {"error": "ANTHROPIC_API_KEY not set"}

    payload = []
    for c in per_csv_summaries:
        payload.append({
            "name": c.get("name"),
            "rows": c.get("rows", 0),
            "qualified_leads_score_60_plus": c.get("qualified", 0),
            "avg_quality_score": c.get("avg_score", 0),
            "with_email": c.get("with_email", 0),
            "with_phone": c.get("with_phone", 0),
            "avg_ai_fit": c.get("avg_fit", 0),
            "ai_fit_checked": c.get("fit_checked", 0),
        })

    user_msg = (
        "Forecast monthly + annual recurring revenue from this lead pool. "
        "Each entry is a CSV of cold-outreach leads we plan to email. "
        "Account for niche typical close rates and conservative deal sizes.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )

    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=1500,
            system=[{"type": "text", "text": SYSTEM_FORECAST,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        parsed = _extract_json(text, "{")
        if not parsed:
            return {"error": "Could not parse Claude response", "raw": text[:500]}
        return parsed
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def score_leads(leads):
    """Bulk score up to 50 leads. Returns list of {niche_fit, lead_score, reason}."""
    if not is_enabled():
        return [{"niche_fit": None, "lead_score": None, "reason": "AI scoring disabled (no ANTHROPIC_API_KEY)"} for _ in leads]
    if not leads:
        return []

    batch = leads[:50]
    payload = []
    for l in batch:
        payload.append({
            "business_name": l.get("business_name", ""),
            "niche": l.get("business_type", ""),
            "city": l.get("city", ""),
            "rating": l.get("rating", 0),
            "reviews": l.get("review_count", 0),
            "has_email": bool(l.get("email")),
            "has_phone": bool(l.get("phone")),
            "has_website": bool(l.get("website")),
        })

    user_msg = (
        "Score the following leads. Return JSON array of length "
        f"{len(batch)}, same order.\n\n{json.dumps(payload)}"
    )

    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=4000,
            system=[{"type": "text", "text": SYSTEM_SCORE_LEADS,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        parsed = _extract_json(text, "[")
        if not isinstance(parsed, list):
            return [{"niche_fit": None, "lead_score": None, "reason": "no JSON array in response"} for _ in batch]
        while len(parsed) < len(batch):
            parsed.append({"niche_fit": None, "lead_score": None, "reason": ""})
        return parsed[:len(batch)]
    except Exception as e:
        return [{"niche_fit": None, "lead_score": None, "reason": f"{type(e).__name__}: {e}"} for _ in batch]
