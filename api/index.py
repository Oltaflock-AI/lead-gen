"""Lead-gen autopilot dashboard. Vercel Flask. Light + dark, Tailwind CDN.

Routes:
  GET  /                         dashboard — KPIs + 7d chart + campaigns
  POST /campaigns                create
  POST /campaigns/<id>/toggle    pause/resume scraping
  POST /campaigns/<id>/scrape    manual scrape now
  POST /campaigns/<id>/start     activate + kick the sequencer (start outreach)
  GET  /campaigns/<id>           detail
  GET  /leads /sequences /events list views
  POST /sequences/<id>/pause | /resume
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

# ─── Semantic theme classes (light default + dark: variant) ───
BG        = "bg-zinc-50 dark:bg-zinc-950"
SURFACE   = "bg-white dark:bg-zinc-900"
BORDER    = "border-zinc-200 dark:border-zinc-800"
TEXT      = "text-zinc-900 dark:text-zinc-100"
MUTED     = "text-zinc-500 dark:text-zinc-400"
SUBTLE    = "text-zinc-400 dark:text-zinc-600"
HOVER_ROW = "hover:bg-zinc-50 dark:hover:bg-zinc-800/40"
INPUT     = ("w-full bg-zinc-50 dark:bg-zinc-950 border border-zinc-300 dark:border-zinc-800 "
             "rounded-lg px-3 py-2 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 focus:outline-none")
THEAD     = "bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800 text-[11px] uppercase tracking-wider text-zinc-500 dark:text-zinc-400"


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


# ─────────── Data ───────────
def _events_window(hours: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return sb.select("sequence_events", {"select": "event_type,ts", "ts": f"gte.{since}", "order": "ts.desc"}, limit=20000)


def _kpi_24h() -> dict:
    b = defaultdict(int)
    for r in _events_window(24):
        b[r["event_type"]] += 1
    return {k: b[k] for k in ("sent", "delivered", "opened", "clicked", "replied", "bounced")}


def _series_7d() -> dict:
    days = defaultdict(lambda: defaultdict(int))
    for r in _events_window(24 * 7):
        days[r["ts"][:10]][r["event_type"]] += 1
    labels, sent, opened = [], [], []
    for i in range(6, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d[5:]); sent.append(days[d]["sent"]); opened.append(days[d]["opened"])
    return {"labels": labels, "sent": sent, "opened": opened}


def _campaign_counts() -> dict[int, dict]:
    out = defaultdict(lambda: {"leads": 0, "pending": 0, "enriched": 0})
    for r in sb.select("leads", {"select": "campaign_id,enrichment_status"}, limit=100000):
        cid = r["campaign_id"]
        if cid is None:
            continue
        out[cid]["leads"] += 1
        st = r["enrichment_status"]
        if st in ("pending", "enriched"):
            out[cid][st] += 1
    return out


def _campaigns() -> list[dict]:
    return sb.select("campaigns", {"select": "*", "order": "created_at.desc"}, limit=200)


# ─────────── Components ───────────
def pill(text: str, kind: str = "neutral") -> str:
    p = {
        "neutral":  "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700",
        "active":   "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-500/30",
        "paused":   "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-500/30",
        "pending":  "bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-500/30",
        "enriched": "bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-500/30",
        "done":     "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700",
        "failed":   "bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-500/30",
        "bounced":  "bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-500/30",
        "completed":"bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-500/30",
        "running":  "bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-500/30",
        "sent":     "bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-500/30",
        "opened":   "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-500/30",
        "clicked":  "bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-500/30",
        "delivered":"bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700",
        "replied":  "bg-emerald-100 dark:bg-emerald-500/20 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-500/40",
    }.get(kind, None)
    if p is None:
        p = "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700"
    return f'<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border {p}">{text}</span>'


def kpi_card(label: str, value, sub: str = "") -> str:
    return f"""
    <div class="{SURFACE} border {BORDER} rounded-xl p-4">
      <div class="text-[11px] uppercase tracking-wider {MUTED}">{label}</div>
      <div class="mt-2 text-2xl font-semibold num-mono {TEXT}">{value}</div>
      {f'<div class="mt-1 text-xs {MUTED}">{sub}</div>' if sub else ''}
    </div>"""


def empty_state(icon: str, title: str, message: str, cta: str = "") -> str:
    return f"""
    <div class="text-center py-16">
      <div class="w-12 h-12 rounded-full bg-zinc-100 dark:bg-zinc-800 border {BORDER} mx-auto flex items-center justify-center mb-4">
        <i data-lucide="{icon}" class="w-5 h-5 {MUTED}"></i>
      </div>
      <div class="text-sm font-medium {TEXT}">{title}</div>
      <div class="text-xs {MUTED} mt-1 max-w-md mx-auto">{message}</div>
      {f'<div class="mt-6">{cta}</div>' if cta else ''}
    </div>"""


def table(headers: list[str], rows_html: str) -> str:
    ths = "".join(f'<th class="text-left py-3 px-4 font-medium">{h}</th>' for h in headers)
    return f"""
    <div class="{SURFACE} border {BORDER} rounded-xl overflow-hidden">
      <table class="w-full text-sm">
        <thead class="{THEAD}"><tr>{ths}</tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>"""


def page(active: str, title: str, body: str, page_title: str | None = None) -> str:
    page_title = page_title or title
    items = [("home", "/", "Dashboard", "layout-dashboard"),
             ("leads", "/leads", "Leads", "users"),
             ("sequences", "/sequences", "Sequences", "send"),
             ("events", "/events", "Events", "activity")]
    nav = ""
    for key, href, label, icon in items:
        on = (key == active)
        cls = ("bg-brand text-white" if on
               else f"{MUTED} hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-100 dark:hover:bg-zinc-800/60")
        nav += f'<a href="{href}" class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition {cls}"><i data-lucide="{icon}" class="w-4 h-4"></i><span>{label}</span></a>'

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
"""<!doctype html><html lang="en" class="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + page_title + """ · Lead-gen</title>
<script>(function(){try{var t=localStorage.getItem('theme');if(t==='light'){document.documentElement.classList.remove('dark');}else{document.documentElement.classList.add('dark');}}catch(e){}})();</script>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
tailwind.config={darkMode:'class',theme:{extend:{
  fontFamily:{sans:['InterVariable','Inter','ui-sans-serif','system-ui','sans-serif'],mono:['JetBrains Mono','ui-monospace','monospace']},
  colors:{brand:{DEFAULT:'#6366f1',500:'#6366f1',600:'#4f46e5',700:'#4338ca'}}
}}};
</script>
<link rel="preconnect" href="https://rsms.me/"><link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<style>
  :root{font-size:15px}
  body{font-feature-settings:'cv02','cv03','cv04','cv11','ss01';-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;letter-spacing:-0.006em;line-height:1.55}
  .num-mono{font-variant-numeric:tabular-nums;font-family:'JetBrains Mono',ui-monospace,monospace;letter-spacing:0}
  ::-webkit-scrollbar{width:9px;height:9px}
  ::-webkit-scrollbar-thumb{background:#a1a1aa55;border-radius:5px}
  .dark ::-webkit-scrollbar-thumb{background:#3f3f46}
  table td{padding-top:.7rem;padding-bottom:.7rem}
</style></head>
<body class=\"""" + f"{BG} {TEXT}" + """ antialiased\">
<div class="min-h-screen flex">
  <aside class=\"""" + f"w-60 border-r {BORDER} {SURFACE}" + """ flex flex-col fixed h-screen\">
    <div class=\"""" + f"px-4 py-5 border-b {BORDER}" + """ flex items-center gap-2.5\">
      <div class="w-8 h-8 rounded-lg bg-brand flex items-center justify-center"><i data-lucide="zap" class="w-4 h-4 text-white"></i></div>
      <div><div class="text-sm font-semibold">Lead-gen</div><div class=\"""" + f"text-[11px] {MUTED}" + """ -mt-0.5\">autopilot · oltaflock</div></div>
    </div>
    <nav class="flex-1 px-3 py-4 space-y-1">""" + nav + """</nav>
    <div class=\"""" + f"px-3 py-3 border-t {BORDER} text-[11px] {MUTED}" + """\">
      <div class="flex items-center gap-2"><span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>autopilot live</div>
    </div>
  </aside>
  <div class="flex-1 flex flex-col min-w-0 ml-60">
    <header class=\"""" + f"h-14 border-b {BORDER}" + """ flex items-center justify-between px-6 sticky top-0 """ + f"{BG}/80" + """ backdrop-blur z-10\">
      <h1 class=\"""" + f"text-sm font-medium {MUTED}" + """\">""" + title + """</h1>
      <div class="flex items-center gap-3">
        <span class=\"""" + f"text-xs {SUBTLE}" + """ num-mono\">""" + now + """</span>
        <button onclick="toggleTheme()" class=\"""" + f"p-2 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 {MUTED}" + """\" title="Toggle theme">
          <i data-lucide="sun" class="w-4 h-4 hidden dark:block"></i>
          <i data-lucide="moon" class="w-4 h-4 block dark:hidden"></i>
        </button>
      </div>
    </header>
    <main class="flex-1 p-6 overflow-x-auto">""" + body + """</main>
  </div>
</div>
<script>
function toggleTheme(){var d=document.documentElement.classList.toggle('dark');try{localStorage.setItem('theme',d?'dark':'light');}catch(e){}lucide.createIcons();}
lucide.createIcons();
</script>
</body></html>""")


# ─────────── Routes ───────────
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "lead-gen-dashboard"})


