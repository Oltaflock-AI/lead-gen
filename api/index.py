"""Lead-gen autopilot dashboard. Vercel Flask. Tailwind via CDN, dark by default.

Routes:
  GET  /              dashboard home — KPIs + 7d chart + campaigns
  POST /campaigns     create
  POST /campaigns/<id>/toggle
  POST /campaigns/<id>/scrape   manual cron fire
  GET  /campaigns/<id>          detail: leads + sequences for one campaign
  GET  /leads                   all leads
  GET  /sequences               all sequences
  GET  /events                  recent events
  GET  /healthz
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, url_for, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib import supabase as sb

app = Flask(__name__)
ADMIN_KEY = os.environ.get("DASHBOARD_KEY", "")
PROD_URL = "https://lead-gen-fawn-seven.vercel.app"


@app.before_request
def _gate():
    if not ADMIN_KEY or request.path == "/healthz":
        return None
    if request.cookies.get("dk") == ADMIN_KEY:
        return None
    if request.args.get("dk") == ADMIN_KEY:
        resp = redirect(request.path)
        resp.set_cookie("dk", ADMIN_KEY, max_age=86400 * 30, httponly=True, samesite="Lax")
        return resp
    return ("Set ?dk=<DASHBOARD_KEY> in URL", 401)


# ─────────── Data helpers ───────────
def _events_window(hours: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return sb.select(
        "sequence_events",
        {"select": "event_type,ts", "ts": f"gte.{since}", "order": "ts.desc"},
        limit=20000,
    )


def _kpi_24h() -> dict:
    rows = _events_window(24)
    bucket: dict[str, int] = defaultdict(int)
    for r in rows:
        bucket[r["event_type"]] += 1
    return {
        "sent": bucket["sent"], "delivered": bucket["delivered"],
        "opened": bucket["opened"], "clicked": bucket["clicked"],
        "replied": bucket["replied"], "bounced": bucket["bounced"],
    }


def _series_7d() -> dict:
    rows = _events_window(24 * 7)
    days: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        d = r["ts"][:10]
        days[d][r["event_type"]] += 1
    labels: list[str] = []
    sent: list[int] = []
    opened: list[int] = []
    for i in range(6, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d[5:])
        sent.append(days[d]["sent"])
        opened.append(days[d]["opened"])
    return {"labels": labels, "sent": sent, "opened": opened}


def _campaign_counts() -> dict[int, dict]:
    leads = sb.select("leads", {"select": "campaign_id,enrichment_status"}, limit=100000)
    out: dict[int, dict] = defaultdict(lambda: {"leads": 0, "pending": 0, "enriched": 0})
    for r in leads:
        cid = r["campaign_id"]
        if cid is None:
            continue
        out[cid]["leads"] += 1
        if r["enrichment_status"] == "pending":
            out[cid]["pending"] += 1
        elif r["enrichment_status"] == "enriched":
            out[cid]["enriched"] += 1
    return out


def _campaigns() -> list[dict]:
    return sb.select("campaigns", {"select": "*", "order": "created_at.desc"}, limit=200)


# ─────────── Layout ───────────
def page(active: str, title: str, body: str, page_title: str | None = None) -> str:
    page_title = page_title or title
    nav_items = [
        ("home",      "/",            "Dashboard",  "layout-dashboard"),
        ("campaigns", "/",            None,         None),  # alias
        ("leads",     "/leads",       "Leads",      "users"),
        ("sequences", "/sequences",   "Sequences",  "send"),
        ("events",    "/events",      "Events",     "activity"),
    ]
    nav_html = ""
    for key, href, label, icon in nav_items:
        if label is None:
            continue
        is_active = (key == active) or (active == "home" and key == "home")
        cls = "bg-zinc-800 text-white" if is_active else "text-zinc-400 hover:text-white hover:bg-zinc-800/50"
        nav_html += f"""
        <a href="{href}" class="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition {cls}">
          <i data-lucide="{icon}" class="w-4 h-4"></i><span>{label}</span>
        </a>"""

    return f"""<!doctype html><html lang="en" class="dark">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title} · Lead-gen autopilot</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
