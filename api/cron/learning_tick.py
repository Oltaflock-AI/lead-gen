"""Learning tick. Recomputes subject-angle performance stats from sequence_events
so the sequencer can bias toward winning angles. Driven by GitHub Actions
(nightly is plenty).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import learning
from lib.auth import is_cron_authorized


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = learning.recompute_angle_performance(); status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