@app.route("/")
def home():
    k = _kpi_24h(); s = _series_7d(); camps = _campaigns(); counts = _campaign_counts()
    orate = round(100 * k["opened"] / k["delivered"]) if k["delivered"] else 0
    crate = round(100 * k["clicked"] / k["delivered"]) if k["delivered"] else 0
    kpis = "".join([
        kpi_card("Sent · 24h", k["sent"]), kpi_card("Delivered", k["delivered"]),
        kpi_card("Opened", k["opened"], f"{orate}% open rate"),
        kpi_card("Clicked", k["clicked"], f"{crate}% click rate"),
        kpi_card("Replied", k["replied"]), kpi_card("Bounced", k["bounced"]),
    ])

    if camps:
        rh = ""
        for c in camps:
            cnt = counts.get(c["id"], {"leads": 0, "pending": 0, "enriched": 0})
            rh += f"""
            <tr class="border-b {BORDER} {HOVER_ROW} transition">
              <td class="px-4"><a href="/campaigns/{c['id']}" class="font-medium hover:text-brand-500">{c['name']}</a>
                <div class="text-[11px] {MUTED} mt-0.5">{c['niche']} · {c['region']}</div></td>
              <td class="px-4">{pill('active' if c['active'] else 'paused', 'active' if c['active'] else 'paused')}</td>
              <td class="px-4 num-mono">{cnt['leads']}</td>
              <td class="px-4 num-mono {MUTED}">{cnt['enriched']}</td>
              <td class="px-4 num-mono">{c['daily_scrape_target']}</td>
              <td class="px-4 text-right whitespace-nowrap">
                <form method="post" action="/campaigns/{c['id']}/scrape" class="inline"><button class="text-xs px-2.5 py-1 rounded-md bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 border {BORDER}">Scrape</button></form>
                <form method="post" action="/campaigns/{c['id']}/start" class="inline ml-1"><button class="text-xs px-2.5 py-1 rounded-md bg-brand hover:bg-brand-600 text-white inline-flex items-center gap-1"><i data-lucide="rocket" class="w-3 h-3"></i>Start outreach</button></form>
              </td>
            </tr>"""
        camp_block = table(["Campaign", "Status", "Leads", "Enriched", "Daily target", ""], rh)
    else:
        camp_block = empty_state("rocket", "No campaigns yet",
            "Create your first campaign to start the autopilot.",
            '<button onclick="cm.showModal()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-brand hover:bg-brand-600 text-white"><i data-lucide="plus" class="w-3.5 h-3.5"></i>Create campaign</button>')

    body = f"""
    <div class="flex items-center justify-between mb-6">
      <div><h2 class="text-xl font-semibold">Dashboard</h2><div class="text-xs {MUTED} mt-0.5">Live metrics from the autopilot loop</div></div>
      <button onclick="cm.showModal()" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-brand hover:bg-brand-600 text-white"><i data-lucide="plus" class="w-3.5 h-3.5"></i>New campaign</button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">{kpis}</div>
    <div class="{SURFACE} border {BORDER} rounded-xl p-4 mb-6">
      <div class="flex items-center justify-between mb-3">
        <div><div class="text-sm font-medium">Last 7 days</div><div class="text-[11px] {MUTED}">Sent vs Opened</div></div>
        <div class="flex items-center gap-3 text-xs {MUTED}"><span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-sky-400"></span>Sent</span><span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400"></span>Opened</span></div>
      </div>
      <canvas id="chart" height="78"></canvas>
    </div>
    <div class="flex items-center justify-between mb-3"><h3 class="text-sm font-medium">Campaigns</h3><a href="/leads" class="text-xs {MUTED} hover:text-brand-500 inline-flex items-center gap-1">All leads <i data-lucide="arrow-right" class="w-3 h-3"></i></a></div>
    {camp_block}

    <dialog id="cm" class="{SURFACE} {TEXT} border {BORDER} rounded-2xl p-0 backdrop:bg-zinc-950/60 max-w-lg w-full">
      <form method="post" action="/campaigns" class="p-6">
        <div class="flex items-center justify-between mb-4"><h3 class="text-base font-semibold">New campaign</h3><button type="button" onclick="cm.close()" class="{MUTED} hover:text-brand-500"><i data-lucide="x" class="w-4 h-4"></i></button></div>
        <div class="space-y-3 text-sm">
          <div><label class="block text-xs {MUTED} mb-1">Name (slug)</label><input name="name" required placeholder="nz-real-estate" class="{INPUT}"></div>
          <div class="grid grid-cols-2 gap-3">
            <div><label class="block text-xs {MUTED} mb-1">Niche</label><input name="niche" required placeholder="real estate agencies" class="{INPUT}"></div>
            <div><label class="block text-xs {MUTED} mb-1">Region</label><input name="region" required placeholder="Auckland, New Zealand" class="{INPUT}"></div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div><label class="block text-xs {MUTED} mb-1">Daily target</label><input name="daily_scrape_target" type="number" value="50" class="{INPUT} num-mono"></div>
            <div><label class="block text-xs {MUTED} mb-1">Status</label><select name="active" class="{INPUT}"><option value="true">Active</option><option value="false">Paused</option></select></div>
          </div>
          <div><label class="block text-xs {MUTED} mb-1">Offer brief <span class="{SUBTLE}">(what you pitch)</span></label><textarea name="offer_brief" rows="6" placeholder="Who, what pain, what outcome — then the offer." class="{INPUT} font-mono text-xs"></textarea></div>
        </div>
        <div class="flex justify-end gap-2 mt-6 pt-4 border-t {BORDER}"><button type="button" onclick="cm.close()" class="px-3 py-1.5 rounded-lg text-xs font-medium {MUTED} hover:bg-zinc-100 dark:hover:bg-zinc-800">Cancel</button><button type="submit" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand hover:bg-brand-600 text-white">Create</button></div>
      </form>
    </dialog>
    <script>
      const ctx=document.getElementById('chart');
      if(ctx){{new Chart(ctx,{{type:'line',data:{{labels:{s['labels']},datasets:[
        {{label:'Sent',data:{s['sent']},borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,0.10)',tension:.35,fill:true,borderWidth:2,pointRadius:3,pointBackgroundColor:'#38bdf8'}},
        {{label:'Opened',data:{s['opened']},borderColor:'#34d399',backgroundColor:'rgba(52,211,153,0.10)',tension:.35,fill:true,borderWidth:2,pointRadius:3,pointBackgroundColor:'#34d399'}}]}},
        options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{grid:{{display:false}},ticks:{{color:'#a1a1aa',font:{{size:11}}}}}},y:{{grid:{{color:'#88888822'}},ticks:{{color:'#a1a1aa',font:{{size:11}},precision:0}}}}}}}}}});}}
    </script>
    """
    return page("home", "Dashboard", body)


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    f = request.form
    sb.insert("campaigns", {
        "name": f["name"].strip(), "niche": f["niche"].strip(), "region": f["region"].strip(),
        "offer_brief": (f.get("offer_brief") or "").strip() or None,
        "daily_scrape_target": int(f.get("daily_scrape_target") or 50),
        "active": f.get("active", "true") == "true",
    })
    return redirect(url_for("home"))


