"""Agent tool registry — the natural-language chat's hands.

Exposes the existing scrape / enrich / qualify / schedule machinery as
OpenAI function-calling tools. Each tool runs the SAME per-lead waterfall the
dashboard uses, but orchestrated end-to-end and streamed sub-step by sub-step
so the chat shows a Clay/Artisan-style live log.

Contract (consumed by lib.llm.chat_tools):
    build_agent_tools()  ->  (tools, tool_impls)
        tools       : list[openai tool schema]
        tool_impls  : dict name -> callable(args: dict, emit) -> json-able result
    emit(event: dict)    : stream a progress event to the chat UI. Types used:
        {"type":"tool_progress","name":<tool>,"line":<str>}

Everything is best-effort per stage: a failing stage logs, emits, and the
waterfall continues — never aborts the whole lead.
"""
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data" / "outputs"

# Hard ceiling so a single chat turn can't burn unbounded API $ / wall-clock.
MAX_LEADS_PER_RUN = 60


# ───────────────────────── helpers ─────────────────────────
def _emit(emit, name, line):
    """Safe progress emit."""
    if emit:
        try:
            emit({"type": "tool_progress", "name": name, "line": line})
        except Exception:
            pass


def _now_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _latest_agent_csv():
    """Most-recent agent_*.csv in data/outputs — for 'schedule those leads'."""
    if not OUTPUT_DIR.exists():
        return None
    cands = sorted(OUTPUT_DIR.glob("agent_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0].name if cands else None


def _resolve_csv(csv_name):
    """Return an absolute Path for a csv name, tolerating bare names."""
    if not csv_name:
        name = _latest_agent_csv()
        if not name:
            return None
        return OUTPUT_DIR / name
    p = Path(csv_name)
    if p.is_absolute() and p.exists():
        return p
    cand = OUTPUT_DIR / Path(csv_name).name
    return cand if cand.exists() else None


# ───────────────────────── per-lead waterfall ─────────────────────────
def _waterfall_one(place, niche, region, country, emit):
    """Run the full enrichment cascade on ONE Google Places hit.

    Stages: email discovery -> company research (site crawl + LLM extract) ->
    persona/brief match -> qualify (AI-fit + email quality). Returns a flat
    lead row dict ready for CSV + Supabase, or None if unusable.
    """
    from lib import scrape as _scrape

    row = _scrape.normalize(place, campaign_id=0)
    if not row:
        return None
    name = row["business"]
    sig = row.get("signals") or {}

    _emit(emit, "scrape_and_enrich_leads", f"🔎 Researching {name}")

    # Build the canonical lead dict the downstream primitives expect.
    lead = {
        "business": name,
        "business_name": name,
        "website": row.get("website"),
        "phone": row.get("phone"),
        "address": row.get("address"),
        "city": region,
        "country": country,
        "niche": niche,
        "email": "",
        "signals": {
            "rating": sig.get("rating"),
            "user_rating_count": sig.get("user_rating_count"),
            "place_id": sig.get("place_id"),
            "business_type": (sig.get("types") or [None])[0],
        },
    }

    # 1) EMAIL WATERFALL — site discovery -> site emails -> snippets -> directories.
    try:
        from . import email_finder
        found = email_finder.deep_enrich_lead(name, region, country)
        if found.get("website") and not lead["website"]:
            lead["website"] = found["website"]
        if found.get("email"):
            lead["email"] = found["email"]
            _emit(emit, "scrape_and_enrich_leads",
                  f"  ✉️  Email: {found['email']} (via {found.get('email_source') or 'web'})")
        else:
            _emit(emit, "scrape_and_enrich_leads", "  ✉️  No email found — kept for review")
    except Exception as e:
        log.warning("email waterfall failed for %s: %s", name, e)
        _emit(emit, "scrape_and_enrich_leads", "  ✉️  Email step skipped (error)")

    # 2) COMPANY RESEARCH — crawl site + LLM extract (description, signals, DM).
    research = {}
    try:
        from lib import enrich as _enrich
        patch = _enrich.enrich_lead(lead)
        research = patch.get("signals") or {}
        if patch.get("email") and not lead["email"]:
            lead["email"] = patch["email"]
        lead["intent_score"] = patch.get("intent_score")
        lead["decision_maker"] = patch.get("decision_maker") or {}
        n_sig = len(research.get("buying_signals") or [])
        summ = (research.get("summary") or "").strip()
        _emit(emit, "scrape_and_enrich_leads",
              f"  🏢 Researched site — {n_sig} buying signals"
              + (f'; "{summ[:70]}"' if summ else ""))
    except Exception as e:
        log.warning("research failed for %s: %s", name, e)
        _emit(emit, "scrape_and_enrich_leads", "  🏢 Research step skipped (error)")

    # 3) PERSONA / NICHE BRIEF.
    try:
        from . import niche_briefs
        brief = niche_briefs.get_brief_for_lead(lead, niche)
        if brief:
            _emit(emit, "scrape_and_enrich_leads", f"  🎭 Persona/brief: {brief.get('label')}")
    except Exception as e:
        log.debug("brief match failed for %s: %s", name, e)

    # 4) QUALIFY — AI-fit score + email quality.
    fit_score = None
    try:
        from . import fitcheck
        fit = fitcheck.compute_fit(name, region, country)
        fit_score = fit.get("ai_fit_score")
        row["_fit"] = fit
    except Exception as e:
        log.debug("fit check failed for %s: %s", name, e)

    email_status = ""
    if lead.get("email"):
        try:
            from . import email_quality
            dom = (lead.get("website") or "").split("//")[-1].split("/")[0]
            q = email_quality.score_email(lead["email"], name, dom)
            email_status = q.get("status") or q.get("verdict") or ""
        except Exception as e:
            log.debug("email quality failed for %s: %s", name, e)
    _emit(emit, "scrape_and_enrich_leads",
          f"  ✅ Qualified — fit {fit_score if fit_score is not None else 'n/a'}/100"
          + (f", email {email_status}" if email_status else ""))

    # Flatten into a CSV/Supabase row.
    return {
        "business_name": name,
        "email": lead.get("email", ""),
        "email_status": email_status,
        "website": lead.get("website") or "",
        "phone": lead.get("phone") or "",
        "address": lead.get("address") or "",
        "city": region,
        "country": country,
        "niche": niche,
        "rating": sig.get("rating"),
        "review_count": sig.get("user_rating_count"),
        "place_id": sig.get("place_id") or "",
        "ai_niche_fit": fit_score,
        "ai_lead_score": lead.get("intent_score"),
        "ai_score_reason": (research.get("summary") or "")[:300],
        "buying_signals": " | ".join(research.get("buying_signals") or []),
        "pain": research.get("pain") or "",
        "revenue_band": research.get("revenue_band") or "",
        "decision_maker": ((lead.get("decision_maker") or {}).get("name") or ""),
    }


# ───────────────────────── tool: scrape_and_enrich ─────────────────────────
def _tool_scrape_and_enrich(args, emit):
    from lib import scrape as _scrape

    niche = (args.get("niche") or "leads").strip()
    region = (args.get("region") or args.get("state") or "").strip()
    city = (args.get("city") or "").strip()
    count = min(int(args.get("count") or 25), MAX_LEADS_PER_RUN)
    min_rating = float(args.get("min_rating") or 0)
    min_reviews = int(args.get("min_reviews") or 0)
    website_filter = args.get("website_filter") or "any"  # any | has | none
    business_types = [t for t in (args.get("business_types") or []) if t] or [niche]

    if not region:
        return {"error": "region/state is required (e.g. 'Pennsylvania')"}
    if not os.environ.get("GOOGLE_PLACES_API_KEY"):
        return {"error": "GOOGLE_PLACES_API_KEY not configured"}

    country = args.get("country") or "US"
    region_code = _scrape._region_code(country) or _scrape._region_code(region) or "US"
    geo = f"{city}, {region}" if city else region

    _emit(emit, "scrape_and_enrich_leads",
          f"🚀 Scraping up to {count} {niche} across {geo} "
          f"({', '.join(business_types)})")

    # ── Discover candidates ──
    seen = set()
    candidates = []
    for bt in business_types:
        if len(candidates) >= count * 2:
            break
        q = f"{bt} in {geo}"
        try:
            places = _scrape.search_places(q, max_pages=3, region_code=region_code)
        except Exception as e:
            _emit(emit, "scrape_and_enrich_leads", f"  ⚠️ search '{q}' failed: {e}")
            continue
        for p in places:
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            if not _scrape._passes_filters(p, min_rating, min_reviews, website_filter):
                continue
            seen.add(pid)
            candidates.append(p)
    _emit(emit, "scrape_and_enrich_leads",
          f"📇 {len(candidates)} candidates found — enriching top {min(count, len(candidates))}")

    # ── Per-lead waterfall ──
    rows = []
    for i, p in enumerate(candidates[:count], 1):
        _emit(emit, "scrape_and_enrich_leads", f"── Lead {i}/{min(count, len(candidates))} ──")
        r = _waterfall_one(p, niche, region, country, emit)
        if r:
            rows.append(r)

    if not rows:
        return {"count": 0, "message": "no usable leads after enrichment"}

    # ── Persist: CSV + Supabase ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_name = f"agent_{niche.replace(' ', '_')}_{_now_stamp()}.csv"
    csv_path = OUTPUT_DIR / csv_name
    fields = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    upserted = 0
    try:
        from . import supabase_leads
        upserted = supabase_leads.upsert_leads(rows, source_csv=csv_name)
    except Exception as e:
        log.warning("supabase upsert failed: %s", e)

    with_email = sum(1 for r in rows if r.get("email"))
    _emit(emit, "scrape_and_enrich_leads",
          f"💾 Saved {len(rows)} leads → {csv_name} "
          f"({with_email} with email, {upserted} synced to Supabase)")

    sample = [
        {"business_name": r["business_name"], "email": r["email"],
         "fit": r.get("ai_niche_fit"), "intent": r.get("ai_lead_score")}
        for r in rows[:5]
    ]
    return {
        "count": len(rows),
        "with_email": with_email,
        "csv_name": csv_name,
        "supabase_synced": upserted,
        "sample": sample,
    }


# ───────────────────────── tool: list_leads ─────────────────────────
def _tool_list_leads(args, emit):
    path = _resolve_csv(args.get("csv_name"))
    if not path:
        return {"error": "no matching leads file found"}
    limit = min(int(args.get("limit") or 50), 200)
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "business_name": r.get("business_name", ""),
                "email": r.get("email", ""),
                "city": r.get("city", ""),
                "niche": r.get("niche", ""),
                "fit": r.get("ai_niche_fit", ""),
            })
    with_email = sum(1 for r in rows if r["email"])
    _emit(emit, "list_leads", f"📄 {path.name}: {len(rows)} leads, {with_email} with email")
    return {"csv_name": path.name, "total": len(rows),
            "with_email": with_email, "leads": rows[:limit]}