tailwind.config = {{
  darkMode: 'class',
  theme: {{ extend: {{
    fontFamily: {{ sans: ['Inter','ui-sans-serif','system-ui','sans-serif'], mono: ['JetBrains Mono','ui-monospace','monospace'] }},
    colors: {{ brand: {{ DEFAULT: '#6366f1', 50:'#eef2ff', 100:'#e0e7ff', 500:'#6366f1', 600:'#4f46e5', 700:'#4338ca' }} }},
  }} }},
}}
</script>
<link rel="preconnect" href="https://rsms.me/">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<style>
  html, body {{ font-feature-settings: 'cv02','cv03','cv04','cv11'; }}
  ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
  ::-webkit-scrollbar-thumb {{ background: #3f3f46; border-radius: 4px; }}
  ::-webkit-scrollbar-track {{ background: transparent; }}
  .num-mono {{ font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono',ui-monospace,monospace; }}
</style>
</head>
<body class="bg-zinc-950 text-zinc-100 antialiased">
  <div class="min-h-screen flex">
    <!-- Sidebar -->
    <aside class="w-60 border-r border-zinc-800 bg-zinc-950 flex flex-col">
      <div class="px-4 py-5 border-b border-zinc-800 flex items-center gap-2">
        <div class="w-7 h-7 rounded-md bg-brand flex items-center justify-center"><i data-lucide="zap" class="w-4 h-4 text-white"></i></div>
        <div>
          <div class="text-sm font-semibold">Lead-gen</div>
          <div class="text-[11px] text-zinc-500 -mt-0.5">autopilot · oltaflock</div>
        </div>
      </div>
      <nav class="flex-1 px-3 py-4 space-y-1">{nav_html}</nav>
      <div class="px-3 py-3 border-t border-zinc-800 text-[11px] text-zinc-500">
        <div class="flex items-center gap-2"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>autopilot live</div>
        <div class="mt-1 font-mono">{PROD_URL.replace('https://','')}</div>
      </div>
    </aside>

    <!-- Main -->
    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-14 border-b border-zinc-800 flex items-center justify-between px-6 sticky top-0 bg-zinc-950/80 backdrop-blur z-10">
        <h1 class="text-sm font-medium text-zinc-300">{title}</h1>
        <div class="flex items-center gap-2 text-xs text-zinc-500">
          <span class="num-mono">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</span>
        </div>
      </header>
      <main class="flex-1 p-6 overflow-x-auto">{body}</main>
    </div>
  </div>
  <script>lucide.createIcons();</script>
</body></html>"""


# ─────────── Components ───────────
def kpi_card(label: str, value: int, sublabel: str = "", trend: str = "") -> str:
    return f"""
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
      <div class="flex items-center justify-between">
        <div class="text-[11px] uppercase tracking-wider text-zinc-500">{label}</div>
        {f'<div class="text-[11px] text-emerald-400">{trend}</div>' if trend else ''}
      </div>
      <div class="mt-2 text-2xl font-semibold num-mono">{value}</div>
      {f'<div class="mt-1 text-xs text-zinc-500">{sublabel}</div>' if sublabel else ''}
    </div>"""


def pill(text: str, kind: str = "neutral") -> str:
    palette = {
        "neutral":  "bg-zinc-800 text-zinc-300 border-zinc-700",
        "active":   "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
        "paused":   "bg-amber-500/10 text-amber-300 border-amber-500/30",
        "pending":  "bg-sky-500/10 text-sky-300 border-sky-500/30",
        "enriched": "bg-violet-500/10 text-violet-300 border-violet-500/30",
        "failed":   "bg-rose-500/10 text-rose-300 border-rose-500/30",
        "bounced":  "bg-rose-500/10 text-rose-300 border-rose-500/30",
        "completed":"bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
        "sent":     "bg-sky-500/10 text-sky-300 border-sky-500/30",
        "opened":   "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
        "clicked":  "bg-violet-500/10 text-violet-300 border-violet-500/30",
        "delivered":"bg-zinc-700/40 text-zinc-300 border-zinc-700",
        "replied":  "bg-emerald-500/20 text-emerald-200 border-emerald-500/40",
    }
    cls = palette.get(kind, palette["neutral"])
    return f'<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border {cls}">{text}</span>'


def empty_state(icon: str, title: str, message: str, cta_html: str = "") -> str:
    return f"""
    <div class="text-center py-16">
      <div class="w-12 h-12 rounded-full bg-zinc-800 border border-zinc-700 mx-auto flex items-center justify-center mb-4">
        <i data-lucide="{icon}" class="w-5 h-5 text-zinc-400"></i>
      </div>
      <div class="text-sm font-medium text-zinc-300">{title}</div>
      <div class="text-xs text-zinc-500 mt-1 max-w-md mx-auto">{message}</div>
      {f'<div class="mt-6">{cta_html}</div>' if cta_html else ''}
    </div>"""


def btn(text: str, variant: str = "primary", **attrs) -> str:
    variants = {
        "primary":   "bg-brand hover:bg-brand-600 text-white",
        "secondary": "bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700",
        "ghost":     "bg-transparent hover:bg-zinc-800 text-zinc-300",
        "danger":    "bg-rose-600/90 hover:bg-rose-600 text-white",
    }
    cls = variants.get(variant, variants["primary"])
    attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return f'<button {attr_str} class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition {cls}">{text}</button>'


# ─────────── Routes ───────────
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "lead-gen-dashboard"})


@app.route("/")
def home():
    k = _kpi_24h()
    s = _series_7d()
    camps = _campaigns()
    counts = _campaign_counts()

    # KPI row
    open_rate = round(100 * k["opened"] / k["delivered"]) if k["delivered"] else 0
    click_rate = round(100 * k["clicked"] / k["delivered"]) if k["delivered"] else 0
    kpis = "".join([
        kpi_card("Sent (24h)",     k["sent"]),
        kpi_card("Delivered",      k["delivered"]),
        kpi_card("Opened",         k["opened"], f"{open_rate}% open rate"),
        kpi_card("Clicked",        k["clicked"], f"{click_rate}% click rate"),
        kpi_card("Replied",        k["replied"]),
        kpi_card("Bounced",        k["bounced"]),
    ])

    # Campaign rows
    if camps:
        rows_html = ""
        for c in camps:
            cnt = counts.get(c["id"], {"leads": 0, "pending": 0, "enriched": 0})
            kind = "active" if c["active"] else "paused"
            rows_html += f"""
            <tr class="border-b border-zinc-800 hover:bg-zinc-900/40 transition">
              <td class="py-3 px-4">
                <a href="/campaigns/{c['id']}" class="font-medium text-zinc-100 hover:text-brand-500">{c['name']}</a>
                <div class="text-[11px] text-zinc-500 mt-0.5">{c['niche']} · {c['region']}</div>
              </td>
              <td class="py-3 px-4">{pill('active' if c['active'] else 'paused', kind)}</td>
              <td class="py-3 px-4 num-mono text-zinc-300">{cnt['leads']}</td>
              <td class="py-3 px-4 num-mono text-zinc-400">{cnt['pending']}</td>
              <td class="py-3 px-4 num-mono text-zinc-300">{c['daily_scrape_target']}</td>
              <td class="py-3 px-4 text-zinc-500 text-xs num-mono">{c['created_at'][:10]}</td>
              <td class="py-3 px-4 text-right whitespace-nowrap">
                <form method="post" action="/campaigns/{c['id']}/toggle" class="inline">
                  <button class="text-xs px-2.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700">{'Pause' if c['active'] else 'Resume'}</button>
                </form>
                <form method="post" action="/campaigns/{c['id']}/scrape" class="inline ml-1">
                  <button class="text-xs px-2.5 py-1 rounded-md bg-brand hover:bg-brand-600 text-white inline-flex items-center gap-1"><i data-lucide="play" class="w-3 h-3"></i>Scrape now</button>
                </form>
              </td>
            </tr>"""
        campaigns_table = f"""
        <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500">
              <tr>
                <th class="text-left py-3 px-4 font-medium">Campaign</th>
                <th class="text-left py-3 px-4 font-medium">Status</th>
                <th class="text-left py-3 px-4 font-medium">Leads</th>
                <th class="text-left py-3 px-4 font-medium">Pending</th>
                <th class="text-left py-3 px-4 font-medium">Daily target</th>
                <th class="text-left py-3 px-4 font-medium">Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""
    else:
        campaigns_table = empty_state(
            "rocket",
            "No campaigns yet",
            "Create your first campaign to start the autopilot. Define the niche, the region, and how many leads to scrape per day.",
            f'<button onclick="document.getElementById(\'add-campaign\').showModal()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium bg-brand hover:bg-brand-600 text-white"><i data-lucide="plus" class="w-3.5 h-3.5"></i>Create campaign</button>',
        )

    body = f"""
    <!-- Top action bar -->
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="text-xl font-semibold">Dashboard</h2>
        <div class="text-xs text-zinc-500 mt-0.5">Live metrics from the autopilot loop</div>
      </div>
      <button onclick="document.getElementById('add-campaign').showModal()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium bg-brand hover:bg-brand-600 text-white">
        <i data-lucide="plus" class="w-3.5 h-3.5"></i>New campaign
      </button>
    </div>

    <!-- KPIs -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">{kpis}</div>

    <!-- 7-day chart -->
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4 mb-6">
      <div class="flex items-center justify-between mb-3">
        <div>
          <div class="text-sm font-medium text-zinc-200">Last 7 days</div>
          <div class="text-[11px] text-zinc-500">Sent vs Opened by day</div>
        </div>
        <div class="flex items-center gap-3 text-xs text-zinc-400">
          <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-sky-400"></span>Sent</span>
          <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400"></span>Opened</span>
        </div>
      </div>
      <canvas id="chart" height="80"></canvas>
    </div>

    <!-- Campaigns -->
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-sm font-medium text-zinc-200">Campaigns</h3>
      <a href="/leads" class="text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1">View all leads <i data-lucide="arrow-right" class="w-3 h-3"></i></a>
    </div>
    {campaigns_table}

    <!-- Add campaign modal -->
    <dialog id="add-campaign" class="bg-zinc-900 border border-zinc-800 rounded-xl p-0 backdrop:bg-zinc-950/70 text-zinc-100 max-w-lg w-full">
      <form method="post" action="/campaigns" class="p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-base font-semibold">New campaign</h3>
          <button type="button" onclick="document.getElementById('add-campaign').close()" class="text-zinc-500 hover:text-white"><i data-lucide="x" class="w-4 h-4"></i></button>
        </div>
        <div class="space-y-3 text-sm">
          <div>
            <label class="block text-xs text-zinc-400 mb-1">Name (slug)</label>
            <input name="name" required placeholder="nz-real-estate" class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none">
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-zinc-400 mb-1">Niche</label>
              <input name="niche" required placeholder="real estate agencies" class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-xs text-zinc-400 mb-1">Region</label>
              <input name="region" required placeholder="Auckland, New Zealand" class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none">
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-zinc-400 mb-1">Daily scrape target</label>
              <input name="daily_scrape_target" type="number" value="50" class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none num-mono">
            </div>
            <div>
              <label class="block text-xs text-zinc-400 mb-1">Status</label>
              <select name="active" class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none">
                <option value="true">Active</option><option value="false">Paused</option>
              </select>
            </div>
          </div>
          <div>
            <label class="block text-xs text-zinc-400 mb-1">Offer brief <span class="text-zinc-600">(markdown, what you're pitching)</span></label>
            <textarea name="offer_brief" rows="6" placeholder="One-liner: who, what pain, what outcome.&#10;&#10;Then 3-5 bullets on the specific offer." class="w-full bg-zinc-950 border border-zinc-800 rounded-md px-3 py-2 focus:border-brand-500 focus:outline-none font-mono text-xs"></textarea>
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-6 pt-4 border-t border-zinc-800">
          <button type="button" onclick="document.getElementById('add-campaign').close()" class="px-3 py-1.5 rounded-md text-xs font-medium text-zinc-300 hover:bg-zinc-800">Cancel</button>
          <button type="submit" class="px-3 py-1.5 rounded-md text-xs font-medium bg-brand hover:bg-brand-600 text-white">Create campaign</button>
        </div>
      </form>
    </dialog>

    <script>
      const ctx = document.getElementById('chart');
      if (ctx) {{
        new Chart(ctx, {{
          type: 'line',
          data: {{
            labels: {s['labels']},
            datasets: [
              {{ label: 'Sent',   data: {s['sent']},   borderColor:'#38bdf8', backgroundColor:'rgba(56,189,248,0.08)', tension:0.35, fill:true, borderWidth:2, pointRadius:3, pointBackgroundColor:'#38bdf8' }},
              {{ label: 'Opened', data: {s['opened']}, borderColor:'#34d399', backgroundColor:'rgba(52,211,153,0.08)', tension:0.35, fill:true, borderWidth:2, pointRadius:3, pointBackgroundColor:'#34d399' }},
            ]
          }},
          options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
              x: {{ grid: {{ display: false }}, ticks: {{ color:'#71717a', font: {{ size: 11 }} }} }},
              y: {{ grid: {{ color: '#27272a' }}, ticks: {{ color:'#71717a', font: {{ size: 11 }}, precision: 0 }} }},
            }},
          }},
        }});
      }}
    </script>
    """
    return page("home", "Dashboard", body)


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    f = request.form
    sb.insert("campaigns", {
        "name": f["name"].strip(),
        "niche": f["niche"].strip(),
        "region": f["region"].strip(),
        "offer_brief": (f.get("offer_brief") or "").strip() or None,
        "daily_scrape_target": int(f.get("daily_scrape_target") or 50),
        "active": f.get("active", "true") == "true",
    })
    return redirect(url_for("home"))


@app.route("/campaigns/<int:cid>/toggle", methods=["POST"])
def toggle_campaign(cid: int):
    rows = sb.select("campaigns", {"select": "active", "id": f"eq.{cid}"}, limit=1)
    current = rows[0]["active"] if rows else False
    sb.update("campaigns", {"id": cid}, {"active": not current})
    return redirect(request.referrer or url_for("home"))


@app.route("/campaigns/<int:cid>/scrape", methods=["POST"])
def scrape_now(cid: int):
    import requests as _r
    secret = os.environ.get("CRON_SECRET", "")
    try:
        _r.get(f"{PROD_URL}/api/cron/daily_scrape",
               params={"campaign_id": cid},
               headers={"Authorization": f"Bearer {secret}"}, timeout=5)
    except Exception:
        pass
    return redirect(request.referrer or url_for("home"))


@app.route("/campaigns/<int:cid>")
def campaign_detail(cid: int):
    rows = sb.select("campaigns", {"select": "*", "id": f"eq.{cid}"}, limit=1)
    if not rows:
        return ("Not found", 404)
    c = rows[0]
    leads = sb.select(
        "leads",
        {"select": "*", "campaign_id": f"eq.{cid}", "order": "created_at.desc"},
        limit=200,
    )
    runs = sb.select(
        "scrape_runs",
        {"select": "*", "campaign_id": f"eq.{cid}", "order": "started_at.desc"},
        limit=20,
    )

    leads_rows = "".join([f"""
      <tr class="border-b border-zinc-800 hover:bg-zinc-900/40">
        <td class="py-2.5 px-4">{r['business']}</td>
        <td class="py-2.5 px-4 text-zinc-400 text-xs">{(r.get('email') or '—')}</td>
        <td class="py-2.5 px-4">{pill(r['enrichment_status'], r['enrichment_status'])}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-500 text-xs">{r['created_at'][:16].replace('T',' ')}</td>
      </tr>""" for r in leads])

    runs_rows = "".join([f"""
      <tr class="border-b border-zinc-800 hover:bg-zinc-900/40">
        <td class="py-2.5 px-4 num-mono text-zinc-300 text-xs">{r['started_at'][:16].replace('T',' ')}</td>
        <td class="py-2.5 px-4">{pill(r['status'], r['status'])}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-300">{r['scraped_count']} / {r.get('target_count') or '—'}</td>
        <td class="py-2.5 px-4 text-zinc-500 text-xs">{(r.get('error') or '')[:60]}</td>
      </tr>""" for r in runs])

    body = f"""
    <a href="/" class="text-xs text-zinc-500 hover:text-zinc-300 inline-flex items-center gap-1 mb-3"><i data-lucide="arrow-left" class="w-3 h-3"></i>Dashboard</a>
    <div class="flex items-start justify-between mb-6">
      <div>
        <h2 class="text-xl font-semibold">{c['name']}</h2>
        <div class="text-sm text-zinc-400 mt-1">{c['niche']} · {c['region']}</div>
      </div>
      <div class="flex items-center gap-2">
        {pill('active' if c['active'] else 'paused', 'active' if c['active'] else 'paused')}
        <form method="post" action="/campaigns/{c['id']}/toggle" class="inline">
          <button class="text-xs px-2.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700">{'Pause' if c['active'] else 'Resume'}</button>
        </form>
        <form method="post" action="/campaigns/{c['id']}/scrape" class="inline">
          <button class="text-xs px-2.5 py-1 rounded-md bg-brand hover:bg-brand-600 text-white inline-flex items-center gap-1"><i data-lucide="play" class="w-3 h-3"></i>Scrape now</button>
        </form>
      </div>
    </div>

    {f'<div class="bg-zinc-900 border border-zinc-800 rounded-lg p-4 mb-6"><div class="text-[11px] uppercase tracking-wider text-zinc-500 mb-2">Offer brief</div><pre class="text-xs text-zinc-300 whitespace-pre-wrap font-mono">{c.get("offer_brief") or "—"}</pre></div>' if c.get('offer_brief') else ''}

    <div class="grid lg:grid-cols-2 gap-6">
      <div>
        <h3 class="text-sm font-medium text-zinc-200 mb-3">Leads ({len(leads)})</h3>
        <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          {('<table class="w-full text-sm"><thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500"><tr><th class="text-left py-2.5 px-4 font-medium">Business</th><th class="text-left py-2.5 px-4 font-medium">Email</th><th class="text-left py-2.5 px-4 font-medium">Status</th><th class="text-left py-2.5 px-4 font-medium">Added</th></tr></thead><tbody>' + leads_rows + '</tbody></table>') if leads else empty_state('users', 'No leads yet', 'Click "Scrape now" to populate this campaign with leads from Google Places.')}
        </div>
      </div>
      <div>
        <h3 class="text-sm font-medium text-zinc-200 mb-3">Scrape runs ({len(runs)})</h3>
        <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
          {('<table class="w-full text-sm"><thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500"><tr><th class="text-left py-2.5 px-4 font-medium">Started</th><th class="text-left py-2.5 px-4 font-medium">Status</th><th class="text-left py-2.5 px-4 font-medium">Scraped</th><th class="text-left py-2.5 px-4 font-medium">Error</th></tr></thead><tbody>' + runs_rows + '</tbody></table>') if runs else empty_state('history', 'No runs yet', 'Scrape runs will appear here after the daily cron fires or you click "Scrape now".')}
        </div>
      </div>
    </div>
    """
    return page("campaigns", c["name"], body, page_title=c["name"])


@app.route("/leads")
def leads():
    rows = sb.select(
        "leads",
        {"select": "id,business,website,email,email_status,intent_score,enrichment_status,created_at,campaign_id", "order": "created_at.desc"},
        limit=300,
    )
    if not rows:
        body = empty_state("users", "No leads yet", "Create a campaign and run a scrape to populate leads.",
                          '<a href="/" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium bg-brand hover:bg-brand-600 text-white"><i data-lucide="arrow-left" class="w-3.5 h-3.5"></i>Go to Dashboard</a>')
        return page("leads", "Leads", body)

    tr = "".join([f"""
      <tr class="border-b border-zinc-800 hover:bg-zinc-900/40">
        <td class="py-2.5 px-4">
          <div class="font-medium text-zinc-100">{r['business']}</div>
          {f'<a href="{r["website"]}" target="_blank" class="text-[11px] text-zinc-500 hover:text-brand-500 inline-flex items-center gap-1"><i data-lucide="external-link" class="w-3 h-3"></i>{r["website"][:50]}</a>' if r.get('website') else '<span class="text-[11px] text-zinc-600">no website</span>'}
        </td>
        <td class="py-2.5 px-4 num-mono text-xs text-zinc-300">{r.get('email') or '<span class="text-zinc-600">—</span>'}</td>
        <td class="py-2.5 px-4">{pill(r.get('email_status') or '—', r.get('email_status') or 'neutral')}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-300">{r.get('intent_score') if r.get('intent_score') is not None else '<span class="text-zinc-600">—</span>'}</td>
        <td class="py-2.5 px-4">{pill(r['enrichment_status'], r['enrichment_status'])}</td>
        <td class="py-2.5 px-4 num-mono text-xs text-zinc-500">{r['created_at'][:10]}</td>
      </tr>""" for r in rows])

    body = f"""
    <div class="flex items-center justify-between mb-4">
      <div>
        <h2 class="text-xl font-semibold">Leads</h2>
        <div class="text-xs text-zinc-500 mt-0.5">{len(rows)} leads · most recent first</div>
      </div>
    </div>
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500">
          <tr>
            <th class="text-left py-3 px-4 font-medium">Business</th>
            <th class="text-left py-3 px-4 font-medium">Email</th>
            <th class="text-left py-3 px-4 font-medium">Verify</th>
            <th class="text-left py-3 px-4 font-medium">Intent</th>
            <th class="text-left py-3 px-4 font-medium">Enrichment</th>
            <th class="text-left py-3 px-4 font-medium">Added</th>
          </tr>
        </thead>
        <tbody>{tr}</tbody>
      </table>
    </div>
    """
    return page("leads", "Leads", body)


@app.route("/sequences")
def sequences():
    rows = sb.select(
        "sequences",
        {"select": "id,lead_id,status,current_step,opens,clicks,replied,bounced,next_send_at,paused_reason", "order": "updated_at.desc"},
        limit=300,
    )
    if not rows:
        body = empty_state("send", "No sequences yet", "Sequences are created automatically after enrichment completes. Build phases 3+4 to start the email loop.")
        return page("sequences", "Sequences", body)

    tr = "".join([f"""
      <tr class="border-b border-zinc-800 hover:bg-zinc-900/40">
        <td class="py-2.5 px-4 num-mono text-zinc-300">#{r['id']}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-400">#{r['lead_id']}</td>
        <td class="py-2.5 px-4">{pill(r['status'], r['status'])}</td>
        <td class="py-2.5 px-4 num-mono">{r['current_step']}</td>
        <td class="py-2.5 px-4 num-mono">{r['opens']}</td>
        <td class="py-2.5 px-4 num-mono">{r['clicks']}</td>
        <td class="py-2.5 px-4">{'✓' if r['replied'] else '<span class="text-zinc-600">—</span>'}</td>
        <td class="py-2.5 px-4">{'✗' if r['bounced'] else '<span class="text-zinc-600">—</span>'}</td>
        <td class="py-2.5 px-4 num-mono text-xs text-zinc-400">{(r.get('next_send_at') or '')[:16].replace('T',' ')}</td>
        <td class="py-2.5 px-4 text-xs text-zinc-500">{r.get('paused_reason') or ''}</td>
      </tr>""" for r in rows])

    body = f"""
    <div class="flex items-center justify-between mb-4">
      <div>
        <h2 class="text-xl font-semibold">Sequences</h2>
        <div class="text-xs text-zinc-500 mt-0.5">{len(rows)} sequences · most recent activity first</div>
      </div>
    </div>
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500">
          <tr>
            <th class="text-left py-3 px-4 font-medium">ID</th>
            <th class="text-left py-3 px-4 font-medium">Lead</th>
            <th class="text-left py-3 px-4 font-medium">Status</th>
            <th class="text-left py-3 px-4 font-medium">Step</th>
            <th class="text-left py-3 px-4 font-medium">Opens</th>
            <th class="text-left py-3 px-4 font-medium">Clicks</th>
            <th class="text-left py-3 px-4 font-medium">Replied</th>
            <th class="text-left py-3 px-4 font-medium">Bounced</th>
            <th class="text-left py-3 px-4 font-medium">Next send</th>
            <th class="text-left py-3 px-4 font-medium">Paused reason</th>
          </tr>
        </thead>
        <tbody>{tr}</tbody>
      </table>
    </div>
    """
    return page("sequences", "Sequences", body)


@app.route("/events")
def events():
    rows = sb.select(
        "sequence_events",
        {"select": "id,sequence_id,step,event_type,resend_id,ts", "order": "ts.desc"},
        limit=300,
    )
    if not rows:
        body = empty_state("activity", "No events yet", "Events appear here as emails are sent, opened, clicked, or bounced.")
        return page("events", "Events", body)

    tr = "".join([f"""
      <tr class="border-b border-zinc-800 hover:bg-zinc-900/40">
        <td class="py-2.5 px-4 num-mono text-xs text-zinc-400">{r['ts'][:19].replace('T', ' ')}</td>
        <td class="py-2.5 px-4">{pill(r['event_type'], r['event_type'])}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-300">{r.get('sequence_id') or '<span class="text-zinc-600">—</span>'}</td>
        <td class="py-2.5 px-4 num-mono text-zinc-400">{r.get('step') or ''}</td>
        <td class="py-2.5 px-4 num-mono text-xs text-zinc-500">{(r.get('resend_id') or '')[:32]}</td>
      </tr>""" for r in rows])

    body = f"""
    <div class="flex items-center justify-between mb-4">
      <div>
        <h2 class="text-xl font-semibold">Events</h2>
        <div class="text-xs text-zinc-500 mt-0.5">{len(rows)} recent events · live activity stream</div>
      </div>
    </div>
    <div class="bg-zinc-900 border border-zinc-800 rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-zinc-900 border-b border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500">
          <tr>
            <th class="text-left py-3 px-4 font-medium">When (UTC)</th>
            <th class="text-left py-3 px-4 font-medium">Event</th>
            <th class="text-left py-3 px-4 font-medium">Sequence</th>
            <th class="text-left py-3 px-4 font-medium">Step</th>
            <th class="text-left py-3 px-4 font-medium">Resend ID</th>
          </tr>
        </thead>
        <tbody>{tr}</tbody>
      </table>
    </div>
    """
    return page("events", "Events", body)