def _fire_cron(path: str, **params):
    import requests as _r
    try:
        _r.get(f"{PROD_URL}{path}", params=params,
               headers={"Authorization": f"Bearer {os.environ.get('CRON_SECRET','')}"}, timeout=4)
    except Exception:
        pass


@app.route("/campaigns/<int:cid>/toggle", methods=["POST"])
def toggle_campaign(cid: int):
    rows = sb.select("campaigns", {"select": "active", "id": f"eq.{cid}"}, limit=1)
    cur = rows[0]["active"] if rows else False
    sb.update("campaigns", {"id": cid}, {"active": not cur})
    return redirect(request.referrer or url_for("home"))


@app.route("/campaigns/<int:cid>/scrape", methods=["POST"])
def scrape_now(cid: int):
    _fire_cron("/api/cron/daily_scrape", campaign_id=cid)
    return redirect(request.referrer or url_for("home"))


@app.route("/campaigns/<int:cid>/start", methods=["POST"])
def start_outreach(cid: int):
    sb.update("campaigns", {"id": cid}, {"active": True})
    _fire_cron("/api/cron/sequencer_tick")  # auto-creates sequences for enriched leads + sends
    return redirect(request.referrer or url_for("home"))


@app.route("/campaigns/<int:cid>")
def campaign_detail(cid: int):
    rows = sb.select("campaigns", {"select": "*", "id": f"eq.{cid}"}, limit=1)
    if not rows:
        return ("Not found", 404)
    c = rows[0]
    leads = sb.select("leads", {"select": "*", "campaign_id": f"eq.{cid}", "order": "created_at.desc"}, limit=200)
    runs = sb.select("scrape_runs", {"select": "*", "campaign_id": f"eq.{cid}", "order": "started_at.desc"}, limit=20)

    lrows = "".join(f"""<tr class="border-b {BORDER} {HOVER_ROW}">
        <td class="px-4">{r['business']}</td>
        <td class="px-4 num-mono text-xs {MUTED}">{r.get('email') or '—'}</td>
        <td class="px-4">{pill(r['enrichment_status'], r['enrichment_status'])}</td>
        <td class="px-4 num-mono text-xs {SUBTLE}">{r['created_at'][:10]}</td></tr>""" for r in leads)
    rrows = "".join(f"""<tr class="border-b {BORDER} {HOVER_ROW}">
        <td class="px-4 num-mono text-xs">{r['started_at'][:16].replace('T',' ')}</td>
        <td class="px-4">{pill(r['status'], r['status'])}</td>
        <td class="px-4 num-mono">{r['scraped_count']} / {r.get('target_count') or '—'}</td>
        <td class="px-4 text-xs {MUTED}">{(r.get('error') or '')[:50]}</td></tr>""" for r in runs)

    offer_html = (f'<div class="{SURFACE} border {BORDER} rounded-xl p-4 mb-6"><div class="text-[11px] uppercase tracking-wider {MUTED} mb-2">Offer brief</div><pre class="text-xs whitespace-pre-wrap font-mono {TEXT}">{c.get("offer_brief")}</pre></div>') if c.get("offer_brief") else ""

    body = f"""
    <a href="/" class="text-xs {MUTED} hover:text-brand-500 inline-flex items-center gap-1 mb-3"><i data-lucide="arrow-left" class="w-3 h-3"></i>Dashboard</a>
    <div class="flex items-start justify-between mb-6">
      <div><h2 class="text-xl font-semibold">{c['name']}</h2><div class="text-sm {MUTED} mt-1">{c['niche']} · {c['region']}</div></div>
      <div class="flex items-center gap-2">{pill('active' if c['active'] else 'paused', 'active' if c['active'] else 'paused')}
        <form method="post" action="/campaigns/{c['id']}/scrape" class="inline"><button class="text-xs px-2.5 py-1 rounded-md bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 border {BORDER}">Scrape now</button></form>
        <form method="post" action="/campaigns/{c['id']}/start" class="inline"><button class="text-xs px-2.5 py-1 rounded-md bg-brand hover:bg-brand-600 text-white inline-flex items-center gap-1"><i data-lucide="rocket" class="w-3 h-3"></i>Start outreach</button></form>
      </div>
    </div>
    {offer_html}
    <div class="grid lg:grid-cols-2 gap-6">
      <div><h3 class="text-sm font-medium mb-3">Leads ({len(leads)})</h3>{table(['Business','Email','Status','Added'], lrows) if leads else empty_state('users','No leads yet','Click "Scrape now" to populate from Google Places.')}</div>
      <div><h3 class="text-sm font-medium mb-3">Scrape runs ({len(runs)})</h3>{table(['Started','Status','Scraped','Error'], rrows) if runs else empty_state('history','No runs yet','Runs appear after the daily cron or a manual scrape.')}</div>
    </div>"""
    return page("home", c["name"], body, page_title=c["name"])


