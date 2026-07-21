"""Phase 3 — deep-research lead enrichment.

Pure, stateless enrichment. `enrich_lead(lead, campaign=None)` takes a leads
row dict (plus, optionally, its campaigns row) and returns a PATCH dict for
that row — it does NOT touch the database (the cron handler owns the writes).
Logic (cheapest first):

  1. Hunt for an email (site scrape + web search, lib/email_finder — free).
  2. If the lead has a website, fetch + strip it to plain text (best-effort),
     then ask the model (OpenAI, see lib/llm.py) to extract buying signals,
     pain, decision-maker, intent score and a one-line summary as strict JSON.
  3. Decision-maker waterfall — STRICTLY opt-in per campaign via
     search_config {"decision_maker_mode": true}:
       a. Pattern-verify (lib/email_verify, ~$0.0037/check): guess likely
          addresses from the known decision-maker name + company domain and
          verify them via MillionVerifier. A verified personal hit beats any
          mx_only role address from the scrape.
       b. Prospeo (lib/prospeo, priciest) — only when pattern-verify found
          nothing and PROSPEO_API_KEY is set.
  4. Merge the extracted signals onto any existing lead["signals"] and stamp
     enrichment_status='enriched' so the lead never gets stuck 'pending'.

If OPENAI_API_KEY is missing or the model fails we STILL return an 'enriched'
patch (with an enrich_note) — but we never fabricate intent_score, it stays None.
"""
import os
import re
from datetime import datetime, timezone

import requests

from lib import llm
from lib import net_guard

USER_AGENT = os.environ.get(
    "LEADGEN_ENRICH_UA",
    "Mozilla/5.0 (compatible; OltaflockBot/1.0; +https://oltaflock.ai/bot)",
)
SITE_TEXT_CAP = 6000

