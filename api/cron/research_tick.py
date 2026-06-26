"""Research tick. Pre-caches fresh research (website excerpt + recent Google
reviews) onto engaged (opened) leads so the sequencer can personalize their next
email without scraping in the send path. Driven by GitHub Actions (every 15 min).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import research
from lib.auth import is_cron_authorized


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = research.refresh_stale(); status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
