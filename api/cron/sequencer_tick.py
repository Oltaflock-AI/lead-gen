"""Sequencer tick cron — STUB. Phase 4 work.

Will find sequences where next_send_at <= now() AND status='active', draft
the next step (Claude personalized using lead.signals), send via Resend with
sequence_id/step tags, insert 'sent' event, bump current_step + next_send_at.
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
        self.wfile.write(json.dumps({"ok": True, "status": "stub", "phase": 4}).encode())