# Decision-maker mode (Prospeo waterfall) — opt-in per campaign.
DEFAULT_TARGET_TITLES = ["owner", "founder", "director", "general manager", "partner"]
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
             "hotmail.com", "outlook.com", "live.com", "icloud.com", "aol.com",
             "rediffmail.com", "protonmail.com", "zoho.com"}


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
        r = net_guard.safe_get(
            target,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            timeout=15,
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


# ─────────── Decision-maker waterfall (Prospeo) ───────────
def _campaign_config(campaign: dict | None) -> dict:
    """Campaign search_config JSON. Mirrors lib/scrape.py: prefer an inlined
    search_config dict, else parse campaigns.notes (the real store — there is
    no dedicated column, see api/index.py)."""
    if not campaign:
        return {}
    cfg = campaign.get("search_config")
    if isinstance(cfg, dict):
        return cfg
    try:
        import json
        parsed = json.loads(campaign.get("notes") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _lead_domain(lead: dict) -> str:
    """Company domain for Prospeo lookups: website first, else the domain of
    an existing business email (never a free-mail provider)."""
    from urllib.parse import urlparse
    site = (lead.get("website") or "").strip()
    if site:
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        host = (urlparse(site).netloc or "").lower()
        host = host.split(":")[0].removeprefix("www.")
        if host:
            return host
    email = (lead.get("email") or "").strip().lower()
    if "@" in email:
        dom = email.split("@")[-1]
        if dom and dom not in FREE_MAIL:
            return dom
    return ""


def _prospeo_person_step(lead: dict, campaign: dict | None, signals: dict) -> dict:
    """Person-level waterfall step. STRICTLY opt-in: runs only when the lead's
    campaign search_config has decision_maker_mode: true. Burn is capped by
    the per-tick budget set in api/cron/enrich_tick.py (lib/prospeo).

    On a verified hit: returns {email, email_status, decision_maker} and
    stamps contact + firmographic fields onto `signals` in place.
    Returns {} on miss/disabled/no-budget. Never raises.
    """
    try:
        cfg = _campaign_config(campaign)
        if not cfg.get("decision_maker_mode"):
            return {}
        dm_existing = lead.get("decision_maker") or {}
        if isinstance(dm_existing, dict) and dm_existing.get("source") == "prospeo":
            return {}  # already person-enriched — don't re-burn credits
        domain = _lead_domain(lead)
        if not domain:
            return {}

        from lib import prospeo
        if not prospeo.enabled():
            return {}

        titles = cfg.get("target_titles")
        if not (isinstance(titles, list) and any(isinstance(t, str) and t.strip() for t in titles)):
            titles = DEFAULT_TARGET_TITLES

        person = prospeo.enrich_person(
            company_domain=domain, job_titles=titles, only_verified_email=True)
        email = (person.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return {}

        dm = {
            "name": person.get("full_name"),
            "title": person.get("job_title"),
            "email": email,
            "linkedin_url": person.get("linkedin_url"),
            "source": "prospeo",
        }
        if person.get("first_name"):
            signals["contact_first_name"] = person["first_name"]
        signals["email_source"] = "prospeo"
        # only_verified_email=True means this address was mailbox-verified.
        signals["email_confidence"] = "verified"
        for k in ("employee_count", "revenue_range", "industry"):
            v = person.get(k)
            if v not in (None, "", "Unknown"):
                signals[k] = v
        return {"email": email, "email_status": "valid", "decision_maker": dm}
    except Exception:
        return {}


# ─────────── Pattern-verify waterfall step (MillionVerifier) ───────────
def _dm_name_parts(lead: dict, llm_dm: dict | None) -> tuple[str, str, str, str | None]:
    """(first, last, full_name, title) of the known decision-maker, from the
    lead's stored decision_maker jsonb first, else this run's LLM extraction.
    ("", "", "", None) when no name is known."""
    for dm in (lead.get("decision_maker"), llm_dm):
        if not isinstance(dm, dict):
            continue
        name = dm.get("name")
        if not (isinstance(name, str) and name.strip()):
            continue
        parts = name.strip().split()
        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""
        title = dm.get("title") if isinstance(dm.get("title"), str) else None
        return first, last, name.strip(), title
    return "", "", "", None


def _pattern_verify_step(lead: dict, campaign: dict | None, signals: dict,
                         existing_result: dict | None = None,
                         llm_dm: dict | None = None) -> dict:
    """Pattern-guess + MillionVerifier step. Runs BEFORE Prospeo (cheaper:
    ~$0.0037/check vs a Prospeo credit). Gates (all must hold):

      - campaign search_config has decision_maker_mode: true (same gate as
        the Prospeo step),
      - a decision-maker NAME is known (lead.decision_maker jsonb, or the
        LLM extraction from this run via `llm_dm`),
      - the lead has a non-freemail company domain,
      - MILLIONVERIFIER_API_KEY is set and the email isn't already verified.

    If the free scrape already found a personal-looking address on the same
    domain (`existing_result`), it is verified first — an "ok" upgrades its
    confidence mx_only → verified without changing the address. Burn is
    capped by the per-tick budget set in api/cron/enrich_tick.py.

    On a verified hit: returns {email, email_status, decision_maker} and
    stamps email_source/email_confidence onto `signals` in place.
    Returns {} on miss/disabled/no-budget. Never raises.
    """
    try:
        cfg = _campaign_config(campaign)
        if not cfg.get("decision_maker_mode"):
            return {}
        if signals.get("email_confidence") == "verified":
            return {}  # already mailbox-verified — don't re-burn credits
        first, last, full_name, title = _dm_name_parts(lead, llm_dm)
        if not first:
            return {}
        domain = _lead_domain(lead)
        if not domain or domain in FREE_MAIL:
            return {}

        from lib import email_verify
        if not email_verify.enabled():
            return {}

        extra: list[str] = []
        existing = ((existing_result or {}).get("email")
                    or lead.get("email") or "").strip().lower()
        if "@" in existing and existing.split("@")[-1] == domain:
            from lib import email_finder
            local = existing.split("@")[0]
            if local not in email_finder.ROLE_PREFIX and \
               not any(local.startswith(p) for p in email_finder.ROLE_PREFIX):
                extra.append(existing)  # personal-looking scrape hit first

        hit = email_verify.find_email_by_pattern(
            first, last, domain, extra_candidates=extra)
        if not hit.get("email"):
            return {}

        signals["email_source"] = "pattern-verify"
        signals["email_confidence"] = "verified"   # MV said mailbox exists
        signals.setdefault("contact_first_name", first)
        dm = {"name": full_name, "title": title, "email": hit["email"],
              "source": "pattern-verify"}
        return {"email": hit["email"], "email_status": "valid",
                "decision_maker": dm}
    except Exception:
        return {}


# ─────────── Model extraction ───────────
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
    """Call the model for structured research. Returns {} on any failure."""
    if not llm.enabled():
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

    return llm.chat_json(system, user, max_tokens=700, tag=False) or {}


# ─────────── Normalisation ───────────
def _coerce_int_0_100(value) -> int | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


def _clean_signals(data: dict) -> dict:
    """Pull the signal-shaped fields out of the model's JSON into a flat dict."""
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
def enrich_lead(lead: dict, campaign: dict | None = None) -> dict:
    """Deep-research a single lead. Returns a PATCH dict for the leads row.

    Pure: no DB calls. The caller persists the returned patch. `campaign` is
    the lead's campaigns row (optional) — it gates the decision-maker
    waterfall (pattern-verify + Prospeo person steps).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    base_signals = dict(lead.get("signals") or {})

    # 1. Email hunt (free) — Places gives none, so scrape + web-search.
    email_patch: dict = {}
    if not lead.get("email"):
        try:
            from lib import email_finder
            found = email_finder.find_email(lead)
            if found.get("email"):
                email_patch = {"email": found["email"], "email_status": found.get("email_status")}
                base_signals["email_source"] = found.get("email_source")
                if found.get("email_confidence"):
                    base_signals["email_confidence"] = found["email_confidence"]
        except Exception:
            pass

    # 2. Site text + model extraction — runs BEFORE the paid steps because
    #    the LLM's decision_maker name feeds the pattern-verify candidates.
    site_text = _fetch_site_text(lead.get("website"))
    data = _extract(lead, site_text)
    llm_dm = _clean_decision_maker(data) if data else None

    # 3. Decision-maker waterfall — opt-in per campaign, cheapest first:
    #    pattern-verify (MillionVerifier, ~$0.0037/check), then Prospeo only
    #    if pattern-verify found nothing. A verified direct email beats
    #    whatever mx_only role address the scrape found.
    dm_patch = _pattern_verify_step(lead, campaign, base_signals,
                                    existing_result=email_patch, llm_dm=llm_dm)
    if not dm_patch.get("email"):
        dm_patch = _prospeo_person_step(lead, campaign, base_signals)
    if dm_patch.get("email"):
        email_patch = {"email": dm_patch["email"],
                       "email_status": dm_patch.get("email_status") or "valid"}

    # LLM skipped or failed → unblock the lead but don't fabricate intent.
    if not data:
        base_signals["enrich_note"] = "llm-skipped"
        return {
            "signals": base_signals,
            "intent_score": None,
            "decision_maker": dm_patch.get("decision_maker")
                              or {"name": None, "title": None, "email": None},
            "enrichment_status": "enriched",
            "enriched_at": now_iso,
            **email_patch,
        }

    merged = {**base_signals, **_clean_signals(data)}
    return {
        "signals": merged,
        "intent_score": _coerce_int_0_100(data.get("intent_score")),
        # A verified person (pattern-verify/Prospeo) outranks the LLM's guess.
        "decision_maker": dm_patch.get("decision_maker") or _clean_decision_maker(data),
        **email_patch,
        "enrichment_status": "enriched",
        "enriched_at": now_iso,
    }