@app.route("/leads")
def leads():
    rows = sb.select("leads", {"select": "id,business,website,email,email_status,intent_score,enrichment_status,created_at", "order": "created_at.desc"}, limit=300)
    if not rows:
        return page("leads", "Leads", empty_state("users", "No leads yet", "Create a campaign and run a scrape.",
            f'<a href="/" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-brand hover:bg-brand-600 text-white">Go to Dashboard</a>'))
    tr = "".join(f"""<tr class="border-b {BORDER} {HOVER_ROW}">
        <td class="px-4"><div class="font-medium">{r['business']}</div>{f'<a href="{r["website"]}" target=_blank class="text-[11px] {MUTED} hover:text-brand-500">{r["website"][:48]}</a>' if r.get('website') else f'<span class="text-[11px] {SUBTLE}">no website</span>'}</td>
        <td class="px-4 num-mono text-xs">{r.get('email') or f'<span class="{SUBTLE}">—</span>'}</td>
        <td class="px-4">{pill(r.get('email_status') or '—', r.get('email_status') or 'neutral')}</td>
        <td class="px-4 num-mono">{r.get('intent_score') if r.get('intent_score') is not None else f'<span class="{SUBTLE}">—</span>'}</td>
        <td class="px-4">{pill(r['enrichment_status'], r['enrichment_status'])}</td>
        <td class="px-4 num-mono text-xs {SUBTLE}">{r['created_at'][:10]}</td></tr>""" for r in rows)
    body = f'<div class="mb-4"><h2 class="text-xl font-semibold">Leads</h2><div class="text-xs {MUTED} mt-0.5">{len(rows)} leads · newest first</div></div>' + table(["Business", "Email", "Verify", "Intent", "Enrichment", "Added"], tr)
    return page("leads", "Leads", body)


