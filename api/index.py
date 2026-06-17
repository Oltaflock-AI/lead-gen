"""Lead-gen autopilot dashboard — warm editorial design (matches prototype).

Server-rendered Flask. Light (cream/Fraunces/forest-green) by default, with a
dark override via the theme toggle. All data from Supabase.

Pages:  /  /scrape  /offers  /leads  /sequences  /sequences/<id>  /settings
Actions: create campaign, scrape now, start outreach, pause/resume, mark replied
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, url_for, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib import supabase as sb
from lib import niches

app = Flask(__name__)
ADMIN_KEY = os.environ.get("DASHBOARD_KEY", "")
PROD_URL = "https://lead-gen-fawn-seven.vercel.app"

STEP_META = {  # step -> (day label, name)
    1: ("Day 0", "Cold"), 2: ("Day 3", "Bump"), 3: ("Day 7", "FOMO"),
    4: ("Day 11", "Loom"), 5: ("Day 16", "Math"), 6: ("Day 21", "Quirky"),
    7: ("Day 28", "Pizza"),
}
WINDOWS = {"today": None, "7d": 168, "30d": 720, "all": 24 * 365 * 5}


@app.before_request
def _gate():
    if not ADMIN_KEY or request.path == "/healthz":
        return None
    if request.cookies.get("dk") == ADMIN_KEY:
        return None
    if request.args.get("dk") == ADMIN_KEY:
        resp = redirect(request.full_path.rstrip("?"))
        resp.set_cookie("dk", ADMIN_KEY, max_age=86400 * 30, httponly=True, samesite="Lax")
        return resp
    return ("Set ?dk=<DASHBOARD_KEY> in URL", 401)


# ════════════════ Styles (from prototype + dark override) ════════════════
CSS = """
:root{
  --bg:#faf9f6; --bg-soft:#f3f1ec; --card:#ffffff;
  --ink:#1a1a1a; --ink-soft:#4a4a4a; --ink-mute:#8a8a8a; --ink-faint:#b8b8b8;
  --line:#e8e6e1; --line-soft:#f0eee8;
  --accent:#2d5a3f; --accent-soft:#e8f0ea;
  --warn:#c44536; --warn-soft:#fdecea; --warn-line:#f5c6c0;
  --info:#3b6ea8; --info-soft:#eaf1f9; --good:#3d7a52;
  --font-serif:'Fraunces',Georgia,serif; --font-mono:'JetBrains Mono',ui-monospace,monospace;
  --font-sans:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; --radius:8px;
}
html.dark{
  --bg:#15130e; --bg-soft:#1f1c15; --card:#1c1a13;
  --ink:#f1eee6; --ink-soft:#c6c1b5; --ink-mute:#8c867a; --ink-faint:#5b564c;
  --line:#2d2920; --line-soft:#252118;
  --accent:#6fae88; --accent-soft:#1d3025;
  --warn:#e0897a; --warn-soft:#37201c; --warn-line:#583129;
  --info:#6f9fce; --info-soft:#1b2733; --good:#7bbd93;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--ink);font-family:var(--font-sans);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
body{display:flex;min-height:100vh}
a{color:inherit;text-decoration:none}
.sidebar{width:220px;flex-shrink:0;border-right:1px solid var(--line);padding:24px 20px;display:flex;flex-direction:column;gap:24px;background:var(--bg);position:fixed;height:100vh;overflow-y:auto}
.brand{display:flex;align-items:center;gap:10px;padding:0 4px}
.brand-mark{width:22px;height:22px;color:var(--accent)}
.brand-name{font-family:var(--font-serif);font-size:18px;font-weight:500;letter-spacing:-0.01em}
.brand-sub{font-family:var(--font-mono);font-size:10px;color:var(--ink-mute);letter-spacing:0.04em}
.nav-section{display:flex;flex-direction:column;gap:2px}
.nav-label{font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--ink-faint);padding:0 8px;margin-bottom:6px}
.nav-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;color:var(--ink-soft);font-size:13.5px;cursor:pointer;transition:background .12s}
.nav-item:hover{background:var(--bg-soft)}
.nav-item.active{background:var(--bg-soft);color:var(--ink);font-weight:500}
.nav-item .badge{margin-left:auto;font-family:var(--font-mono);font-size:10px;color:var(--ink-mute);background:var(--card);padding:1px 6px;border-radius:10px;border:1px solid var(--line)}
.nav-item.warn .badge{color:var(--warn);border-color:var(--warn-line)}
.main{flex:1;margin-left:220px;padding:36px 56px 80px;max-width:1200px}
.crumb{font-family:var(--font-mono);font-size:11px;color:var(--ink-mute);margin-bottom:6px;letter-spacing:0.04em}
.h1{font-family:var(--font-serif);font-size:36px;font-weight:500;letter-spacing:-0.02em;margin-bottom:6px}
.sub{color:var(--ink-soft);margin-bottom:28px;font-size:14px}
.window-bar{display:flex;align-items:center;gap:10px;margin-bottom:24px;font-size:12px;color:var(--ink-mute)}
.window-pills{display:flex;gap:2px;background:var(--bg-soft);padding:3px;border-radius:8px;border:1px solid var(--line)}
.window-pill{padding:5px 12px;font-size:12px;border-radius:5px;background:transparent;border:none;color:var(--ink-soft);cursor:pointer;font-family:inherit;font-weight:450}
.window-pill.active{background:var(--card);color:var(--ink);font-weight:500;box-shadow:0 1px 2px rgba(0,0,0,0.04)}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:32px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:18px}
.kpi-label{font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--ink-mute);margin-bottom:10px}
.kpi-num{font-family:var(--font-serif);font-size:32px;font-weight:500;letter-spacing:-0.02em;line-height:1.05}
.kpi-num .unit{font-size:18px;color:var(--ink-mute);margin-left:1px}
.kpi-meta{font-size:12px;color:var(--ink-mute);margin-top:6px}
.kpi.alert-kpi{background:var(--warn-soft);border-color:var(--warn-line)}
.kpi.alert-kpi .kpi-num,.kpi.alert-kpi .kpi-label{color:var(--warn)}
.block{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:20px;overflow:hidden}
.block-head{padding:16px 22px;border-bottom:1px solid var(--line-soft);display:flex;align-items:center;justify-content:space-between}
.block-title{font-family:var(--font-serif);font-size:18px;font-weight:500;letter-spacing:-0.01em}
.block-sub{font-size:12px;color:var(--ink-mute);margin-top:2px}
.block-body{padding:22px}
.block-actions{display:flex;gap:6px}
.inner-tabs{display:flex;border-bottom:1px solid var(--line);background:var(--bg)}
.inner-tab{padding:14px 22px;font-size:13px;color:var(--ink-mute);cursor:pointer;background:none;border:none;border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:450;font-family:inherit;display:flex;align-items:center;gap:8px}
.inner-tab:hover{color:var(--ink-soft)}
.inner-tab.active{color:var(--ink);border-bottom-color:var(--accent);font-weight:500}
.inner-tab .count{font-family:var(--font-mono);font-size:10px;color:var(--ink-mute);background:var(--card);padding:1px 6px;border-radius:10px;border:1px solid var(--line)}
.inner-tab.active .count{color:var(--accent);border-color:var(--accent-soft);background:var(--accent-soft)}
.inner-tab.warn .count{color:var(--warn);border-color:var(--warn-line);background:var(--warn-soft)}
.inner-panel{display:none}.inner-panel.active{display:block}
.pipeline{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.pipe-step{background:var(--bg-soft);border:1px solid var(--line);border-radius:var(--radius);padding:14px 12px;text-align:center}
.pipe-day{font-family:var(--font-mono);font-size:9px;color:var(--ink-faint);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px}
.pipe-name{font-size:11px;color:var(--ink-soft);font-weight:500;margin-bottom:8px}
.pipe-count{font-family:var(--font-serif);font-size:24px;font-weight:500;letter-spacing:-0.02em}
.pipe-count.zero{color:var(--ink-faint)}
.pipe-step.active{background:var(--accent-soft);border-color:var(--accent)}
.pipe-step.active .pipe-count{color:var(--accent)}
.niche-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.act-table{width:100%;border-collapse:collapse}
.act-table thead th{font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--ink-mute);text-align:left;padding:12px 22px;background:var(--bg-soft);border-bottom:1px solid var(--line)}
.act-table tbody td{padding:12px 22px;border-bottom:1px solid var(--line-soft);font-size:13px;vertical-align:top}
.act-table tbody tr:last-child td{border-bottom:none}
.act-table tbody tr:hover{background:var(--bg-soft)}
.act-table .when{font-family:var(--font-mono);font-size:11px;color:var(--ink-mute);white-space:nowrap}
.act-table .biz{font-weight:500;color:var(--ink)}
.act-table .email{font-size:11px;color:var(--ink-mute);font-family:var(--font-mono);margin-top:1px}
.act-table .subj{color:var(--ink-soft);max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{display:inline-block;padding:2px 8px;font-size:10px;font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.04em;border-radius:10px;border:1px solid;font-weight:500}
.chip.failed,.chip.bounced{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.chip.sent{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.chip.delivered{background:var(--bg-soft);border-color:var(--line);color:var(--ink-mute)}
.chip.opened{background:var(--info-soft);border-color:var(--info);color:var(--info)}
.chip.clicked{background:#fff5d8;border-color:#e8d39a;color:#7a5916}
.chip.replied{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.chip.active{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.chip.paused{background:#fef7ec;border-color:#f0d8a8;color:#8a5a16}
.chip.done,.chip.queued{background:var(--bg-soft);border-color:var(--line);color:var(--ink-mute)}
.chip.pending{background:var(--info-soft);border-color:var(--info);color:var(--info)}
.chip.enriched{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.group-head{background:var(--bg-soft);font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--ink-mute);padding:8px 22px;border-top:1px solid var(--line);border-bottom:1px solid var(--line-soft)}
.filter-row{display:flex;gap:8px;padding:14px 22px;border-bottom:1px solid var(--line-soft);background:var(--bg);flex-wrap:wrap}
.filter-chip{padding:5px 11px;font-size:12px;border-radius:14px;border:1px solid var(--line);color:var(--ink-soft);cursor:pointer;background:var(--card);font-family:inherit}
.filter-chip.active{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.filter-chip .count{font-family:var(--font-mono);font-size:10px;margin-left:4px;opacity:0.7}
.timeline{display:flex;flex-direction:column;border-left:1.5px solid var(--line);margin-left:8px;padding-left:24px}
.tl-item{position:relative;padding:10px 0}
.tl-item::before{content:'';position:absolute;left:-30px;top:14px;width:8px;height:8px;background:var(--card);border:2px solid var(--ink-faint);border-radius:50%}
.tl-item.live::before{background:var(--accent);border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.tl-time{font-family:var(--font-mono);font-size:11px;color:var(--ink-mute)}
.tl-text{font-size:13px;margin-top:2px}
.csv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.csv-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:18px;cursor:pointer;transition:border-color .12s,box-shadow .12s;display:block}
.csv-card:hover{border-color:var(--ink-faint);box-shadow:0 1px 4px rgba(0,0,0,0.04)}
.csv-name{font-family:var(--font-serif);font-size:17px;color:var(--ink);font-weight:500;margin-bottom:4px}
.csv-meta{font-size:11px;color:var(--ink-mute);margin-bottom:14px;font-family:var(--font-mono)}
.csv-health{display:flex;align-items:baseline;gap:8px;margin-bottom:12px}
.csv-health-num{font-family:var(--font-serif);font-size:28px;font-weight:500;letter-spacing:-0.02em}
.csv-health-num.good{color:var(--good)}.csv-health-num.bad{color:var(--warn)}
.csv-health-label{font-family:var(--font-mono);font-size:9px;color:var(--ink-mute);text-transform:uppercase;letter-spacing:0.08em}
.dots{display:inline-flex;gap:3px;align-items:center}
.dots .dot{width:7px;height:7px;border-radius:50%;background:var(--line)}
.dots .dot.done{background:var(--accent)}
.dots .dot.current{background:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.note-bar{background:var(--info-soft);border:1px solid var(--info);color:var(--ink-soft);padding:10px 14px;border-radius:var(--radius);font-size:12px;margin-bottom:18px}
.btn{padding:7px 14px;font-size:13px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;font-family:inherit;font-weight:500;display:inline-flex;align-items:center;gap:6px}
.btn:hover{background:var(--bg-soft)}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn.primary:hover{opacity:0.92;background:var(--accent)}
.btn.sm{padding:4px 10px;font-size:12px}
.btn.danger{color:var(--warn);border-color:var(--warn-line)}
.row-2{display:grid;grid-template-columns:1.5fr 1fr;gap:20px}
.muted{color:var(--ink-mute)}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--ink-mute);margin-bottom:5px;font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.04em}
.field input,.field textarea,.field select{width:100%;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:9px 12px;font-family:inherit;font-size:14px;color:var(--ink)}
.field input:focus,.field textarea:focus,.field select:focus{outline:none;border-color:var(--accent)}
.field textarea{font-family:var(--font-mono);font-size:12px}
.theme-btn{position:fixed;top:18px;right:22px;background:var(--card);border:1px solid var(--line);border-radius:8px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--ink-mute);z-index:50}
.theme-btn:hover{color:var(--ink)}
dialog{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:0;max-width:540px;width:100%}
dialog::backdrop{background:rgba(0,0,0,0.5)}
.empty{background:var(--bg-soft);border:1px dashed var(--line);border-radius:var(--radius);padding:48px;text-align:center;color:var(--ink-mute);font-size:13px}
.conn-row{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--line-soft)}
.conn-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.conn-dot.on{background:var(--good)}.conn-dot.off{background:var(--ink-faint)}
"""


# ════════════════ Data helpers ════════════════
def _events(hours=None):
    p = {"select": "event_type,ts,sequence_id,step,resend_id,meta", "order": "ts.desc"}
    if hours:
        p["ts"] = f"gte.{(datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()}"
    else:  # today
        p["ts"] = f"gte.{datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}"
    return sb.select("sequence_events", p, limit=20000)


def _counts(events):
    b = defaultdict(int)
    for e in events:
        b[e["event_type"]] += 1
    return b


def _campaigns():
    return sb.select("campaigns", {"select": "*", "order": "created_at.desc"}, limit=200)


def _lead_counts():
    out = defaultdict(lambda: {"leads": 0, "enriched": 0, "with_email": 0})
    for r in sb.select("leads", {"select": "campaign_id,enrichment_status,email"}, limit=100000):
        cid = r["campaign_id"]
        if cid is None:
            continue
        out[cid]["leads"] += 1
        if r["enrichment_status"] == "enriched":
            out[cid]["enriched"] += 1
        if r.get("email"):
            out[cid]["with_email"] += 1
    return out


def _spark_14d():
    days = defaultdict(int)
    for e in _events(24 * 14):
        if e["event_type"] == "sent":
            days[e["ts"][:10]] += 1
    pts = []
    for i in range(13, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        pts.append(days[d])
    return pts


# ════════════════ Shell ════════════════
def shell(active, crumb, h1, sub, body, badges=None):
    badges = badges or {}
    out_b = f' <span class="badge">{badges["outreach"]}</span>' if badges.get("outreach") else ""
    seq_b = f' <span class="badge">{badges.get("sequences", 0)}</span>'
    nav = f"""
    <div class="nav-section"><div class="nav-label">Pulse</div>
      <a class="nav-item {'active' if active=='dashboard' else ''}" href="/">Dashboard</a></div>
    <div class="nav-section"><div class="nav-label">Pipeline</div>
      <a class="nav-item {'active' if active=='scrape' else ''}" href="/scrape">Scrape</a>
      <a class="nav-item {'active' if active=='offers' else ''}" href="/offers">Offers</a>
      <a class="nav-item {'active' if active=='leads' else ''}" href="/leads">Leads</a>
      <a class="nav-item {'active' if active=='sequences' else ''}" href="/sequences">Sequences{seq_b}</a>
      <a class="nav-item {'active' if active=='events' else ''}" href="/events">Activity</a></div>
    <div class="nav-section"><div class="nav-label">System</div>
      <a class="nav-item {'active' if active=='settings' else ''}" href="/settings">Settings</a></div>"""
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h1} · Lead-gen</title>
<script>(function(){{try{{if(localStorage.getItem('theme')==='dark')document.documentElement.classList.add('dark');}}catch(e){{}}}})();</script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;450;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<aside class="sidebar">
  <div class="brand">
    <svg class="brand-mark" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2 L4 9 L12 7 L20 9 Z M4 11 L12 9 L20 11 L12 22 Z"/></svg>
    <div><div class="brand-name">Oltaflock</div><div class="brand-sub">lead-gen</div></div>
  </div>
  {nav}
  <div style="margin-top:auto;font-family:var(--font-mono);font-size:10px;color:var(--ink-faint);display:flex;align-items:center;gap:6px">
    <span style="width:6px;height:6px;border-radius:50%;background:var(--good)"></span>autopilot live</div>
</aside>
<button class="theme-btn" onclick="tg()" title="Theme">
  <svg id="ic-sun" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:none"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg>
  <svg id="ic-moon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>
</button>
<main class="main">
  <div class="crumb">{crumb}</div>
  <h1 class="h1">{h1}</h1>
  <p class="sub">{sub}</p>
  {body}
</main>
<script>
function tg(){{var d=document.documentElement.classList.toggle('dark');try{{localStorage.setItem('theme',d?'dark':'light');}}catch(e){{}}syncIcon();}}
function syncIcon(){{var dark=document.documentElement.classList.contains('dark');document.getElementById('ic-sun').style.display=dark?'block':'none';document.getElementById('ic-moon').style.display=dark?'none':'block';}}
syncIcon();
function showPanel(id,el){{document.querySelectorAll('.inner-panel').forEach(p=>p.classList.remove('active'));document.querySelectorAll('.inner-tab').forEach(t=>t.classList.remove('active'));document.getElementById('panel-'+id).classList.add('active');el.classList.add('active');}}
</script>
</body></html>"""


def chip(kind, label=None):
    return f'<span class="chip {kind}">{label or kind}</span>'


def dots(step):
    out = ""
    for i in range(1, 8):
        c = "done" if i <= step else ""
        if i == step:
            c = "current"
        out += f'<span class="dot {c}"></span>'
    return f'<span class="dots">{out}</span>'


# ════════════════ Routes ════════════════
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "lead-gen-dashboard"})


@app.route("/")
def home():
    w = request.args.get("w", "today")
    hours = WINDOWS.get(w, None)
    ev = _events(hours)
    c = _counts(ev)
    camps = _campaigns()
    lc = _lead_counts()
    total_leads = sum(v["leads"] for v in lc.values())
    n_camps = len(camps)
    active_seqs = sb.select("sequences", {"select": "id,current_step,status", "status": "eq.active"}, limit=100000)

    sent, deliv, opened, clicked, bounced, replied = (c["sent"], c["delivered"], c["opened"], c["clicked"], c["bounced"], c["replied"])
    drate = round(100 * deliv / sent, 1) if sent else 0
    orate = round(100 * opened / deliv, 1) if deliv else 0
    spark = _spark_14d()
    spark_max = max(spark + [1])
    pts = " ".join(f"{i*(200/13):.0f},{24 - (v/spark_max*22):.0f}" for i, v in enumerate(spark))

    drate_alert = sent and drate < 70
    wlabel = {"today": "Today", "7d": "7d", "30d": "30d", "all": "All"}
    pills = "".join(
        f'<a href="/?w={k}" class="window-pill {"active" if k==w else ""}">{wlabel[k]}</a>'
        for k in ["today", "7d", "30d", "all"])

    kpis = f"""
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Total leads</div><div class="kpi-num">{total_leads}</div><div class="kpi-meta">across {n_camps} campaigns</div></div>
      <div class="kpi"><div class="kpi-label">Sent · {wlabel[w]}</div><div class="kpi-num">{sent}</div>
        <svg viewBox="0 0 200 24" preserveAspectRatio="none" style="margin-top:10px;height:24px;width:100%">
          <polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="{pts}"/></svg></div>
      <div class="kpi {'alert-kpi' if drate_alert else ''}"><div class="kpi-label">Delivery rate</div><div class="kpi-num">{drate}<span class="unit">%</span></div><div class="kpi-meta">{deliv} of {sent}{' · ' + str(bounced) + ' bounced' if bounced else ''}</div></div>
      <div class="kpi"><div class="kpi-label">Open rate</div><div class="kpi-num">{orate}<span class="unit">%</span></div><div class="kpi-meta">{opened} of {deliv} delivered · {clicked} click{'s' if clicked!=1 else ''}</div></div>
    </div>"""

    # Activity panel
    arows = ""
    leadmap = {}
    seq_ids = list({e["sequence_id"] for e in ev[:60] if e.get("sequence_id")})
    if seq_ids:
        in_list = ",".join(str(s) for s in seq_ids)
        seqs = sb.select("sequences", {"select": "id,lead_id", "id": f"in.({in_list})"}, limit=500)
        lid_by_seq = {s["id"]: s["lead_id"] for s in seqs}
        lids = list({v for v in lid_by_seq.values()})
        if lids:
            ll = ",".join(str(x) for x in lids)
            for l in sb.select("leads", {"select": "id,business,email", "id": f"in.({ll})"}, limit=500):
                leadmap[l["id"]] = l
    else:
        lid_by_seq = {}
    for e in ev[:40]:
        lead = leadmap.get(lid_by_seq.get(e.get("sequence_id")), {})
        subj = (e.get("meta") or {}).get("subject", "") if isinstance(e.get("meta"), dict) else ""
        t = e["ts"][11:16]
        et = e["event_type"]
        if et == "opened_bot":
            continue
        arows += f"""<tr><td class="when">{t}</td>
          <td><div class="biz">{lead.get('business') or ('seq #'+str(e.get('sequence_id')) if e.get('sequence_id') else '—')}</div>
          <div class="email">{lead.get('email') or ''}</div></td>
          <td class="subj">{subj}</td><td>{chip(et if et in ('sent','delivered','opened','clicked','bounced','replied') else 'queued', et)}</td></tr>"""
    if not arows:
        arows = '<tr><td colspan="4" style="padding:32px;text-align:center;color:var(--ink-mute)">No activity in this window yet.</td></tr>'

    # Pipeline panel — distribution of active sequences by step
    dist = defaultdict(int)
    for s in active_seqs:
        st = s["current_step"] or 0
        dist[max(st, 1)] += 1
    pipe = ""
    for i in range(1, 8):
        day, name = STEP_META[i]
        n = dist.get(i, 0)
        pipe += f'<div class="pipe-step {"active" if n else ""}"><div class="pipe-day">{day}</div><div class="pipe-name">{name}</div><div class="pipe-count {"zero" if not n else ""}">{n}</div></div>'

    # Issues panel
    issues = ""
    brate = round(100 * bounced / sent, 1) if sent else 0
    if sent and brate > 5:
        issues += f'<div class="note-bar" style="background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)"><strong>Bounce rate {brate}% — over 5% threshold.</strong> {bounced} of {sent} sends bounced. Sustained bounces hurt domain reputation.</div>'
    noemail = sb.select("leads", {"select": "id", "email": "is.null", "enrichment_status": "eq.enriched"}, limit=10000)
    if noemail:
        issues += f'<div class="note-bar"><strong>{len(noemail)} enriched leads have no email.</strong> They can\'t be sequenced until an address is found. Re-run enrichment or add emails.</div>'
    senderr = sb.select("sequences", {"select": "id", "paused_reason": "like.send-error*"}, limit=1000)
    if senderr:
        issues += f'<div class="note-bar" style="background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)"><strong>{len(senderr)} sequences paused on send errors.</strong> Check Sequences → paused.</div>'
    if not issues:
        issues = '<div class="note-bar" style="background:var(--accent-soft);border-color:var(--accent);color:var(--accent)">✓ All clear — no bounce spikes, missing emails, or send errors detected.</div>'

    body = f"""
    <div class="window-bar"><span>Window</span><div class="window-pills">{pills}</div></div>
    {kpis}
    <div class="block">
      <div class="inner-tabs">
        <button class="inner-tab active" onclick="showPanel('activity',this)">Activity <span class="count">{len([e for e in ev if e['event_type']!='opened_bot'])}</span></button>
        <button class="inner-tab" onclick="showPanel('pipeline',this)">Pipeline <span class="count">{len(active_seqs)}</span></button>
        <button class="inner-tab {'warn' if issues and 'All clear' not in issues else ''}" onclick="showPanel('issues',this)">Issues</button>
      </div>
      <div class="inner-panel active" id="panel-activity">
        <table class="act-table"><thead><tr><th style="width:80px">When</th><th>Lead</th><th>Subject</th><th style="width:90px">Status</th></tr></thead><tbody>{arows}</tbody></table>
      </div>
      <div class="inner-panel" id="panel-pipeline"><div class="block-body">
        <div class="muted" style="font-size:12px;margin-bottom:14px">Where every active lead sits in the 28-day cadence ({len(active_seqs)} active).</div>
        <div class="pipeline">{pipe}</div>
      </div></div>
      <div class="inner-panel" id="panel-issues"><div class="block-body">{issues}</div></div>
    </div>"""
    return shell("dashboard", "workspace / dashboard", "Today",
                 datetime.now(timezone.utc).strftime("%A, %B %-d · %H:%M UTC"), body,
                 badges={"sequences": len(active_seqs)})


@app.route("/scrape")
def scrape_page():
    import json as _json
    niche_opts = "".join(f'<option value="{n}">{n}</option>' for n in niches.NICHE_PRESETS)
    country_opts = "".join(f'<option value="{c}">{c}</option>' for c in niches.COUNTRY_REGION_CODES)
    presets_json = _json.dumps(niches.NICHE_PRESETS)
    rating_opts = "".join(f'<option value="{v}">{l}</option>' for v, l in
                          [("0", "Any rating"), ("3", "3.0+"), ("3.5", "3.5+"), ("4", "4.0+"), ("4.5", "4.5+")])
    web_opts = "".join(f'<option value="{k}">{v}</option>' for k, v in niches.WEBSITE_FILTERS.items())
    body = f"""
    <div class="block"><div class="block-head"><div><div class="block-title">New scrape</div><div class="block-sub">Pick a niche, narrow the business types, set quality filters, scrape.</div></div></div>
    <div class="block-body">
      <div class="note-bar">Leads land in ~10–15s. The campaign stays <strong>paused</strong> (no emails) until you click <strong>Start outreach</strong>.</div>
      <form method="post" action="/campaigns/create-and-scrape" style="max-width:680px">
        <div class="row-2" style="grid-template-columns:1fr 1fr">
          <div class="field"><label>Niche</label><select name="niche" id="niche" required onchange="renderTypes()">{niche_opts}</select></div>
          <div class="field"><label>Country</label><select name="country" id="country">{country_opts}</select></div>
        </div>

        <div class="field">
          <label>Business types <span style="text-transform:none;color:var(--ink-faint)">— click to include (none selected = all)</span></label>
          <div id="types" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:4px"></div>
          <div style="margin-top:8px;display:flex;gap:10px"><a onclick="selAll(true)" style="font-size:12px;color:var(--accent);cursor:pointer">select all</a><a onclick="selAll(false)" style="font-size:12px;color:var(--ink-mute);cursor:pointer">clear</a></div>
        </div>

        <div class="row-2" style="grid-template-columns:1fr 1fr">
          <div class="field"><label>City / area <span style="text-transform:none;color:var(--ink-faint)">(optional)</span></label><input name="city" placeholder="Auckland"></div>
          <div class="field"><label>How many leads?</label><input name="daily_scrape_target" type="number" value="50"></div>
        </div>

        <div class="row-2" style="grid-template-columns:1fr 1fr 1fr">
          <div class="field"><label>Min rating</label><select name="min_rating">{rating_opts}</select></div>
          <div class="field"><label>Min reviews</label><input name="min_reviews" type="number" value="0"></div>
          <div class="field"><label>Website</label><select name="website_filter">{web_opts}</select></div>
        </div>

        <div class="field"><label>Offer brief <span style="text-transform:none;color:var(--ink-faint)">(what you pitch — used to write the emails)</span></label><textarea name="offer_brief" rows="5" placeholder="Who, what pain, what outcome — then the offer."></textarea></div>
        <input type="hidden" name="name" id="cname">
        <div style="display:flex;gap:10px;margin-top:4px">
          <button class="btn primary" type="submit" onclick="setName()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 3l14 9-14 9V3z"/></svg> Scrape now</button>
          <button class="btn" type="submit" formaction="/campaigns" onclick="setName()">Just create (don't scrape)</button>
        </div>
      </form>
    </div></div>
    <style>
      .tchip{{padding:5px 11px;font-size:12px;border-radius:14px;border:1px solid var(--line);color:var(--ink-soft);cursor:pointer;background:var(--card);user-select:none}}
      .tchip.on{{background:var(--accent);color:#fff;border-color:var(--accent)}}
    </style>
    <script>
      const PRESETS = {presets_json};
      function renderTypes(){{
        const niche=document.getElementById('niche').value;
        const box=document.getElementById('types'); box.innerHTML='';
        (PRESETS[niche]||[]).forEach(t=>{{
          const el=document.createElement('label'); el.className='tchip';
          el.innerHTML=`<input type="checkbox" name="business_types" value="${{t}}" style="display:none">${{t}}`;
          el.querySelector('input').addEventListener('change',e=>el.classList.toggle('on',e.target.checked));
          box.appendChild(el);
        }});
      }}
      function selAll(on){{document.querySelectorAll('#types input').forEach(i=>{{i.checked=on;i.parentElement.classList.toggle('on',on);}});}}
      function setName(){{
        const niche=document.getElementById('niche').value, country=document.getElementById('country').value;
        const city=document.querySelector('[name=city]').value;
        document.getElementById('cname').value=(niche+'-'+(city||country)).toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
        // fold city+country into region field
        let r=document.createElement('input'); r.type='hidden'; r.name='region'; r.value=(city?city+', ':'')+country;
        document.forms[0].appendChild(r);
      }}
      renderTypes();
    </script>"""
    return shell("scrape", "workspace / scrape", "Scrape", "Find leads", body)


@app.route("/offers")
def offers_page():
    import html as _html
    camps = _campaigns()
    cards = ""
    for c in camps:
        brief = (c.get("offer_brief") or "").strip()
        has = bool(brief)
        preview = (brief[:220] + "…") if len(brief) > 220 else (brief or "No offer set — emails fall back to a generic pitch.")
        esc = _html.escape(brief)
        cards += f"""<div class="csv-card" style="cursor:default">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div><div class="csv-name">{c['niche']}</div><div class="csv-meta">{c['name']} · {c['region']}</div></div>
            <button class="btn sm" onclick='openOffer({c["id"]}, {_json_attr(c["niche"])}, {_json_attr(brief)})'>Edit</button>
          </div>
          <div style="font-size:13px;color:var(--ink-soft);line-height:1.55;margin-top:6px;white-space:pre-wrap">{_html.escape(preview)}</div>
          <div style="margin-top:10px">{chip('set' if has else 'pending', 'offer set' if has else 'no offer')}</div>
        </div>"""
    body = f"""
    <div style="margin-bottom:16px"><div class="note-bar">The offer is injected into every email the sequencer writes. Keep it tight: product, pricing, risk-reversal, why you win.</div></div>
    {f'<div class="csv-grid">{cards}</div>' if camps else '<div class="empty">No offers yet. Create a campaign first.</div>'}
    <dialog id="offerModal">
      <form method="post" id="offerForm" class="block-body" style="min-width:520px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
          <div class="block-title" id="offerTitle">Edit offer</div>
          <button type="button" class="btn sm" onclick="document.getElementById('offerModal').close()">✕</button>
        </div>
        <div class="field"><label>Offer brief</label><textarea name="offer_brief" id="offerText" rows="16"></textarea></div>
        <div style="display:flex;gap:10px;justify-content:flex-end"><button type="button" class="btn" onclick="document.getElementById('offerModal').close()">Cancel</button><button class="btn primary" type="submit">Save offer</button></div>
      </form>
    </dialog>
    <script>
      function openOffer(id,niche,brief){{
        document.getElementById('offerForm').action='/campaigns/'+id+'/offer';
        document.getElementById('offerTitle').textContent='Offer · '+niche;
        document.getElementById('offerText').value=brief||'';
        document.getElementById('offerModal').showModal();
      }}
    </script>"""
    return shell("offers", "workspace / offers", "Offers", "What you sell", body)


def _json_attr(s):
    import json as _j
    return _j.dumps(s or "").replace("'", "\\u0027")


@app.route("/campaigns/<int:cid>/offer", methods=["POST"])
def update_offer(cid):
    sb.update("campaigns", {"id": cid}, {"offer_brief": (request.form.get("offer_brief") or "").strip() or None})
    return redirect("/offers")


REVENUE_BANDS = ["<$1M", "$1M–5M", "$5M–20M", "$20M–50M", "$50M+"]
PAGE_SIZE = 50


@app.route("/leads")
def leads_page():
    import html as _html
    a = request.args
    q = (a.get("q") or "").strip()
    campaign = a.get("campaign", "")
    enrich = a.get("enrich", "")
    email = a.get("email", "")
    intent_min = a.get("intent", "")
    revenue = a.get("revenue", "")
    page = max(1, int(a.get("page", "1") or 1))

    params = {"select": "id,business,email,email_status,city,country,intent_score,enrichment_status,campaign_id,signals,website",
              "order": "created_at.desc"}
    if campaign:
        params["campaign_id"] = f"eq.{campaign}"
    if enrich:
        params["enrichment_status"] = f"eq.{enrich}"
    if email == "has":
        params["email"] = "not.is.null"
    elif email == "none":
        params["email"] = "is.null"
    if intent_min:
        params["intent_score"] = f"gte.{intent_min}"
    if revenue:
        params["signals->>revenue_band"] = f"eq.{revenue}"
    if q:
        params["or"] = f"(business.ilike.*{q}*,email.ilike.*{q}*,city.ilike.*{q}*)"

    rows = sb.select("leads", params, limit=2000)
    total = len(rows)
    start = (page - 1) * PAGE_SIZE
    page_rows = rows[start:start + PAGE_SIZE]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    camps = _campaigns()
    cmap = {c["id"]: c for c in camps}

    # ── filter rail ──
    def opt(name, label, options, cur):
        os_ = '<option value="">Any</option>' + "".join(
            f'<option value="{v}" {"selected" if str(v)==str(cur) else ""}>{l}</option>' for v, l in options)
        return f'<div class="field" style="margin-bottom:12px"><label>{label}</label><select name="{name}" onchange="this.form.submit()">{os_}</select></div>'

    camp_opts = [(c["id"], f'{c["niche"]} · {c["region"]}') for c in camps]
    rail = f"""
    <form method="get" id="filters" class="{SURFACE if False else ''}" style="width:230px;flex-shrink:0">
      <div class="block" style="margin-bottom:0"><div class="block-head"><div class="block-title" style="font-size:15px">Filters</div></div>
      <div class="block-body" style="padding:16px">
        <div class="field" style="margin-bottom:12px"><label>Search</label><input name="q" value="{_html.escape(q)}" placeholder="name, email, city" onkeydown="if(event.key==='Enter')this.form.submit()"></div>
        {opt('campaign', 'Campaign', camp_opts, campaign)}
        {opt('enrich', 'Enrichment', [('enriched','Enriched'),('pending','Pending'),('failed','Failed')], enrich)}
        {opt('email', 'Email', [('has','Has email'),('none','No email')], email)}
        {opt('intent', 'Min intent', [('80','80+'),('60','60+'),('40','40+'),('20','20+')], intent_min)}
        {opt('revenue', 'Est. revenue', [(b,b) for b in REVENUE_BANDS], revenue)}
        <a href="/leads" class="btn sm" style="width:100%;justify-content:center;margin-top:4px">Clear filters</a>
      </div></div>
    </form>"""

    # ── table rows ──
    trows = ""
    for r in page_rows:
        c = cmap.get(r["campaign_id"], {})
        sig = r.get("signals") or {}
        rev = sig.get("revenue_band") or "—"
        loc = ", ".join([x for x in [r.get("city"), r.get("country")] if x]) or "—"
        intent = r.get("intent_score")
        intent_html = f'<span style="font-weight:600;color:{"var(--good)" if (intent or 0)>=60 else "var(--ink-soft)"}">{intent}</span>' if intent is not None else f'<span class="{SUBTLE}">—</span>'
        trows += f"""<tr class="lead-row">
          <td style="width:34px"><input type="checkbox" class="lcb" value="{r['id']}" onchange="sync()"></td>
          <td><div class="biz">{_html.escape(r['business'] or '')}</div><div class="email">{_html.escape(r.get('email') or 'no email')}</div></td>
          <td class="when">{_html.escape((c.get('niche') or ''))}</td>
          <td class="when">{_html.escape(loc)}</td>
          <td>{chip(r['enrichment_status'], r['enrichment_status'])}</td>
          <td class="when">{rev}</td>
          <td>{intent_html}</td>
        </tr>"""
    if not trows:
        trows = '<tr><td colspan="7" style="padding:40px;text-align:center;color:var(--ink-mute)">No leads match these filters.</td></tr>'

    # ── pagination ──
    def plink(p, label, disabled=False):
        if disabled:
            return f'<span class="btn sm" style="opacity:.4;cursor:default">{label}</span>'
        qs = "&".join(f"{k}={v}" for k, v in a.items() if k != "page" and v)
        return f'<a class="btn sm" href="/leads?{qs}&page={p}">{label}</a>'
    pager = f'<div style="display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-top:14px;font-size:12px;color:var(--ink-mute)"><span>Page {page} of {pages} · {total} leads</span>{plink(page-1,"‹ Prev",page<=1)}{plink(page+1,"Next ›",page>=pages)}</div>'

    table_html = f"""
    <div style="flex:1;min-width:0">
      <div id="bulkbar" class="block" style="margin-bottom:12px;display:none">
        <div style="padding:12px 18px;display:flex;align-items:center;justify-content:space-between">
          <div><span id="selcount" class="num-mono" style="font-weight:600">0</span> selected</div>
          <form method="post" action="/leads/enroll" id="enrollForm" onsubmit="return collect()">
            <input type="hidden" name="lead_ids" id="enrollIds">
            <button class="btn primary sm" type="submit"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> Add to sequence</button>
          </form>
        </div>
      </div>
      <div class="block">
        <table class="act-table">
          <thead><tr>
            <th style="width:34px"><input type="checkbox" id="selall" onchange="toggleAll(this)"></th>
            <th>Lead</th><th>Niche</th><th>Location</th><th>Enrichment</th><th>Est. revenue</th><th>Intent</th>
          </tr></thead>
          <tbody>{trows}</tbody>
        </table>
      </div>
      {pager}
    </div>"""

    body = f"""
    <div style="display:flex;gap:20px;align-items:flex-start">{rail}{table_html}</div>
    <style>.lead-row td{{padding-top:.55rem;padding-bottom:.55rem}} input[type=checkbox]{{accent-color:var(--accent);width:15px;height:15px;cursor:pointer}}</style>
    <script>
      function sync(){{
        const sel=[...document.querySelectorAll('.lcb:checked')];
        document.getElementById('selcount').textContent=sel.length;
        document.getElementById('bulkbar').style.display=sel.length?'block':'none';
      }}
      function toggleAll(el){{document.querySelectorAll('.lcb').forEach(c=>c.checked=el.checked);sync();}}
      function collect(){{
        const ids=[...document.querySelectorAll('.lcb:checked')].map(c=>c.value);
        if(!ids.length){{alert('Select at least one lead');return false;}}
        document.getElementById('enrollIds').value=ids.join(',');return true;
      }}
    </script>"""
    return shell("leads", "workspace / leads", "Leads", f"{total} leads · filter and enroll into sequences", body)


@app.route("/leads/enroll", methods=["POST"])
def enroll_leads():
    ids = [int(x) for x in (request.form.get("lead_ids") or "").split(",") if x.strip().isdigit()]
    if not ids:
        return redirect("/leads")
    in_list = ",".join(str(i) for i in ids)
    leads = sb.select("leads", {"select": "id,campaign_id,email", "id": f"in.({in_list})"}, limit=5000)
    existing = {s["lead_id"] for s in sb.select("sequences", {"select": "lead_id", "lead_id": f"in.({in_list})"}, limit=5000)}
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"lead_id": l["id"], "campaign_id": l["campaign_id"], "status": "active", "current_step": 0, "next_send_at": now}
            for l in leads if l.get("email") and l["id"] not in existing]
    if rows:
        sb.insert("sequences", rows, on_conflict="lead_id")
    return redirect("/sequences")


def _slug(*parts):
    import re
    s = "-".join(p.strip() for p in parts if p and p.strip())
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "campaign"


def _new_campaign(f) -> dict:
    niche = (f.get("niche") or "").strip()
    region = (f.get("region") or "").strip()
    if not region:
        city, country = (f.get("city") or "").strip(), (f.get("country") or "").strip()
        region = (f"{city}, {country}" if city else country).strip(", ") or "United States"
    name = (f.get("name") or "").strip() or _slug(niche, region)

    cfg: dict = {}
    types = f.getlist("business_types") if hasattr(f, "getlist") else (f.get("business_types") or [])
    types = [t for t in types if t]
    if types:
        cfg["business_types"] = types
    try:
        if float(f.get("min_rating") or 0) > 0:
            cfg["min_rating"] = float(f["min_rating"])
    except (TypeError, ValueError):
        pass
    try:
        if int(f.get("min_reviews") or 0) > 0:
            cfg["min_reviews"] = int(f["min_reviews"])
    except (TypeError, ValueError):
        pass
    if (f.get("website_filter") or "any") != "any":
        cfg["website_filter"] = f.get("website_filter")

    rows = sb.insert("campaigns", {
        "name": name, "niche": niche, "region": region,
        "offer_brief": (f.get("offer_brief") or "").strip() or None,
        "daily_scrape_target": int(f.get("daily_scrape_target") or 50),
        "active": f.get("active", "false") == "true",
        # search config lives in `notes` as JSON (avoids a schema migration)
        "notes": json.dumps(cfg) if cfg else None,
    }, on_conflict="name")
    return rows[0]


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    _new_campaign(request.form)
    return redirect(url_for("leads_page"))


@app.route("/campaigns/create-and-scrape", methods=["POST"])
def create_and_scrape():
    from lib import scrape
    c = _new_campaign(request.form)
    run = sb.insert("scrape_runs", {"campaign_id": c["id"], "target_count": c["daily_scrape_target"], "status": "running"})[0]
    try:
        rows = list(scrape.scrape_for_campaign(c, target=c["daily_scrape_target"]))
        n = len(sb.insert("leads", rows, on_conflict="campaign_id,business")) if rows else 0
        sb.update("scrape_runs", {"id": run["id"]}, {"status": "completed", "scraped_count": n, "finished_at": datetime.now(timezone.utc).isoformat()})
    except Exception as e:
        sb.update("scrape_runs", {"id": run["id"]}, {"status": "failed", "error": str(e)[:400], "finished_at": datetime.now(timezone.utc).isoformat()})
    return redirect(f"/campaigns/{c['id']}")


def _fire(path, **params):
    import requests as _r
    try:
        _r.get(f"{PROD_URL}{path}", params=params, headers={"Authorization": f"Bearer {os.environ.get('CRON_SECRET','')}"}, timeout=4)
    except Exception:
        pass


@app.route("/campaigns/<int:cid>/scrape", methods=["POST"])
def scrape_now(cid):
    _fire("/api/cron/daily_scrape", campaign_id=cid)
    return redirect(request.referrer or "/leads")


@app.route("/campaigns/<int:cid>/start", methods=["POST"])
def start_outreach(cid):
    sb.update("campaigns", {"id": cid}, {"active": True})
    _fire("/api/cron/sequencer_tick")
    return redirect(request.referrer or "/leads")


@app.route("/campaigns/<int:cid>/toggle", methods=["POST"])
def toggle_campaign(cid):
    rows = sb.select("campaigns", {"select": "active", "id": f"eq.{cid}"}, limit=1)
    sb.update("campaigns", {"id": cid}, {"active": not (rows[0]["active"] if rows else False)})
    return redirect(request.referrer or "/leads")


@app.route("/campaigns/<int:cid>")
def campaign_detail(cid):
    rows = sb.select("campaigns", {"select": "*", "id": f"eq.{cid}"}, limit=1)
    if not rows:
        return ("Not found", 404)
    c = rows[0]
    leads = sb.select("leads", {"select": "*", "campaign_id": f"eq.{cid}", "order": "created_at.desc"}, limit=200)
    runs = sb.select("scrape_runs", {"select": "*", "campaign_id": f"eq.{cid}", "order": "started_at.desc"}, limit=10)
    lrows = "".join(f"""<tr><td><div class="biz">{l['business']}</div><div class="email">{l.get('email') or 'no email'}</div></td>
      <td>{chip(l['enrichment_status'], l['enrichment_status'])}</td>
      <td class="when">{l.get('intent_score') if l.get('intent_score') is not None else '—'}</td>
      <td class="when">{l['created_at'][:10]}</td></tr>""" for l in leads)
    offer = f'<div class="block"><div class="block-head"><div class="block-title">Offer brief</div></div><div class="block-body"><pre style="font-family:var(--font-mono);font-size:12px;white-space:pre-wrap;color:var(--ink-soft)">{c.get("offer_brief")}</pre></div></div>' if c.get("offer_brief") else ""
    runrows = "".join(f'<tr><td class="when">{r["started_at"][:16].replace("T"," ")}</td><td>{chip(r["status"], r["status"])}</td><td class="when">{r["scraped_count"]}/{r.get("target_count") or "—"}</td></tr>' for r in runs)
    body = f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;margin-top:-12px">
      <div>{chip('active' if c['active'] else 'paused', 'active' if c['active'] else 'paused')}</div>
      <div class="block-actions">
        <form method="post" action="/campaigns/{cid}/toggle" style="display:inline"><button class="btn sm">{'Pause' if c['active'] else 'Resume'}</button></form>
        <form method="post" action="/campaigns/{cid}/scrape" style="display:inline"><button class="btn sm">Scrape now</button></form>
        <form method="post" action="/campaigns/{cid}/start" style="display:inline"><button class="btn sm primary">Start outreach</button></form>
      </div>
    </div>
    {offer}
    <div class="row-2">
      <div class="block"><div class="block-head"><div class="block-title">Leads</div><div class="block-sub">{len(leads)}</div></div>
        <table class="act-table"><thead><tr><th>Business</th><th>Enrich</th><th>Intent</th><th>Added</th></tr></thead><tbody>{lrows or '<tr><td colspan=4 style="padding:24px;text-align:center;color:var(--ink-mute)">No leads yet.</td></tr>'}</tbody></table></div>
      <div class="block"><div class="block-head"><div class="block-title">Scrape runs</div></div>
        <table class="act-table"><thead><tr><th>Started</th><th>Status</th><th>Scraped</th></tr></thead><tbody>{runrows or '<tr><td colspan=3 style="padding:24px;text-align:center;color:var(--ink-mute)">No runs.</td></tr>'}</tbody></table></div>
    </div>"""
    return shell("leads", f"leads / {c['name']}", c["niche"], f"{c['name']} · {c['region']}", body)


@app.route("/sequences")
def sequences_page():
    flt = request.args.get("s", "all")
    p = {"select": "*", "order": "updated_at.desc"}
    if flt != "all":
        p["status"] = f"eq.{flt}"
    rows = sb.select("sequences", p, limit=300)
    allrows = sb.select("sequences", {"select": "status"}, limit=100000)
    by = defaultdict(int)
    for r in allrows:
        by[r["status"]] += 1
    ev_today = _counts(_events(None))
    leadmap = {}
    lids = list({r["lead_id"] for r in rows})
    if lids:
        ll = ",".join(str(x) for x in lids)
        for l in sb.select("leads", {"select": "id,business,city,email", "id": f"in.({ll})"}, limit=500):
            leadmap[l["id"]] = l
    stat = f"""<div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Active</div><div class="kpi-num">{by['active']}</div></div>
      <div class="kpi"><div class="kpi-label">Replies (today)</div><div class="kpi-num">{ev_today['replied']}</div></div>
      <div class="kpi"><div class="kpi-label">Sent (today)</div><div class="kpi-num">{ev_today['sent']}</div></div>
      <div class="kpi"><div class="kpi-label">Paused</div><div class="kpi-num">{by['paused']}</div></div></div>"""
    tabs = ""
    for k in ["all", "active", "paused", "done"]:
        n = sum(by.values()) if k == "all" else by.get(k, 0)
        tabs += f'<a href="/sequences?s={k}" class="filter-chip {"active" if k==flt else ""}">{k.title()} <span class="count">{n}</span></a>'
    trows = ""
    for r in rows:
        l = leadmap.get(r["lead_id"], {})
        trows += f"""<tr onclick="location.href='/sequences/{r['id']}'" style="cursor:pointer">
          <td><div class="biz">{l.get('business') or '#'+str(r['lead_id'])}</div><div class="email">{l.get('email') or ''}</div></td>
          <td class="when">{l.get('city') or ''}</td>
          <td>{dots(r['current_step'])}</td>
          <td>{chip(r['status'], r['status'])}</td>
          <td class="when">{(r.get('next_send_at') or '')[:16].replace('T',' ')}</td>
          <td class="when">{r.get('paused_reason') or ''}</td></tr>"""
    table = f"""<div class="block"><div class="filter-row">{tabs}</div>
      <table class="act-table"><thead><tr><th>Lead</th><th>City</th><th>Step</th><th>Status</th><th>Next send</th><th>Reason</th></tr></thead>
      <tbody>{trows or '<tr><td colspan=6 style="padding:32px;text-align:center;color:var(--ink-mute)">No sequences. Start outreach on a campaign.</td></tr>'}</tbody></table></div>"""
    return shell("sequences", "workspace / sequences", "Sequences", "The 7-step nurture · 28-day cadence",
                 stat + table, badges={"sequences": by.get("active", 0)})


@app.route("/sequences/<int:sid>")
def sequence_detail(sid):
    srows = sb.select("sequences", {"select": "*", "id": f"eq.{sid}"}, limit=1)
    if not srows:
        return ("Not found", 404)
    s = srows[0]
    lead = (sb.select("leads", {"select": "*", "id": f"eq.{s['lead_id']}"}, limit=1) or [{}])[0]
    drafts = sb.select("drafts", {"select": "*", "sequence_id": f"eq.{sid}", "order": "step.asc"}, limit=20)
    evs = sb.select("sequence_events", {"select": "*", "sequence_id": f"eq.{sid}", "order": "ts.desc"}, limit=100)
    thread = ""
    for d in drafts:
        day, name = STEP_META.get(d["step"], ("", ""))
        thread += f"""<div class="block"><div class="block-head"><div><div class="block-title" style="font-size:15px">{d['subject']}</div><div class="block-sub">Step {d['step']} · {name} · {day} · {d.get('angle') or ''}</div></div></div>
        <div class="block-body"><pre style="font-family:var(--font-sans);font-size:13px;white-space:pre-wrap;color:var(--ink-soft);line-height:1.6">{d['body']}</pre></div></div>"""
    if not thread:
        thread = '<div class="empty">No drafts yet. They appear as the sequencer sends each step.</div>'
    tl = "".join(f'<div class="tl-item {"live" if e["event_type"] in ("opened","clicked","replied") else ""}"><div class="tl-time">{e["ts"][:16].replace("T"," ")}</div><div class="tl-text">{chip(e["event_type"], e["event_type"])} {("step "+str(e["step"])) if e.get("step") else ""}</div></div>' for e in evs) or '<div class="muted" style="font-size:12px">No events yet.</div>'
    actions = ""
    if s["status"] == "active":
        actions += f'<form method="post" action="/sequences/{sid}/pause" style="display:inline"><button class="btn sm">Pause</button></form>'
    elif s["status"] == "paused":
        actions += f'<form method="post" action="/sequences/{sid}/resume" style="display:inline"><button class="btn sm primary">Resume</button></form>'
    actions += f'<form method="post" action="/sequences/{sid}/replied" style="display:inline"><button class="btn sm">Mark replied</button></form>'
    body = f"""
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-top:-12px;margin-bottom:20px">
      <div style="display:flex;gap:8px;align-items:center">{chip(s['status'], s['status'])} {dots(s['current_step'])}</div>
      <div class="block-actions">{actions}</div>
    </div>
    <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      <div class="kpi"><div class="kpi-label">Step</div><div class="kpi-num">{s['current_step']}<span class="unit">/7</span></div></div>
      <div class="kpi"><div class="kpi-label">Opens</div><div class="kpi-num">{s['opens']}</div></div>
      <div class="kpi"><div class="kpi-label">Clicks</div><div class="kpi-num">{s['clicks']}</div></div>
      <div class="kpi"><div class="kpi-label">Next send</div><div class="kpi-num" style="font-size:16px;font-family:var(--font-mono)">{(s.get('next_send_at') or '—')[:16].replace('T',' ')}</div></div>
    </div>
    <div class="row-2">
      <div><div class="block-title" style="margin-bottom:12px">Email thread</div>{thread}</div>
      <div><div class="block-title" style="margin-bottom:12px">Activity</div><div class="block"><div class="block-body"><div class="timeline">{tl}</div></div></div></div>
    </div>"""
    return shell("sequences", f"sequences / #{sid}", lead.get("business") or f"Sequence #{sid}", lead.get("email") or "", body)


@app.route("/sequences/<int:sid>/pause", methods=["POST"])
def pause_seq(sid):
    sb.update("sequences", {"id": sid}, {"status": "paused", "paused_reason": "manual"})
    return redirect(request.referrer or "/sequences")


@app.route("/sequences/<int:sid>/resume", methods=["POST"])
def resume_seq(sid):
    sb.update("sequences", {"id": sid}, {"status": "active", "paused_reason": None})
    return redirect(request.referrer or "/sequences")


@app.route("/sequences/<int:sid>/replied", methods=["POST"])
def mark_replied(sid):
    sb.update("sequences", {"id": sid}, {"replied": True, "status": "paused", "paused_reason": "replied", "next_send_at": None})
    sb.insert("sequence_events", {"sequence_id": sid, "event_type": "replied", "meta": {"via": "manual"}})
    return redirect(request.referrer or "/sequences")


@app.route("/events")
def events_page():
    rows = sb.select("sequence_events", {"select": "*", "order": "ts.desc"}, limit=200)
    tr = "".join(f'<tr><td class="when">{e["ts"][:19].replace("T"," ")}</td><td>{chip(e["event_type"], e["event_type"])}</td><td class="when">{e.get("sequence_id") or ""}</td><td class="when">{e.get("step") or ""}</td><td class="email">{(e.get("resend_id") or "")[:28]}</td></tr>' for e in rows if e["event_type"] != "opened_bot")
    body = f'<div class="block"><table class="act-table"><thead><tr><th>When (UTC)</th><th>Event</th><th>Seq</th><th>Step</th><th>Resend ID</th></tr></thead><tbody>{tr or "<tr><td colspan=5 style=padding:32px;text-align:center;color:var(--ink-mute)>No events yet.</td></tr>"}</tbody></table></div>'
    return shell("events", "workspace / activity", "Activity", "Every send, open, click, bounce, reply", body)


@app.route("/settings")
def settings_page():
    def st(name):
        return "on" if os.environ.get(name) else "off"
    conns = [
        ("Supabase", "SUPABASE_SERVICE_KEY", "state of truth"),
        ("Anthropic (Claude)", "ANTHROPIC_API_KEY", "email drafting + enrichment"),
        ("Resend", "RESEND_API_KEY", "email sending"),
        ("Resend webhook", "RESEND_WEBHOOK_SECRET", "open/click/bounce events"),
        ("Google Places", "GOOGLE_PLACES_API_KEY", "scraping"),
        ("Cron secret", "CRON_SECRET", "protects cron endpoints"),
    ]
    rows = "".join(f'<div class="conn-row"><span class="conn-dot {st(env)}"></span><div style="flex:1"><div style="font-weight:500">{name}</div><div class="muted" style="font-size:12px">{desc}</div></div><span class="mono muted" style="font-size:11px">{"✓ set" if os.environ.get(env) else "missing"}</span></div>' for name, env, desc in conns)
    daily_cap = os.environ.get("LEADGEN_DAILY_CAP", "100")
    body = f"""
    <div class="block"><div class="block-head"><div class="block-title">Connections</div><div class="block-sub">Service health — set via Vercel env vars</div></div>
      <div class="block-body">{rows}</div></div>
    <div class="block"><div class="block-head"><div class="block-title">Autopilot</div></div>
      <div class="block-body">
        <div class="conn-row"><div style="flex:1"><div style="font-weight:500">Daily send cap</div><div class="muted" style="font-size:12px">Max emails/day across all campaigns</div></div><span class="mono">{daily_cap}</span></div>
        <div class="conn-row"><div style="flex:1"><div style="font-weight:500">Cadence</div><div class="muted" style="font-size:12px">7 steps · day 0,3,7,11,16,21,28 · accelerates on opens</div></div><span class="mono">28d</span></div>
        <div class="conn-row" style="border:none"><div style="flex:1"><div style="font-weight:500">Crons</div><div class="muted" style="font-size:12px">scrape 08:00 · digest 16:00 UTC · sequencer every 5m (GitHub Actions)</div></div><span class="mono">live</span></div>
      </div></div>"""
    return shell("settings", "workspace / settings", "Settings", "Plumbing", body)
