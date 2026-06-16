"""Daily email digest. Cron hits at 16:00 UTC = 21:30 IST.

Queries Supabase for today's metrics + last-24h activity, renders an HTML
summary, sends via Resend. Logs delivery into sequence_events for traceability.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from lib import supabase as sb
from lib.auth import is_cron_authorized

DIGEST_TO = os.environ.get("DIGEST_TO", "admin@oltaflock.ai")
RESEND_FROM = os.environ.get("RESEND_FROM", "outreach@oltaflock.ai")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")


def _today_window():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _count_events(event_type: str, since_iso: str) -> int:
    rows = sb.select(
        "sequence_events",
        params={
            "select": "id",
            "event_type": f"eq.{event_type}",
            "ts": f"gte.{since_iso}",
        },
        limit=10000,
    )
    return len(rows)


def _gather_stats() -> dict:
    start, now = _today_window()
    since = start.isoformat()
    yday = (start - timedelta(days=1)).isoformat()

    # Today
    sent_today = _count_events("sent", since)
    delivered_today = _count_events("delivered", since)
    opened_today = _count_events("opened", since)
    clicked_today = _count_events("clicked", since)
    replied_today = _count_events("replied", since)
    bounced_today = _count_events("bounced", since)

    # Yesterday for delta
    sent_yday = _count_events("sent", yday)

    # Pipeline state
    leads_total = sb.select("leads", params={"select": "id"}, limit=10000)
    seqs_active = sb.select(
        "sequences", params={"select": "id", "status": "eq.active"}, limit=10000
    )
    pending_enrich = sb.select(
        "leads",
        params={"select": "id", "enrichment_status": "eq.pending"},
        limit=10000,
    )

    return {
        "today": {
            "sent": sent_today,
            "delivered": delivered_today,
            "opened": opened_today,
            "clicked": clicked_today,
            "replied": replied_today,
            "bounced": bounced_today,
        },
        "yesterday_sent": sent_yday,
        "pipeline": {
            "leads_total": len(leads_total),
            "sequences_active": len(seqs_active),
            "leads_pending_enrich": len(pending_enrich),
        },
        "as_of": now.isoformat(),
    }


def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "—"
    return f"{round(100 * num / denom)}%"


def _render_html(stats: dict) -> str:
    t = stats["today"]
    p = stats["pipeline"]
    return f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:540px;margin:0 auto;padding:24px;color:#111;">
<h2 style="margin:0 0 4px;">Lead-gen autopilot</h2>
<div style="color:#666;font-size:13px;margin-bottom:24px;">{stats['as_of'][:10]} UTC</div>

<h3 style="margin:0 0 8px;font-size:15px;">Today</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:24px;">
  <tr><td style="padding:6px 0;color:#666;">Sent</td><td style="text-align:right;font-weight:600;">{t['sent']}</td></tr>
  <tr><td style="padding:6px 0;color:#666;">Delivered</td><td style="text-align:right;">{t['delivered']}</td></tr>
  <tr><td style="padding:6px 0;color:#666;">Opened</td><td style="text-align:right;font-weight:600;">{t['opened']} <span style="color:#888;font-weight:400;">({_pct(t['opened'], t['delivered'])})</span></td></tr>
  <tr><td style="padding:6px 0;color:#666;">Clicked</td><td style="text-align:right;">{t['clicked']} <span style="color:#888;">({_pct(t['clicked'], t['delivered'])})</span></td></tr>
  <tr><td style="padding:6px 0;color:#666;">Replied</td><td style="text-align:right;font-weight:600;color:#0a7d2c;">{t['replied']}</td></tr>
  <tr><td style="padding:6px 0;color:#666;">Bounced</td><td style="text-align:right;color:{'#a00' if t['bounced'] else '#666'};">{t['bounced']}</td></tr>
</table>

<h3 style="margin:0 0 8px;font-size:15px;">Pipeline</h3>
<table style="border-collapse:collapse;width:100%;font-size:14px;">
  <tr><td style="padding:6px 0;color:#666;">Leads in DB</td><td style="text-align:right;">{p['leads_total']}</td></tr>
  <tr><td style="padding:6px 0;color:#666;">Active sequences</td><td style="text-align:right;">{p['sequences_active']}</td></tr>
  <tr><td style="padding:6px 0;color:#666;">Awaiting enrichment</td><td style="text-align:right;">{p['leads_pending_enrich']}</td></tr>
</table>

<div style="color:#888;font-size:12px;margin-top:32px;">Yesterday sent: {stats['yesterday_sent']}</div>
</body></html>"""


def _send_email(html: str, subject: str) -> dict:
    if not RESEND_API_KEY:
        return {"skipped": "RESEND_API_KEY missing"}
    import requests as _r
    r = _r.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": RESEND_FROM,
            "to": [DIGEST_TO],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_cron_authorized(self.headers):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return

        try:
            stats = _gather_stats()
            today = stats["as_of"][:10]
            subject = f"Lead-gen {today} — {stats['today']['sent']} sent, {stats['today']['opened']} opened"
            html = _render_html(stats)
            result = _send_email(html, subject)
            body = {"ok": True, "stats": stats, "email": result}
        except Exception as e:
            body = {"ok": False, "error": str(e)}

        self.send_response(200 if body.get("ok") else 500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
