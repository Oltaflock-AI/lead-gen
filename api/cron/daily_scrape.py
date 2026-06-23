"""Daily scrape cron — Phase 2.

For every active campaign (or one specific campaign via ?campaign_id=N),
search Google Places, dedup, upsert into leads with enrichment_status='pending'.
Log a scrape_runs row per campaign with the count scraped.

Idempotent: leads (campaign_id, business) unique constraint dedups across days.
"""
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import scrape, supabase as sb
from lib.auth import is_cron_authorized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_campaigns(only_id: int | None) -> list[dict]:
    params = {"select": "*"}
    if only_id is not None:
        params["id"] = f"eq.{only_id}"
    else:
        params["active"] = "eq.true"
    return sb.select("campaigns", params, limit=200)


def _start_run(campaign_id: int, target: int) -> int:
    rows = sb.insert("scrape_runs", {
        "campaign_id": campaign_id,
        "target_count": target,
        "status": "running",
    })
    return rows[0]["id"]


def _finish_run(run_id: int, scraped: int, status: str = "completed", err: str | None = None) -> None:
    sb.update("scrape_runs", {"id": run_id}, {
        "status": status,
        "error": err,
        "scraped_count": scraped,
        "finished_at": _now(),
    })


def _upsert_leads(rows: list[dict]) -> int:
    if not rows:
        return 0
    # on_conflict on (campaign_id, business) — schema has UNIQUE constraint
    out = sb.insert("leads", rows, on_conflict="campaign_id,business")
    return len(out)


def _is_csv_campaign(campaign: dict) -> bool:
    """CSV-uploaded lists carry their own leads and must never be scraped."""
    try:
        return json.loads(campaign.get("notes") or "{}").get("source") == "csv"
    except Exception:
        return False


def _scrape_campaign(campaign: dict) -> dict:
    if _is_csv_campaign(campaign):
        return {"campaign": campaign["name"], "skipped": "csv-upload"}
    target = campaign.get("daily_scrape_target") or 50
    run_id = _start_run(campaign["id"], target)
    try:
        rows = list(scrape.scrape_for_campaign(campaign, target=target))
        n = _upsert_leads(rows)
        _finish_run(run_id, n, "completed")
        return {"campaign": campaign["name"], "scraped": n, "target": target, "run_id": run_id}
    except Exception as e:
        _finish_run(run_id, 0, "failed", str(e)[:500])
        return {"campaign": campaign["name"], "error": str(e), "run_id": run_id}


def _run(only_id: int | None) -> dict:
    camps = _active_campaigns(only_id)
    if not camps:
        return {"ok": True, "campaigns": 0, "results": []}
    results = [_scrape_campaign(c) for c in camps]
    return {"ok": True, "campaigns": len(camps), "results": results}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return

        qs = parse_qs(urlparse(self.path).query)
        only_id = None
        if "campaign_id" in qs:
            try:
                only_id = int(qs["campaign_id"][0])
            except ValueError:
                pass

        try:
            body = _run(only_id)
            status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
