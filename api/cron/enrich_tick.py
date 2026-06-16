"""Enrichment tick cron — STUB. Phase 3 work.

Will pick up to N leads with enrichment_status='pending', deep-research each
(website scrape + signals + intent score via Claude), update lead row, then
create a sequences row in 'pending' so sequencer_tick takes it from there.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib.auth import is_cron_authorized


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "status": "stub", "phase": 3}).encode())
