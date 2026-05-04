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

SYSTEM_SCORE_LEADS = """You score cold outreach leads from 0 to 100 for fit with our AI services (voice agent, website chatbot, missed-call recovery + booking automation).

Higher score = more likely to (a) need our services, (b) be willing to pay $200-1500/month, (c) respond to cold email.

Strong positive signals:
- Service businesses that take phone bookings (home services, law firms, medical, salons, auto repair)
- High rating (4.3+) AND many reviews (200+) → established, has revenue
- Operates with after-hours gap → voice agent fit
- No website → chatbot/website agent upside

Negative signals:
- Restaurants, retail, transactional businesses (less appointment-driven)
- Very high reviews but low rating (operational issues, won't buy)
- Tiny review count (<20) → likely too small to afford SaaS

Output a JSON array with EXACTLY one entry per input lead, in the same order:
[{"score": <int 0-100>, "reason": "<8-15 word justification>"}]
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
    """Bulk score up to 50 leads. Returns list of {score, reason} same length as input."""
    if not is_enabled():
        return [{"score": None, "reason": "AI scoring disabled (no ANTHROPIC_API_KEY)"} for _ in leads]
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
            return [{"score": None, "reason": "no JSON array in response"} for _ in batch]
        # Pad or truncate to match the batch length.
        while len(parsed) < len(batch):
            parsed.append({"score": None, "reason": ""})
        return parsed[:len(batch)]
    except Exception as e:
        return [{"score": None, "reason": f"{type(e).__name__}: {e}"} for _ in batch]
