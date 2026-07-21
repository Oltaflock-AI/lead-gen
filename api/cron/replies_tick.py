"""Reply-poller tick. Driven by GitHub Actions (Vercel Hobby crons are daily).

Polls the Gmail inbox for replies from contacted leads, logs a 'replied' event,
and pauses the sequence. Idempotent and bounded by the Gmail query window.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import gmail_replies
from lib import ops
from lib.auth import is_cron_authorized


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401); self.end_headers(); return
        try:
            body = gmail_replies.check_and_log_replies(); status = 200
        except Exception as e:
            body = {"ok": False, "error": str(e)}; status = 500
        ops.heartbeat("replies_tick", "ok" if status == 200 else f"error: {str(body.get('error', ''))[:100]}", json.dumps(body, default=str)[:400])
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
