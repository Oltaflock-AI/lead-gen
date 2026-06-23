"""Phase 3 — deep-research lead enrichment.

Pure, stateless enrichment. `enrich_lead(lead)` takes a leads row dict and
returns a PATCH dict for that row — it does NOT touch the database (the cron
handler owns the writes). Logic:

  1. If the lead has a website, fetch + strip it to plain text (best-effort).
  2. Ask Claude to extract buying signals, pain, decision-maker, intent score
     and a one-line summary as strict JSON (same REST shape as sequence.py).
  3. Merge the extracted signals onto any existing lead["signals"] and stamp
     enrichment_status='enriched' so the lead never gets stuck 'pending'.

If ANTHROPIC_API_KEY is missing or Claude fails we STILL return an 'enriched'
patch (with an enrich_note) — but we never fabricate intent_score, it stays None.
"""
import json
import os
import re
from datetime import datetime, timezone

import requests

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

USER_AGENT = os.environ.get(
    "LEADGEN_ENRICH_UA",
    "Mozilla/5.0 (compatible; OltaflockBot/1.0; +https://oltaflock.ai/bot)",
)
SITE_TEXT_CAP = 6000


# ─────────── Website fetch + strip ───────────
def _fetch_site_text(url: str | None) -> str:
    """Fetch a URL and return collapsed plain text (~6000 chars). Never raises."""
    if not url:
        return ""
    target = url.strip()
    if not target:
        return ""
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    try:
        r = requests.get(
            target,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=15,
            allow_redirects=True,
        )
        if r.status_code >= 400:
            return ""
        return _strip_html(r.text)
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    # Drop script/style/noscript bodies, then all remaining tags.
    text = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    # Decode the handful of entities that actually matter for prose.
    for ent, ch in (("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(ent, ch)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SITE_TEXT_CAP]


# ─────────── Claude extraction ───────────
def _facts_block(lead: dict) -> str:
    s = lead.get("signals") or {}
    facts = {
        "business": lead.get("business"),
        "website": lead.get("website") or "no website",
        "city": lead.get("city"),
        "country": lead.get("country"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "rating": s.get("rating"),
        "review_count": s.get("user_rating_count") or s.get("review_count"),
        "business_type": s.get("business_type"),
    }
    return "\n".join(f"- {k}: {v}" for k, v in facts.items() if v is not None)


def _extract(lead: dict, site_text: str) -> dict:
    """Call Claude for structured research. Returns {} on any failure."""
    if not ANTHROPIC_API_KEY:
        return {}

    system = (
        "You are a B2B sales-research analyst. Given facts about a local business "
        "and (optionally) the text of its website, you infer concrete, plausible "
        "buying signals for an AI phone + chat agent that captures missed-call and "
        "after-hours leads. You return ONLY valid JSON with EXACTLY these keys:\n"
        '{"buying_signals": ["...", "...", "..."], "pain": "...", '
        '"decision_maker": {"name": null, "title": null, "email": null}, '
        '"intent_score": 0, "summary": "..."}\n'
        "Rules: buying_signals is an array of exactly 3 short strings. pain is one "
        "sentence. decision_maker fields come ONLY from the website text — if a "
        "person's name/title/email is not clearly present, use null (never guess). "
        "intent_score is an integer 0-100 estimating how likely this business needs "
        "the offer now. summary is one sentence. ALSO estimate the business's annual "
        "revenue band as one of EXACTLY these strings (best guess from business type, "
        'review volume, locations, site): "<$1M", "$1M–5M", "$5M–20M", "$20M–50M", '
        '"$50M+" — return it as "revenue_band". No prose outside the JSON.'
    )

    site_block = site_text if site_text else "(no website text available)"
    user = (
        f"BUSINESS FACTS:\n{_facts_block(lead)}\n\n"
        f"WEBSITE TEXT (may be truncated):\n{site_block}\n\n"
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
                "max_tokens": 700,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            return {}
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0) if m else text)
    except Exception:
        return {}


# ─────────── Normalisation ───────────
def _coerce_int_0_100(value) -> int | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


def _clean_signals(data: dict) -> dict:
    """Pull the signal-shaped fields out of Claude's JSON into a flat dict."""
    out: dict = {}
    bs = data.get("buying_signals")
    if isinstance(bs, list):
        out["buying_signals"] = [str(x).strip() for x in bs if str(x).strip()][:3]
    pain = data.get("pain")
    if isinstance(pain, str) and pain.strip():
        out["pain"] = pain.strip()
    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        out["summary"] = summary.strip()
    rev = data.get("revenue_band")
    if isinstance(rev, str) and rev.strip() in ("<$1M", "$1M–5M", "$5M–20M", "$20M–50M", "$50M+"):
        out["revenue_band"] = rev.strip()
    return out


def _clean_decision_maker(data: dict) -> dict:
    dm = data.get("decision_maker")
    if not isinstance(dm, dict):
        return {"name": None, "title": None, "email": None}
    pick = lambda k: (dm.get(k).strip() if isinstance(dm.get(k), str) and dm.get(k).strip() else None)
    return {"name": pick("name"), "title": pick("title"), "email": pick("email")}


# ─────────── Public entrypoint ───────────
def enrich_lead(lead: dict) -> dict:
    """Deep-research a single lead. Returns a PATCH dict for the leads row.

    Pure: no DB calls. The caller persists the returned patch.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    base_signals = dict(lead.get("signals") or {})

    # Email hunt — Places gives none, so scrape the site + Prospeo fallback.
    email_patch: dict = {}
    if not lead.get("email"):
        try:
            from lib import email_finder
            found = email_finder.find_email(lead)
            if found.get("email"):
                email_patch = {"email": found["email"], "email_status": found.get("email_status")}
                base_signals["email_source"] = found.get("email_source")
        except Exception:
            pass

    site_text = _fetch_site_text(lead.get("website"))
    data = _extract(lead, site_text)

    # LLM skipped or failed → unblock the lead but don't fabricate intent.
    if not data:
        base_signals["enrich_note"] = "llm-skipped"
        return {
            "signals": base_signals,
            "intent_score": None,
            "decision_maker": {"name": None, "title": None, "email": None},
            "enrichment_status": "enriched",
            "enriched_at": now_iso,
            **email_patch,
        }

    merged = {**base_signals, **_clean_signals(data)}
    return {
        "signals": merged,
        "intent_score": _coerce_int_0_100(data.get("intent_score")),
        "decision_maker": _clean_decision_maker(data),
        **email_patch,
        "enrichment_status": "enriched",
        "enriched_at": now_iso,
    }
