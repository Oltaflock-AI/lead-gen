"""Lead-gen dashboard — Vercel Flask app.

Single-file control panel for the autopilot:
  GET  /              campaigns + global stats
  POST /campaigns     create a campaign
  POST /campaigns/<id>/toggle     active/inactive
  POST /campaigns/<id>/scrape     manually trigger scrape now
  GET  /leads         recent leads
  GET  /sequences     active sequences
  GET  /events        recent events
  GET  /healthz       liveness

Reads Supabase via REST. No SQLite. No threads. Stateless across invocations.
"""
import os
import sys
from datetime import datetime, timezone

from flask import Flask, render_template_string, request, redirect, url_for

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib import supabase as sb

app = Flask(__name__)
ADMIN_KEY = os.environ.get("DASHBOARD_KEY", "")


# ───── Auth gate (optional, set DASHBOARD_KEY env to enable) ─────
@app.before_request
def _gate():
    if not ADMIN_KEY:
        return None
    if request.path == "/healthz":
        return None
    if request.cookies.get("dk") == ADMIN_KEY:
        return None
    if request.args.get("dk") == ADMIN_KEY:
        resp = redirect(request.path)
        resp.set_cookie("dk", ADMIN_KEY, max_age=86400 * 30, httponly=True, samesite="Lax")
        return resp
    return ("Set ?dk=<DASHBOARD_KEY> in URL", 401)


# ───── Helpers ─────
def _stats_24h() -> dict:
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    def n(t): return len(sb.select("sequence_events", {"select": "id", "event_type": f"eq.{t}", "ts": f"gte.{since}"}, limit=10000))
    return {"sent": n("sent"), "opened": n("opened"), "clicked": n("clicked"), "replied": n("replied"), "bounced": n("bounced")}


def _campaigns() -> list[dict]:
    return sb.select("campaigns", {"select": "*", "order": "created_at.desc"}, limit=200)