# ───────────────────────── tool: schedule_leads ─────────────────────────
def _tool_schedule_leads(args, emit):
    """Enrol leads into the 6-step sequence (drafts + schedules step 1).

    SAFETY: this only ENQUEUES — it never sends synchronously. Actual sending
    stays behind the cron scheduler (paused-by-default). Requires confirm=True.
    """
    path = _resolve_csv(args.get("csv_name"))
    if not path:
        return {"error": "no matching leads file found — scrape first"}

    niche = (args.get("niche") or "").strip()
    sender_name = (args.get("sender_name") or "").strip()
    confirm = bool(args.get("confirm"))

    leads = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("email") or "").strip():
                leads.append(r)

    if not leads:
        return {"error": f"{path.name} has no leads with an email to schedule"}

    if not confirm:
        return {
            "requires_confirmation": True,
            "csv_name": path.name,
            "schedulable": len(leads),
            "message": (f"{len(leads)} leads in {path.name} have emails and are ready to "
                        "enrol into the 6-step sequence (step 1 scheduled, nothing sends "
                        "until the scheduler runs). Re-call with confirm=true to proceed."),
        }

    from . import sequencer
    enrolled, skipped = 0, []
    for r in leads:
        lead = dict(r)
        lead["business_name"] = r.get("business_name", "")
        lead["niche"] = niche or r.get("niche", "")
        try:
            sid, status = sequencer.enqueue_lead(lead, csv_name=path.name, sender_name=sender_name)
            if sid and status in ("queued",):
                enrolled += 1
                _emit(emit, "schedule_leads", f"  📅 Enrolled {lead['business_name']} ({status})")
            else:
                skipped.append({"business": lead["business_name"], "reason": status})
                _emit(emit, "schedule_leads", f"  ⏭️ {lead['business_name']}: {status}")
        except Exception as e:
            skipped.append({"business": lead.get("business_name", ""), "reason": str(e)})
            log.warning("enqueue failed: %s", e)

    _emit(emit, "schedule_leads",
          f"✅ Scheduled {enrolled} leads (step 1 queued, sending waits for the scheduler)")
    return {"enrolled": enrolled, "skipped": len(skipped),
            "skipped_detail": skipped[:10], "csv_name": path.name}