@app.route("/sequences")
def sequences():
    rows = sb.select("sequences", {"select": "id,lead_id,status,current_step,opens,clicks,replied,bounced,next_send_at,paused_reason", "order": "updated_at.desc"}, limit=300)
    if not rows:
        return page("sequences", "Sequences", empty_state("send", "No sequences yet", 'Click "Start outreach" on an active campaign to create sequences for its enriched leads.'))
    tr = ""
    for r in rows:
        act = ""
        if r["status"] == "active":
            act = f'<form method="post" action="/sequences/{r["id"]}/pause" class="inline"><button class="text-xs px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 border {BORDER}">Pause</button></form>'
        elif r["status"] == "paused":
            act = f'<form method="post" action="/sequences/{r["id"]}/resume" class="inline"><button class="text-xs px-2 py-0.5 rounded bg-brand hover:bg-brand-600 text-white">Resume</button></form>'
        tr += f"""<tr class="border-b {BORDER} {HOVER_ROW}">
          <td class="px-4 num-mono"><a href="/sequences/{r['id']}" class="hover:text-brand-500">#{r['id']}</a></td><td class="px-4 num-mono {MUTED}">#{r['lead_id']}</td>
          <td class="px-4">{pill(r['status'], r['status'])}</td><td class="px-4 num-mono">{r['current_step']}</td>
          <td class="px-4 num-mono">{r['opens']}</td><td class="px-4 num-mono">{r['clicks']}</td>
          <td class="px-4">{'✓' if r['replied'] else ''}</td>
          <td class="px-4 num-mono text-xs {MUTED}">{(r.get('next_send_at') or '')[:16].replace('T',' ')}</td>
          <td class="px-4 text-xs {MUTED}">{r.get('paused_reason') or ''}</td>
          <td class="px-4 text-right">{act}</td></tr>"""
    body = f'<div class="mb-4"><h2 class="text-xl font-semibold">Sequences</h2><div class="text-xs {MUTED} mt-0.5">{len(rows)} sequences</div></div>' + table(["ID", "Lead", "Status", "Step", "Opens", "Clicks", "Replied", "Next send", "Reason", ""], tr)
    return page("sequences", "Sequences", body)