def _layout(body: str, title: str = "Lead-gen") -> str:
    nav = """
      <nav><ul>
        <li><strong>Lead-gen autopilot</strong></li>
      </ul><ul>
        <li><a href="/">Campaigns</a></li>
        <li><a href="/leads">Leads</a></li>
        <li><a href="/sequences">Sequences</a></li>
        <li><a href="/events">Events</a></li>
      </ul></nav>
    """
    return f"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
  :root {{ --pico-font-size: 15px; }}
  main {{ padding: 1rem; max-width: 1100px; }}
  .stat {{ display: inline-block; min-width: 130px; padding: 12px 16px; margin: 4px 8px 4px 0; border: 1px solid var(--pico-muted-border-color); border-radius: 8px; }}
  .stat b {{ display: block; font-size: 22px; }}
  .stat span {{ color: var(--pico-secondary); font-size: 12px; text-transform: uppercase; }}
  table {{ font-size: 13px; }}
  td, th {{ padding: 6px 10px; }}
  .pill {{ padding: 2px 8px; border-radius: 12px; font-size: 11px; background: var(--pico-muted-border-color); }}
  .pill-active {{ background: #d4edda; color: #155724; }}
  .pill-paused {{ background: #fff3cd; color: #856404; }}
  .pill-pending {{ background: #cce5ff; color: #004085; }}
  .pill-bounced {{ background: #f8d7da; color: #721c24; }}
  form.inline {{ display: inline; margin: 0; }}
  button.tiny {{ padding: 4px 10px; font-size: 12px; margin: 0 2px; width: auto; }}
</style></head><body><main>{nav}{body}</main></body></html>"""


# ───── Routes ─────
@app.route("/healthz")
def healthz():
    return {"ok": True, "service": "lead-gen-dashboard"}


@app.route("/")
def home():
    stats = _stats_24h()
    camps = _campaigns()
    rows = "".join(
        f"""<tr>
          <td><strong>{c['name']}</strong><br><small>{c['niche']} · {c['region']}</small></td>
          <td>{c['daily_scrape_target']}</td>
          <td><span class="pill {'pill-active' if c['active'] else 'pill-paused'}">{'active' if c['active'] else 'paused'}</span></td>
          <td>{c['created_at'][:10]}</td>
          <td>
            <form class="inline" method="post" action="/campaigns/{c['id']}/toggle"><button class="tiny secondary">{'Pause' if c['active'] else 'Resume'}</button></form>
            <form class="inline" method="post" action="/campaigns/{c['id']}/scrape"><button class="tiny">Scrape now</button></form>
          </td>
        </tr>""" for c in camps
    ) or '<tr><td colspan="5"><em>No campaigns yet — add one below.</em></td></tr>'

    body = f"""
      <h2>Last 24h</h2>
      <div>
        <div class="stat"><b>{stats['sent']}</b><span>Sent</span></div>
        <div class="stat"><b>{stats['opened']}</b><span>Opened</span></div>
        <div class="stat"><b>{stats['clicked']}</b><span>Clicked</span></div>
        <div class="stat"><b>{stats['replied']}</b><span>Replied</span></div>
        <div class="stat"><b>{stats['bounced']}</b><span>Bounced</span></div>
      </div>

      <h2 style="margin-top:2rem;">Campaigns</h2>
      <table>
        <thead><tr><th>Name</th><th>Daily target</th><th>Status</th><th>Created</th><th></th></tr></thead>
        <tbody>{rows}</tbody>
      </table>

      <details style="margin-top:1.5rem;">
        <summary><strong>+ Add campaign</strong></summary>
        <form method="post" action="/campaigns" style="margin-top:1rem;">
          <div class="grid">
            <label>Name (slug)<input name="name" required placeholder="nz-real-estate"></label>
            <label>Niche<input name="niche" required placeholder="real estate agencies"></label>
            <label>Region<input name="region" required placeholder="New Zealand"></label>
          </div>
          <div class="grid">
            <label>Daily scrape target<input name="daily_scrape_target" type="number" value="50"></label>
            <label>Active<select name="active"><option value="true">Yes</option><option value="false">No</option></select></label>
          </div>
          <label>Offer brief (markdown)<textarea name="offer_brief" rows="6" placeholder="Paste the offer you're pitching..."></textarea></label>
          <button type="submit">Create campaign</button>
        </form>
      </details>
    """
    return _layout(body, "Campaigns")


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    f = request.form
    row = {
        "name": f["name"].strip(),
        "niche": f["niche"].strip(),
        "region": f["region"].strip(),
        "offer_brief": (f.get("offer_brief") or "").strip() or None,
        "daily_scrape_target": int(f.get("daily_scrape_target") or 50),
        "active": f.get("active", "true") == "true",
    }
    sb.insert("campaigns", row)
    return redirect(url_for("home"))


@app.route("/campaigns/<int:cid>/toggle", methods=["POST"])
def toggle_campaign(cid: int):
    rows = sb.select("campaigns", {"select": "active", "id": f"eq.{cid}"}, limit=1)
    current = rows[0]["active"] if rows else False
    sb.update("campaigns", {"id": cid}, {"active": not current})
    return redirect(url_for("home"))


@app.route("/campaigns/<int:cid>/scrape", methods=["POST"])
def scrape_now(cid: int):
    """Fire-and-forget — hits the daily_scrape cron with ?campaign_id=cid override."""
    import requests as _r
    base = os.environ.get("VERCEL_URL")
    base_url = f"https://{base}" if base else "https://lead-gen-fawn-seven.vercel.app"
    secret = os.environ.get("CRON_SECRET", "")
    try:
        _r.get(
            f"{base_url}/api/cron/daily_scrape",
            params={"campaign_id": cid},
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5,
        )
    except Exception:
        pass  # cron call is fire-and-forget; check /events for progress
    return redirect(url_for("home"))


@app.route("/leads")
def leads():
    rows = sb.select(
        "leads",
        {"select": "id,business,website,email,email_status,intent_score,enrichment_status,created_at,campaign_id", "order": "created_at.desc"},
        limit=200,
    )
    tr = "".join(
        f"""<tr>
          <td>{r['business']}</td>
          <td>{('<a href="' + r['website'] + '" target=_blank>site</a>') if r.get('website') else ''}</td>
          <td><code>{r.get('email') or '—'}</code></td>
          <td><span class="pill">{r.get('email_status') or '—'}</span></td>
          <td>{r.get('intent_score') or '—'}</td>
          <td><span class="pill pill-{r['enrichment_status']}">{r['enrichment_status']}</span></td>
          <td>{r['created_at'][:10]}</td>
        </tr>""" for r in rows
    ) or '<tr><td colspan="7"><em>No leads yet.</em></td></tr>'

    body = f"""
      <h2>Recent leads ({len(rows)})</h2>
      <table>
        <thead><tr><th>Business</th><th>Web</th><th>Email</th><th>Email status</th><th>Intent</th><th>Enrich</th><th>Added</th></tr></thead>
        <tbody>{tr}</tbody>
      </table>
    """
    return _layout(body, "Leads")


@app.route("/sequences")
def sequences():
    rows = sb.select(
        "sequences",
        {"select": "id,lead_id,status,current_step,opens,clicks,replied,bounced,next_send_at,paused_reason", "order": "updated_at.desc"},
        limit=200,
    )
    tr = "".join(
        f"""<tr>
          <td>{r['id']}</td>
          <td>{r['lead_id']}</td>
          <td><span class="pill pill-{r['status']}">{r['status']}</span></td>
          <td>{r['current_step']}</td>
          <td>{r['opens']}</td>
          <td>{r['clicks']}</td>
          <td>{'✓' if r['replied'] else ''}</td>
          <td>{'✗' if r['bounced'] else ''}</td>
          <td>{(r.get('next_send_at') or '')[:16]}</td>
          <td>{r.get('paused_reason') or ''}</td>
        </tr>""" for r in rows
    ) or '<tr><td colspan="10"><em>No sequences yet.</em></td></tr>'

    body = f"""
      <h2>Sequences ({len(rows)})</h2>
      <table>
        <thead><tr><th>ID</th><th>Lead</th><th>Status</th><th>Step</th><th>Opens</th><th>Clicks</th><th>Replied</th><th>Bounced</th><th>Next send</th><th>Paused reason</th></tr></thead>
        <tbody>{tr}</tbody>
      </table>
    """
    return _layout(body, "Sequences")


@app.route("/events")
def events():
    rows = sb.select(
        "sequence_events",
        {"select": "id,sequence_id,step,event_type,resend_id,ts", "order": "ts.desc"},
        limit=200,
    )
    tr = "".join(
        f"""<tr>
          <td>{r['ts'][:19].replace('T',' ')}</td>
          <td><span class="pill">{r['event_type']}</span></td>
          <td>{r.get('sequence_id') or ''}</td>
          <td>{r.get('step') or ''}</td>
          <td><small>{(r.get('resend_id') or '')[:24]}</small></td>
        </tr>""" for r in rows
    ) or '<tr><td colspan="5"><em>No events yet.</em></td></tr>'

    body = f"""
      <h2>Recent events ({len(rows)})</h2>
      <table>
        <thead><tr><th>When</th><th>Type</th><th>Sequence</th><th>Step</th><th>Resend ID</th></tr></thead>
        <tbody>{tr}</tbody>
      </table>
    """
    return _layout(body, "Events")
