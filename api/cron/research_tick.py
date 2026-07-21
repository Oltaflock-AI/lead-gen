"""Research tick. Pre-caches fresh research (website excerpt + recent Google
reviews) onto engaged (opened) leads so the sequencer can personalize their next
email without scraping in the send path. Driven by GitHub Actions (every 15 min).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import meta_ads, research
from lib import ops
from lib.auth import is_cron_authorized


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = research.refresh_stale(); status = 200
            try:
                # Meta Ad Library intent signal for engaged leads. No-op until
                # LEADGEN_ADLIB_ENABLED=1; never fails the research tick.
                body["meta_ads"] = meta_ads.refresh_engaged_signals()
            except Exception as e:
                body["meta_ads"] = {"ok": False, "error": type(e).__name__}
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        ops.heartbeat("research_tick", "ok" if status == 200 else f"error: {str(body.get('error', ''))[:100]}", json.dumps(body, default=str)[:400])
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
