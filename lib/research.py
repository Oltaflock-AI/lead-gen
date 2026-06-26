"""Phase 3 — real-time fresh research for engaged (warm/hot) leads.

Once a lead opens or clicks, we want the NEXT email to be visibly about them,
right now. This module scrapes the lead's website homepage and pulls their most
recent Google reviews, distills both into a compact digest, and caches it on the
lead row (leads.research / leads.researched_at).

Run by api/cron/research_tick, NOT in the send path — the send tick only reads
the cached digest, so scraping latency never touches outbound timing. A TTL keeps
the digest current as fresh reviews land.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from lib import enrich       # reuse the proven site fetch + html strip
from lib import scrape       # reuse the Google Places api key
from lib import supabase as sb

PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

TTL_DAYS        = int(os.environ.get("LEADGEN_RESEARCH_TTL_DAYS", "14"))
# Bound network work per run. Each lead does a site GET + a reviews GET + an
# update; keep PER_TICK low and also hard-stop on a wall-clock deadline so the
# function never approaches the 300s maxDuration.
PER_TICK        = int(os.environ.get("LEADGEN_RESEARCH_PER_TICK", "5"))
DEADLINE_S      = int(os.environ.get("LEADGEN_RESEARCH_DEADLINE_S", "240"))
MAX_REVIEWS     = int(os.environ.get("LEADGEN_RESEARCH_MAX_REVIEWS", "4"))
SITE_EXCERPT    = int(os.environ.get("LEADGEN_RESEARCH_SITE_CHARS", "1500"))


# ─────────── Fetchers (best-effort, never raise) ───────────
def fetch_reviews(place_id: str | None) -> list[dict]:
    """Latest Google reviews for a place: [{rating, text, when}]. Best-effort."""
    if not place_id:
        return []
    pid = place_id.split("/")[-1]  # accept "ChIJ..." or "places/ChIJ..."
    try:
        key = scrape._api_key()
    except Exception:
        return []
    try:
        r = requests.get(
            PLACE_DETAILS_URL.format(place_id=pid),
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "reviews"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        reviews = r.json().get("reviews") or []
    except Exception:
        return []
    out = []
    for rv in reviews[:MAX_REVIEWS]:
        text = ((rv.get("text") or {}).get("text") or (rv.get("originalText") or {}).get("text") or "").strip()
        if not text:
            continue
        out.append({
            "rating": rv.get("rating"),
            "when": rv.get("relativePublishTimeDescription"),
            "text": text[:400],
        })
    return out


def build_research(lead: dict) -> dict:
    """Scrape website + reviews into a compact digest for the draft prompt."""
    site = enrich._fetch_site_text(lead.get("website"))
    place_id = (lead.get("signals") or {}).get("place_id")
    reviews = fetch_reviews(place_id)
    return {
        "site_excerpt": (site or "")[:SITE_EXCERPT],
        "reviews": reviews,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────── Render (consumed by sequence.draft_one) ───────────
def research_block(research: dict | None) -> str:
    """Human-readable block injected into the draft prompt. Empty if no signal."""
    if not research:
        return ""
    parts = []
    site = (research.get("site_excerpt") or "").strip()
    if site:
        parts.append("WEBSITE (their own words, current):\n" + site)
    reviews = research.get("reviews") or []
    if reviews:
        lines = []
        for rv in reviews:
            rating = rv.get("rating")
            when = rv.get("when")
            tag = " ".join(x for x in [f"{rating}*" if rating else "", f"({when})" if when else ""] if x).strip()
            lines.append(f"- {tag}: {rv.get('text')}".strip())
        parts.append("RECENT GOOGLE REVIEWS (what their customers are saying now):\n" + "\n".join(lines))
    return "\n\n".join(parts)


# ─────────── Refresh worker (the research tick) ───────────
def _is_stale(lead: dict) -> bool:
    if not lead.get("research") or not lead.get("researched_at"):
        return True
    try:
        ts = datetime.fromisoformat(lead["researched_at"].replace("Z", "+00:00"))
    except Exception:
        return True
    return (datetime.now(timezone.utc) - ts) > timedelta(days=TTL_DAYS)


def refresh_stale() -> dict:
    """Find engaged leads (opens>0) whose research is missing/stale and refresh a
    bounded batch. Idempotent; safe to run on a short interval."""
    seqs = sb.select(
        "sequences",
        {"select": "lead_id", "status": "eq.active", "opens": "gt.0", "order": "updated_at.desc"},
        limit=2000,
    )
    lead_ids = list({s["lead_id"] for s in seqs if s.get("lead_id")})
    if not lead_ids:
        return {"ok": True, "engaged": 0, "refreshed": 0}

    start = time.monotonic()
    refreshed, scanned, errors = 0, 0, 0
    # Chunk the id filter so the URL stays sane; stop on the per-tick count budget
    # or the wall-clock deadline, whichever comes first.
    for i in range(0, len(lead_ids), 200):
        chunk = lead_ids[i:i + 200]
        ids = ",".join(str(x) for x in chunk)
        leads = sb.select(
            "leads",
            {"select": "id,website,signals,research,researched_at", "id": f"in.({ids})"},
            limit=200,
        )
        for lead in leads:
            scanned += 1
            if not _is_stale(lead):
                continue
            if not lead.get("website") and not (lead.get("signals") or {}).get("place_id"):
                continue  # nothing to research
            # Fail-open per lead: one bad fetch/store must not abort the batch.
            try:
                digest = build_research(lead)
                sb.update("leads", {"id": lead["id"]},
                          {"research": digest, "researched_at": digest["fetched_at"]})
                refreshed += 1
            except Exception:
                errors += 1
            if refreshed >= PER_TICK or (time.monotonic() - start) > DEADLINE_S:
                return {"ok": True, "engaged": len(lead_ids), "scanned": scanned,
                        "refreshed": refreshed, "errors": errors,
                        "stopped": "budget" if refreshed >= PER_TICK else "deadline"}
    return {"ok": True, "engaged": len(lead_ids), "scanned": scanned,
            "refreshed": refreshed, "errors": errors, "stopped": "drained"}
