"""Phase 3 — enrichment tick. Driven by GitHub Actions / Vercel cron.

Each tick:
  1. Find up to ENRICH_BATCH leads with enrichment_status='pending'
     (oldest first).
  2. Deep-research each: enrich.enrich_lead(lead) → patch (website scrape +
     buying signals + intent score + decision maker via Claude).
  3. Persist the patch onto the lead row. sequencer_tick then auto-creates a
     sequence once enrichment_status flips to 'enriched'.

Stateless. Bounded per tick (ENRICH_BATCH) and resilient per lead — one bad
lead is marked 'failed' and the batch continues.
"""
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import enrich
from lib import supabase as sb
from lib.auth import is_cron_authorized

ENRICH_BATCH = int(os.environ.get("LEADGEN_ENRICH_BATCH", "10"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _pending(limit: int) -> list[dict]:
    return sb.select(
        "leads",
        {"select": "*", "enrichment_status": "eq.pending", "order": "created_at.asc"},
        limit=limit,
    )


def _process_one(lead: dict) -> dict:
    patch = enrich.enrich_lead(lead)
    sb.update("leads", {"id": lead["id"]}, patch)
    return {
        "lead_id": lead["id"],
        "intent_score": patch.get("intent_score"),
        "status": patch.get("enrichment_status"),
    }


def _run() -> dict:
    leads = _pending(ENRICH_BATCH)
    results: list[dict] = []
    for lead in leads:
        try:
            results.append(_process_one(lead))
        except Exception as e:
            # Don't let one bad lead kill the batch — mark it failed and move on.
            try:
                sb.update("leads", {"id": lead["id"]}, {
                    "enrichment_status": "failed",
                    "signals": {**(lead.get("signals") or {}), "enrich_error": str(e)[:200]},
                    "enriched_at": _iso(_now()),
                })
            except Exception:
                pass
            results.append({"lead_id": lead.get("id"), "intent_score": None, "status": "failed"})
    enriched = sum(1 for r in results if r.get("status") == "enriched")
    return {"ok": True, "enriched": enriched, "results": results}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = _run(); status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