@app.route("/sequences/<int:sid>")
def sequence_detail(sid: int):
    srows = sb.select("sequences", {"select": "*", "id": f"eq.{sid}"}, limit=1)
    if not srows:
        return ("Not found", 404)
    s = srows[0]
    lead = (sb.select("leads", {"select": "*", "id": f"eq.{s['lead_id']}"}, limit=1) or [{}])[0]
    drafts = sb.select("drafts", {"select": "*", "sequence_id": f"eq.{sid}", "order": "step.asc"}, limit=20)
    evs = sb.select("sequence_events", {"select": "*", "sequence_id": f"eq.{sid}", "order": "ts.desc"}, limit=100)

    # Email thread — one card per drafted step.
    thread = ""
    for d in drafts:
        thread += f"""
        <div class="{SURFACE} border {BORDER} rounded-xl p-4 mb-3">
          <div class="flex items-center justify-between mb-2">
            <div class="text-[11px] uppercase tracking-wider {MUTED}">Step {d['step']} · {d.get('angle') or ''}</div>
            <div class="text-[11px] {SUBTLE} num-mono">{d.get('model') or ''}</div>
          </div>
          <div class="font-medium mb-2">{d['subject']}</div>
          <pre class="text-xs whitespace-pre-wrap font-sans {MUTED} leading-relaxed">{d['body']}</pre>
        </div>"""
    if not thread:
        thread = empty_state("mail", "No drafts yet", "Drafts appear here as the sequencer sends each step.")

    timeline = "".join(f"""<div class="flex items-center gap-3 py-2 border-b {BORDER} last:border-0">
        <div class="num-mono text-xs {SUBTLE} w-36 shrink-0">{e['ts'][:19].replace('T',' ')}</div>
        <div>{pill(e['event_type'], e['event_type'])}</div>
        <div class="text-xs {MUTED}">{('step ' + str(e['step'])) if e.get('step') else ''}</div>
      </div>""" for e in evs) or f'<div class="text-xs {MUTED} py-4">No events yet.</div>'

    actions = ""
    if s["status"] == "active":
        actions += f'<form method="post" action="/sequences/{sid}/pause" class="inline"><button class="text-xs px-2.5 py-1 rounded-md bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 border {BORDER}">Pause</button></form>'
    elif s["status"] == "paused":
        actions += f'<form method="post" action="/sequences/{sid}/resume" class="inline"><button class="text-xs px-2.5 py-1 rounded-md bg-brand hover:bg-brand-600 text-white">Resume</button></form>'
    actions += f'<form method="post" action="/sequences/{sid}/replied" class="inline ml-1"><button class="text-xs px-2.5 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white inline-flex items-center gap-1"><i data-lucide="reply" class="w-3 h-3"></i>Mark replied</button></form>'

    body = f"""
    <a href="/sequences" class="text-xs {MUTED} hover:text-brand-500 inline-flex items-center gap-1 mb-3"><i data-lucide="arrow-left" class="w-3 h-3"></i>Sequences</a>
    <div class="flex items-start justify-between mb-6">
      <div>
        <h2 class="text-xl font-semibold">{lead.get('business') or 'Sequence #' + str(sid)}</h2>
        <div class="text-sm {MUTED} mt-1 num-mono">{lead.get('email') or ''}</div>
      </div>
      <div class="flex items-center gap-2">{pill(s['status'], s['status'])}{actions}</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      {kpi_card("Step", f"{s['current_step']} / 7")}
      {kpi_card("Opens", s['opens'])}
      {kpi_card("Clicks", s['clicks'])}
      {kpi_card("Next send", (s.get('next_send_at') or '—')[:16].replace('T',' '))}
    </div>
    <div class="grid lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2"><h3 class="text-sm font-medium mb-3">Email thread</h3>{thread}</div>
      <div><h3 class="text-sm font-medium mb-3">Activity</h3><div class="{SURFACE} border {BORDER} rounded-xl p-4">{timeline}</div></div>
    </div>"""
    return page("sequences", lead.get("business") or f"Sequence #{sid}", body, page_title=f"Sequence #{sid}")