# ───────────────────────── registry ─────────────────────────
def build_agent_tools():
    """Return (tools, tool_impls) for lib.llm.chat_tools."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "scrape_and_enrich_leads",
                "description": (
                    "Scrape local businesses from Google Places and run the FULL "
                    "per-lead enrichment waterfall on each: email discovery (site "
                    "-> snippets -> directories), company research (crawl website + "
                    "extract description, buying signals, decision maker), persona/"
                    "niche match, and qualification (AI-fit score + email quality). "
                    "Saves a CSV and syncs to Supabase. Use for requests like "
                    "'scrape 50 real estate leads across Pennsylvania'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "niche": {"type": "string", "description": "e.g. 'real estate', 'travel agency'"},
                        "region": {"type": "string", "description": "State/region/country, e.g. 'Pennsylvania'"},
                        "city": {"type": "string", "description": "Optional city to narrow the search"},
                        "business_types": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Specific Google-Places search terms, e.g. ['real estate agency','realtor']. Infer sensible ones from the niche.",
                        },
                        "count": {"type": "integer", "description": f"How many enriched leads to return (max {MAX_LEADS_PER_RUN})"},
                        "min_rating": {"type": "number", "description": "Minimum Google rating filter, e.g. 4.0"},
                        "min_reviews": {"type": "integer", "description": "Minimum review count filter"},
                        "website_filter": {"type": "string", "enum": ["any", "has", "none"],
                                            "description": "'none' = only businesses WITHOUT a website"},
                        "country": {"type": "string", "description": "Country name for region-coding, default US"},
                    },
                    "required": ["niche", "region", "count"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_leads",
                "description": "List leads from the most recent (or a named) scraped CSV, so you can review before scheduling.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "csv_name": {"type": "string", "description": "Optional; defaults to the most recent scrape."},
                        "limit": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "schedule_leads",
                "description": (
                    "Enrol scraped+enriched leads into the 6-step email sequence "
                    "(drafts all steps, schedules step 1). It does NOT send anything "
                    "immediately — sending stays behind the paused-by-default "
                    "scheduler. ALWAYS call once with confirm=false to preview the "
                    "count, then again with confirm=true after the user agrees."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "csv_name": {"type": "string", "description": "Optional; defaults to the most recent scrape."},
                        "niche": {"type": "string", "description": "Niche whose offer/brief to use for drafting."},
                        "sender_name": {"type": "string"},
                        "confirm": {"type": "boolean", "description": "Must be true to actually enrol."},
                    },
                },
            },
        },
    ]
    tool_impls = {
        "scrape_and_enrich_leads": _tool_scrape_and_enrich,
        "list_leads": _tool_list_leads,
        "schedule_leads": _tool_schedule_leads,
    }
    return tools, tool_impls


AGENT_SYSTEM_PROMPT = (
    "You are the OltaFlock lead-gen operator agent. You help the user scrape, "
    "enrich, and schedule outbound leads through natural language. You have "
    "tools that scrape Google Places and run a full per-lead enrichment "
    "waterfall, list scraped leads, and enrol leads into the email sequence.\n\n"
    "Rules:\n"
    "- When the user asks to scrape, infer sensible business_types from the "
    "niche and call scrape_and_enrich_leads. Default count to what they say "
    "(cap 60).\n"
    "- 'those leads' / 'them' refers to the most recent scrape — omit csv_name "
    "to use it.\n"
    "- NEVER send email directly. Scheduling only enrols into the paused "
    "sequence. Before scheduling, ALWAYS preview (confirm=false) and get the "
    "user's explicit yes before calling with confirm=true.\n"
    "- After each tool call, briefly summarise what happened in plain language "
    "(counts, csv name, how many have emails, fit scores).\n"
    "- Be concise. Report real numbers from tool results, never invent leads."
)