@app.route("/sequences/<int:sid>/replied", methods=["POST"])
def mark_replied(sid: int):
    sb.update("sequences", {"id": sid}, {"replied": True, "status": "paused", "paused_reason": "replied", "next_send_at": None})
    sb.insert("sequence_events", {"sequence_id": sid, "event_type": "replied", "meta": {"via": "manual"}})
    return redirect(request.referrer or url_for("sequences"))


@app.route("/sequences/<int:sid>/pause", methods=["POST"])
def pause_seq(sid: int):
    sb.update("sequences", {"id": sid}, {"status": "paused", "paused_reason": "manual"})
    return redirect(request.referrer or url_for("sequences"))


@app.route("/sequences/<int:sid>/resume", methods=["POST"])
def resume_seq(sid: int):
    sb.update("sequences", {"id": sid}, {"status": "active", "paused_reason": None})
    return redirect(request.referrer or url_for("sequences"))


@app.route("/events")
def events():
    rows = sb.select("sequence_events", {"select": "id,sequence_id,step,event_type,resend_id,ts", "order": "ts.desc"}, limit=300)
    if not rows:
        return page("events", "Events", empty_state("activity", "No events yet", "Events stream in as emails are sent, opened, clicked, or bounced."))
    tr = "".join(f"""<tr class="border-b {BORDER} {HOVER_ROW}">
        <td class="px-4 num-mono text-xs {MUTED}">{r['ts'][:19].replace('T',' ')}</td>
        <td class="px-4">{pill(r['event_type'], r['event_type'])}</td>
        <td class="px-4 num-mono">{r.get('sequence_id') or ''}</td>
        <td class="px-4 num-mono {MUTED}">{r.get('step') or ''}</td>
        <td class="px-4 num-mono text-xs {SUBTLE}">{(r.get('resend_id') or '')[:30]}</td></tr>""" for r in rows)
    body = f'<div class="mb-4"><h2 class="text-xl font-semibold">Events</h2><div class="text-xs {MUTED} mt-0.5">{len(rows)} recent · live stream</div></div>' + table(["When (UTC)", "Event", "Sequence", "Step", "Resend ID"], tr)
    return page("events", "Events", body)
