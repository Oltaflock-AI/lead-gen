"""Lead-gen autopilot dashboard — warm editorial design (matches prototype).

Server-rendered Flask. Light (cream/Fraunces/forest-green) by default, with a
dark override via the theme toggle. All data from Supabase.

Pages:  /  /scrape  /offers  /leads  /sequences  /sequences/<id>  /settings
Actions: create campaign, scrape now, start outreach, pause/resume, mark replied
"""
import base64
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, url_for, jsonify, g

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from lib import supabase as sb
from lib import niches
from lib import sequence as seq
from lib import users

app = Flask(__name__)
ADMIN_KEY = os.environ.get("DASHBOARD_KEY", "")
PROD_URL = "https://lead-gen-fawn-seven.vercel.app"


def _logo_data_uri():
    """OltaFlock logo as a base64 data URI (avoids Vercel static-routing). Cached."""
    root = os.path.dirname(os.path.dirname(__file__))
    for p in (os.path.join(root, "OF_FAVICON.png"),
              os.path.join(root, "src", "web", "static", "img", "favicon.png")):
        try:
            with open(p, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            continue
    return ""


LOGO_URI = _logo_data_uri()

STEP_META = {  # step -> (day label, name)
    1: ("Day 0", "Cold"), 2: ("Day 3", "Bump"), 3: ("Day 7", "FOMO"),
    4: ("Day 11", "Loom"), 5: ("Day 16", "Math"), 6: ("Day 21", "Quirky"),
    7: ("Day 28", "Pizza"),
}
WINDOWS = {"today": None, "7d": 168, "30d": 720, "all": 24 * 365 * 5}


_PUBLIC_PATHS = {"/healthz", "/login", "/logout"}


@app.before_request
def _gate():
    if request.path in _PUBLIC_PATHS or request.path.startswith("/static"):
        return None
    uid = users.verify(request.cookies.get("uid"))
    if uid:
        g.user = users.get(uid)
        return None
    return redirect(url_for("login_page", next=request.full_path.rstrip("?")))


def _login_html(err: str = "", nxt: str = "/") -> str:
    import html as _html
    e = f'<div class="lerr">{_html.escape(err)}</div>' if err else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Lead-gen</title>
<style>
  :root{{--accent:#b9603a;--ink:#2a2724;--ink-mute:#8a8178;--line:#e7e1d8;--card:#fff;--bg:#f6f2ec}}
  *{{box-sizing:border-box}} body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink);display:flex;min-height:100vh;align-items:center;justify-content:center}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:34px 30px;width:340px;box-shadow:0 8px 30px rgba(0,0,0,.06)}}
  h1{{font-family:Georgia,serif;font-size:22px;font-weight:500;margin:0 0 4px}} .sub{{color:var(--ink-mute);font-size:13px;margin-bottom:22px}}
  label{{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-mute);margin:14px 0 5px}}
  input{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font-size:14px;background:#fdfbf8}}
  button{{width:100%;margin-top:20px;padding:11px;background:var(--accent);color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer}}
  .lerr{{background:#fdecea;color:#c0392b;font-size:13px;padding:8px 11px;border-radius:8px;margin-bottom:14px}}
</style></head><body>
  <form class="card" method="post" action="/login">
    <input type="hidden" name="next" value="{_html.escape(nxt)}">
    <h1>Lead-gen</h1><div class="sub">Sign in to continue</div>
    {e}
    <label>Username</label><input name="username" autofocus autocapitalize="off" autocomplete="username">
    <label>Password</label><input name="password" type="password" autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
</body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login_page():
    nxt = request.values.get("next") or "/"
    if not nxt.startswith("/"):
        nxt = "/"
    if request.method == "POST":
        u = (request.form.get("username") or "").strip().lower()
        if users.verify_login(u, request.form.get("password") or ""):
            resp = redirect(nxt)
            resp.set_cookie("uid", users.sign(u), max_age=86400 * 30,
                            httponly=True, samesite="Lax", secure=True)
            return resp
        return _login_html("Wrong username or password", nxt)
    return _login_html("", nxt)


@app.route("/logout")
def logout():
    resp = redirect(url_for("login_page"))
    resp.delete_cookie("uid")
    return resp


@app.route("/account")
def account_page():
    import html as _html
    me = getattr(g, "user", {}) or {}
    note = ""
    if request.args.get("saved"):
        note = '<div class="note-bar" style="border-color:var(--good);color:var(--good)">Password updated.</div>'
    elif request.args.get("err"):
        note = f'<div class="note-bar" style="border-color:var(--danger,#c33);color:var(--danger,#c33)">{_html.escape(request.args.get("err"))}</div>'
    body = f"""
    <div class="block" style="max-width:520px"><div class="block-head"><div><div class="block-title">Account</div><div class="block-sub">Signed in as {_html.escape(me.get('name',''))} ({_html.escape(me.get('id',''))})</div></div></div>
    <div class="block-body">
      {note}
      <form method="post" action="/account/password" style="max-width:380px">
        <div class="field"><label>Current password</label><input name="current" type="password" autocomplete="current-password" required></div>
        <div class="field"><label>New password <span style="text-transform:none;color:var(--ink-faint)">(min 8 chars)</span></label><input name="new" type="password" autocomplete="new-password" minlength="8" required></div>
        <div class="field"><label>Confirm new password</label><input name="confirm" type="password" autocomplete="new-password" minlength="8" required></div>
        <div style="margin-top:6px"><button class="btn primary" type="submit">Update password</button></div>
      </form>
    </div></div>"""
    return shell("account", "workspace / account", "Account", "Manage your login", body)


@app.route("/account/password", methods=["POST"])
def account_password():
    from urllib.parse import quote
    me = getattr(g, "user", {}) or {}
    uid = me.get("id", "")
    current = request.form.get("current") or ""
    new = request.form.get("new") or ""
    confirm = request.form.get("confirm") or ""
    if not users.verify_login(uid, current):
        return redirect("/account?err=" + quote("Current password is incorrect."))
    if new != confirm:
        return redirect("/account?err=" + quote("New passwords do not match."))
    ok, msg = users.set_password(uid, new)
    if not ok:
        return redirect("/account?err=" + quote(msg))
    return redirect("/account?saved=1")


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
.main{flex:1;margin-left:220px;padding:36px 56px 80px;max-width:1320px}
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
/* ── redesign: primary nav button, guided panels, inline help, wider layout ── */
.nav-item.primary{background:var(--accent);color:#fff;font-weight:600;padding:9px 12px;margin-bottom:2px;justify-content:flex-start;gap:9px}
.nav-item.primary:hover{background:var(--accent);opacity:.92}
.nav-item.primary svg{flex-shrink:0}
.nav-item.primary.active{background:var(--accent);color:#fff}
.nav-hint{font-size:11px;color:var(--ink-faint);padding:2px 8px 0;line-height:1.4}
.guide{background:var(--accent-soft);border:1px solid var(--accent);border-radius:12px;padding:22px 24px;margin-bottom:28px}
.guide-h{font-family:var(--font-serif);font-size:21px;font-weight:500;color:var(--accent);letter-spacing:-0.01em;margin-bottom:4px}
.guide-sub{font-size:13px;color:var(--ink-soft);margin-bottom:18px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:20px}
.step{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:15px 16px}
.step .n{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;font-family:var(--font-mono);font-size:11px;font-weight:600;margin-bottom:9px}
.step b{display:block;font-size:14px;color:var(--ink);margin-bottom:4px}
.step p{font-size:12.5px;color:var(--ink-mute);line-height:1.5}
.step code{font-family:var(--font-mono);font-size:11px;background:var(--bg-soft);padding:1px 5px;border-radius:4px;color:var(--ink-soft)}
.help{background:var(--info-soft);border:1px solid var(--info);border-left-width:3px;border-radius:var(--radius);padding:11px 15px;font-size:12.5px;color:var(--ink-soft);line-height:1.55;margin-bottom:18px;display:flex;gap:10px;align-items:flex-start}
.help svg{flex-shrink:0;margin-top:1px;color:var(--info)}
.help b{color:var(--ink)}
@media(min-width:1500px){.main{max-width:1440px}}
@media(max-width:980px){.steps{grid-template-columns:1fr}.kpi-grid{grid-template-columns:repeat(2,1fr)}}
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


def _metrics(events):
    """Per-recipient funnel. Dedupes by (sequence_id, step) so repeat opens,
    duplicate sends, and multi-fire webhooks never inflate a rate past 100%.
    Bot opens (opened_bot) are excluded from real opens."""
    uniq = defaultdict(set)
    raw_sent = 0
    for e in events:
        et = e["event_type"]
        if et == "sent":
            raw_sent += 1
        if et in ("opened_bot", "clicked_bot"):
            continue
        key = (e.get("sequence_id"), e.get("step"))
        uniq[et].add(key)
    m = {et: len(s) for et, s in uniq.items()}
    m["sent_raw"] = raw_sent  # actual emails sent (includes any duplicates)
    return m


def _rate(num, denom):
    """Percentage, clamped to [0, 100] so lagging webhooks can't break it."""
    if not denom:
        return 0.0
    return round(min(100.0, 100.0 * num / denom), 1)


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
def _replies_pending_count() -> int:
    """Count pending reply rows. Returns 0 if the table does not exist yet."""
    try:
        rows = sb.select("replies", {"select": "id", "status": "eq.pending"}, limit=200)
        return len(rows)
    except Exception:
        return 0


def _user_nav() -> str:
    u = getattr(g, "user", None)
    if not u:
        return ""
    name = u.get("name", u.get("id", ""))
    return (f'<a class="nav-item" href="/account">{name}</a>'
            f'<a class="nav-item" href="/logout">Sign out</a>')


def shell(active, crumb, h1, sub, body, badges=None):
    badges = badges or {}
    if "replies" not in badges:
        badges["replies"] = _replies_pending_count()
    out_b = f' <span class="badge">{badges["outreach"]}</span>' if badges.get("outreach") else ""
    seq_b = f' <span class="badge">{badges.get("sequences", 0)}</span>'
    send_icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>'
    nav = f"""
    <div class="nav-section">
      <a class="nav-item primary {'active' if active=='compose' else ''}" href="/compose">{send_icon} Send email</a>
      <div class="nav-hint">Write your copy, pick leads, send.</div>
    </div>
    <div class="nav-section"><div class="nav-label">Workspace</div>
      <a class="nav-item {'active' if active=='build' else ''}" href="/build">New campaign</a>
      <a class="nav-item {'active' if active=='campaigns' else ''}" href="/campaigns">Campaigns</a>
      <a class="nav-item {'active' if active=='blasts' else ''}" href="/blasts">Sent emails</a>
      <a class="nav-item {'active' if active=='replies' else ''} {'warn' if badges.get('replies') else ''}" href="/replies">Replies{f' <span class="badge">{badges["replies"]}</span>' if badges.get('replies') else ''}</a>
      <a class="nav-item {'active' if active=='leads' else ''}" href="/leads">Leads</a>
      <a class="nav-item {'active' if active=='dashboard' else ''}" href="/">Dashboard</a>
      <a class="nav-item {'active' if active=='events' else ''}" href="/events">Activity</a></div>
    <div class="nav-section"><div class="nav-label">Automation · optional</div>
      <a class="nav-item {'active' if active=='sequences' else ''}" href="/sequences">Auto follow-up{seq_b}</a>
      <a class="nav-item {'active' if active=='scrape' else ''}" href="/scrape">Find new leads</a>
      <a class="nav-item {'active' if active=='upload' else ''}" href="/upload">Upload CSV</a>
      <a class="nav-item {'active' if active=='offers' else ''}" href="/offers">Offers</a></div>
    <div class="nav-section"><div class="nav-label">System</div>
      <a class="nav-item {'active' if active=='settings' else ''}" href="/settings">Settings</a>
      {_user_nav()}</div>"""
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h1} · Lead-gen</title>
<link rel="icon" type="image/png" href="{LOGO_URI}"><link rel="apple-touch-icon" href="{LOGO_URI}">
<script>(function(){{try{{if(localStorage.getItem('theme')==='dark')document.documentElement.classList.add('dark');}}catch(e){{}}}})();</script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;450;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<aside class="sidebar">
  <div class="brand">
    {f'<img class="brand-mark" src="{LOGO_URI}" alt="OltaFlock" width="24" height="24">' if LOGO_URI else '<svg class="brand-mark" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2 L4 9 L12 7 L20 9 Z M4 11 L12 9 L20 11 L12 22 Z"/></svg>'}
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

    m = _metrics(ev)
    sent = m.get("sent", 0)            # unique recipients reached (deduped)
    sent_raw = m.get("sent_raw", 0)    # actual emails sent (incl. duplicates)
    deliv = m.get("delivered", 0)
    opened = m.get("opened", 0)
    clicked = m.get("clicked", 0)
    bounced = m.get("bounced", 0)
    replied = m.get("replied", 0)
    deliv_base = deliv or sent          # opens land before delivered webhook; fall back to sent
    drate = _rate(deliv, sent)
    orate = _rate(opened, deliv_base)
    crate = _rate(clicked, deliv_base)
    rrate = _rate(replied, sent)
    spark = _spark_14d()
    spark_max = max(spark + [1])
    pts = " ".join(f"{i*(200/13):.0f},{24 - (v/spark_max*22):.0f}" for i, v in enumerate(spark))

    drate_alert = sent and drate < 70
    wlabel = {"today": "Today", "7d": "7d", "30d": "30d", "all": "All"}
    pills = "".join(
        f'<a href="/?w={k}" class="window-pill {"active" if k==w else ""}">{wlabel[k]}</a>'
        for k in ["today", "7d", "30d", "all"])

    dup = sent_raw - sent
    sent_meta = f"{sent} recipient{'s' if sent != 1 else ''}"
    if dup > 0:
        sent_meta += f' · <span style="color:var(--warn)">{dup} duplicate</span>'
    kpis = f"""
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Total leads</div><div class="kpi-num">{total_leads}</div><div class="kpi-meta">across {n_camps} campaigns</div></div>
      <div class="kpi"><div class="kpi-label">Sent · {wlabel[w]}</div><div class="kpi-num">{sent_raw}</div><div class="kpi-meta">{sent_meta}</div>
        <svg viewBox="0 0 200 24" preserveAspectRatio="none" style="margin-top:10px;height:24px;width:100%">
          <polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="{pts}"/></svg></div>
      <div class="kpi {'alert-kpi' if drate_alert else ''}"><div class="kpi-label">Delivery rate</div><div class="kpi-num">{drate}<span class="unit">%</span></div><div class="kpi-meta">{deliv} of {sent} sent{' · ' + str(bounced) + ' bounced' if bounced else ''}</div></div>
      <div class="kpi"><div class="kpi-label">Open rate</div><div class="kpi-num">{orate}<span class="unit">%</span></div><div class="kpi-meta">{opened} opened of {deliv_base}</div></div>
      <div class="kpi"><div class="kpi-label">Click rate</div><div class="kpi-num">{crate}<span class="unit">%</span></div><div class="kpi-meta">{clicked} click{'s' if clicked!=1 else ''} of {deliv_base}</div></div>
      <div class="kpi"><div class="kpi-label">Reply rate</div><div class="kpi-num">{rrate}<span class="unit">%</span></div><div class="kpi-meta">{replied} repl{'y' if replied==1 else 'ies'} of {sent}</div></div>
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
        if et in ("opened_bot", "clicked_bot"):
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

    guide = f"""
    <div class="guide">
      <div class="guide-h">Send your copy to your leads in 3 steps</div>
      <div class="guide-sub">You have <strong>{total_leads}</strong> lead{'s' if total_leads!=1 else ''} loaded. To email them with your own copy, use <strong>Send email</strong> — it goes out as individual one-off emails, not the automated drip.</div>
      <div class="steps">
        <div class="step"><span class="n">1</span><b>Pick who gets it</b><p>On <strong>Send email</strong>, tick the businesses (or hit “select all”). Already on <strong>Leads</strong>? Select them there and click Compose.</p></div>
        <div class="step"><span class="n">2</span><b>Write or AI-draft</b><p>Paste your copy. Type <code>{{business}}</code> or <code>{{city}}</code> and each email is personalized. Or click <strong>AI draft</strong> for a first pass.</p></div>
        <div class="step"><span class="n">3</span><b>Send</b><p>Pick your From address + signature, hit send. Each lead gets their own email. Track opens/replies on the Dashboard.</p></div>
      </div>
      <a class="btn primary" href="/compose">Send an email now →</a>
    </div>"""
    body = f"""
    {guide}
    <div class="window-bar"><span>Window</span><div class="window-pills">{pills}</div></div>
    {kpis}
    <div class="block">
      <div class="inner-tabs">
        <button class="inner-tab active" onclick="showPanel('activity',this)">Activity <span class="count">{len([e for e in ev if e['event_type'] not in ('opened_bot','clicked_bot')])}</span></button>
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


# ---- CSV upload: bring-your-own leads (never scraped — see daily_scrape) ----
_CSV_FIELD_ALIASES = {
    "business": ("business", "company", "name", "business_name", "company_name",
                 "company_name_for_emails", "organisation", "organization",
                 "organization_name", "account", "account_name", "employer"),
    "website":  ("website", "url", "site", "web", "domain", "website_url",
                 "company_website", "company_url"),
    "email":    ("email", "email_address", "e_mail", "contact_email", "work_email",
                 "primary_email"),
    "phone":    ("phone", "telephone", "tel", "mobile", "phone_number",
                 "company_phone", "work_direct_phone", "corporate_phone"),
    "address":  ("address", "full_address", "street", "location", "company_address"),
    "city":     ("city", "town", "suburb", "company_city"),
    "country":  ("country", "nation", "company_country"),
    "contact":  ("first_name", "firstname", "first", "fname", "contact_first_name",
                 "given_name"),
    "seed_subject": ("subject", "seed_subject", "email_subject", "subject_line"),
    "seed_body":    ("body", "seed_body", "email_body", "message", "copy", "email_copy"),
}


def _norm_key(k: str) -> str:
    """Normalize a CSV header to snake_case: lower, strip, any run of
    non-alphanumerics (spaces, hyphens, slashes) -> single underscore. So
    'Company Name', 'company-name', 'E-mail' all collapse to alias keys."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", (k or "").strip().lower()).strip("_")


def _csv_pick(row_lc: dict, key: str) -> str:
    for alias in _CSV_FIELD_ALIASES[key]:
        v = row_lc.get(_norm_key(alias))
        if v and v.strip():
            return v.strip()
    return ""


def _csv_row_to_lead(raw: dict, campaign_id: int) -> dict | None:
    row_lc = {_norm_key(k): (v or "") for k, v in raw.items()}
    business = _csv_pick(row_lc, "business")
    if not business:
        return None
    email = _csv_pick(row_lc, "email").lower()
    signals = {"source": "csv"}
    contact = _csv_pick(row_lc, "contact")
    if contact:
        signals["contact_first_name"] = contact
    # Pre-written, hand-reviewed step-1 copy (optional). When present the
    # sequencer sends it verbatim as step 1 instead of drafting one. The
    # structured signature is appended at send time, so the body must NOT
    # carry its own signoff.
    seed_body = _csv_pick(row_lc, "seed_body")
    if seed_body:
        signals["seed_body"] = seed_body
        signals["seed_subject"] = _csv_pick(row_lc, "seed_subject")
        signals["seed_step"] = 1
    lead = {
        "campaign_id": campaign_id,
        "business": business,
        "website": _csv_pick(row_lc, "website") or None,
        "phone": _csv_pick(row_lc, "phone") or None,
        "address": _csv_pick(row_lc, "address") or None,
        "city": _csv_pick(row_lc, "city") or None,
        "country": _csv_pick(row_lc, "country") or None,
        "source": "csv",
        # A provided email is ready to send, so it skips the enrichment queue
        # (these are pre-verified imports, not scraped leads needing lookup).
        "enrichment_status": "enriched" if email else "pending",
        "signals": signals,
    }
    if email:
        lead["email"] = email
        lead["email_status"] = "provided"
    return lead


@app.route("/upload", methods=["GET", "POST"])
def upload_csv():
    if request.method == "POST":
        import csv as _csv, io as _io
        f = request.files.get("file")
        if not f or not f.filename:
            return redirect("/upload?err=nofile")
        region = (request.form.get("region") or "").strip() or "United States"
        name = (request.form.get("name") or "").strip() or _slug("csv", f.filename.rsplit(".", 1)[0])
        raw = f.read().decode("utf-8-sig", errors="replace")
        reader = _csv.DictReader(_io.StringIO(raw))
        iset = (request.form.get("instruction_set") or "").strip()
        seq_cfg = json.dumps({"instruction_set": iset}) if iset and iset != "default" else None
        camp_row = {
            "name": name, "niche": "csv-upload", "region": region,
            "offer_brief": (request.form.get("offer_brief") or "").strip() or None,
            "daily_scrape_target": 0,
            "active": False,
            "notes": json.dumps({"source": "csv"}),
        }
        if seq_cfg:
            camp_row["sequence_config"] = seq_cfg
        try:
            camp = sb.insert("campaigns", camp_row, on_conflict="name")[0]
        except Exception:
            # sequence_config column may not be migrated on this deploy — retry
            # without it so import never hard-fails (default copy then applies).
            camp_row.pop("sequence_config", None)
            camp = sb.insert("campaigns", camp_row, on_conflict="name")[0]
        rows, seen = [], set()
        for r in reader:
            lead = _csv_row_to_lead(r, camp["id"])
            if lead and lead["business"].lower() not in seen:
                seen.add(lead["business"].lower())
                rows.append(lead)
        if rows:
            sb.insert("leads", rows, on_conflict="campaign_id,business")
        return redirect(f"/campaigns/{camp['id']}")

    err = request.args.get("err")
    alert = '<div class="note-bar" style="border-color:var(--danger,#c33);color:var(--danger,#c33)">Pick a CSV file first.</div>' if err == "nofile" else ""
    body = f"""
    <div class="block"><div class="block-head"><div><div class="block-title">Upload leads</div><div class="block-sub">Bring your own CSV. These leads are imported as-is and never scraped.</div></div></div>
    <div class="block-body">
      {alert}
      <div class="note-bar">Recognized columns (any order, case-insensitive): <strong>business</strong> (required), website, email, phone, address, city, country, first_name, and optional <strong>subject</strong> + <strong>body</strong> (pre-written step-1 copy, sent verbatim). The campaign stays <strong>paused</strong> until you click <strong>Start outreach</strong>.</div>
      <form method="post" action="/upload" enctype="multipart/form-data" style="max-width:680px">
        <div class="field"><label>CSV file</label><input name="file" type="file" accept=".csv,text/csv" required></div>
        <div class="row-2" style="grid-template-columns:1fr 1fr">
          <div class="field"><label>List name <span style="text-transform:none;color:var(--ink-faint)">(optional)</span></label><input name="name" placeholder="defaults to file name"></div>
          <div class="field"><label>Region <span style="text-transform:none;color:var(--ink-faint)">(optional)</span></label><input name="region" placeholder="United States"></div>
        </div>
        <div class="field"><label>Email sequence style <span style="text-transform:none;color:var(--ink-faint)">(drives the copy for steps 2-7)</span></label><select name="instruction_set"><option value="default">Default (AI ops audit)</option><option value="travel">Travel agencies (AI itinerary generator)</option></select></div>
        <div class="field"><label>Offer brief <span style="text-transform:none;color:var(--ink-faint)">(what you pitch — used to write the emails)</span></label><textarea name="offer_brief" rows="5" placeholder="Who, what pain, what outcome — then the offer."></textarea></div>
        <div style="display:flex;gap:10px;margin-top:4px">
          <button class="btn primary" type="submit"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0-12l-4 4m4-4l4 4M5 21h14"/></svg> Import CSV</button>
        </div>
      </form>
    </div></div>"""
    return shell("upload", "workspace / upload", "Upload CSV", "Import leads", body)


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


# ════════════════ Campaign builder (interactive wizard) ════════════════
BUILD_HTML = """
<style>
  .wiz{max-width:920px}
  .wiz-steps{display:flex;gap:8px;margin-bottom:22px;flex-wrap:wrap}
  .wiz-pill{display:flex;align-items:center;gap:8px;padding:8px 14px;border:1px solid var(--line,#e6e1d8);border-radius:999px;font-size:13px;color:var(--ink-faint);background:var(--card,#fff)}
  .wiz-pill .n{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:600;background:var(--line,#e6e1d8);color:var(--ink-soft)}
  .wiz-pill.active{border-color:var(--accent,#b4541f);color:var(--ink)}
  .wiz-pill.active .n{background:var(--accent,#b4541f);color:#fff}
  .wiz-pill.done .n{background:var(--good,#0a7d2c);color:#fff}
  .wiz-panel{display:none}
  .wiz-panel.active{display:block;animation:wizfade .18s ease}
  @keyframes wizfade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .wiz-foot{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
  .preset-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
  .preset-chip{cursor:pointer;border:1px solid var(--line,#e6e1d8);border-radius:10px;padding:10px 14px;background:var(--card,#fff);font-size:13px;min-width:130px}
  .preset-chip:hover{border-color:var(--accent,#b4541f)}
  .preset-chip b{display:block;font-size:14px;margin-bottom:2px}
  .preset-chip span{color:var(--ink-faint);font-size:12px}
  .seq-row{display:grid;grid-template-columns:64px 1fr 130px 34px;gap:10px;align-items:center;margin-bottom:8px}
  .seq-row .lbl{font-size:12px;color:var(--ink-faint);font-family:var(--font-mono,monospace)}
  .seq-row select,.seq-row input{width:100%}
  .seq-x{cursor:pointer;border:1px solid var(--line,#e6e1d8);border-radius:8px;height:34px;display:grid;place-items:center;color:var(--ink-faint)}
  .seq-x:hover{border-color:var(--danger,#c33);color:var(--danger,#c33)}
  .sig-grid{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
  .sig{border:1px solid var(--line,#e6e1d8);border-radius:8px;padding:8px 12px;font-size:13px;background:var(--card,#fff)}
  .sig b{color:var(--ink-faint);font-weight:500;font-size:11px;display:block;text-transform:uppercase;letter-spacing:.04em}
  .pv-email{border:1px solid var(--line,#e6e1d8);border-radius:12px;padding:16px;background:var(--card,#fff);margin-top:8px}
  .pv-email .subj{font-weight:600;margin-bottom:8px;font-family:var(--font-serif,Georgia),serif}
  .pv-email .bd{white-space:pre-wrap;font-family:var(--font-serif,Georgia),serif;font-size:14px;line-height:1.6;color:var(--ink-soft)}
  .spin{width:18px;height:18px;border:2px solid var(--line,#e6e1d8);border-top-color:var(--accent,#b4541f);border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:-3px;margin-right:8px}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
<div class="wiz">
  <div class="wiz-steps">
    <div class="wiz-pill active" data-pill="1"><span class="n">1</span> Upload</div>
    <div class="wiz-pill" data-pill="2"><span class="n">2</span> Offer</div>
    <div class="wiz-pill" data-pill="3"><span class="n">3</span> Sequence</div>
    <div class="wiz-pill" data-pill="4"><span class="n">4</span> Preview &amp; launch</div>
  </div>

  <!-- STEP 1 -->
  <div class="wiz-panel active" data-step="1">
    <div class="block"><div class="block-body">
      <div class="note-bar">CSV columns (any order, case-insensitive): <b>business</b> (required), website, email, phone, address, city, country. Leads import paused; nothing sends until you launch.</div>
      <div class="field"><label>Leads CSV (Apollo export works)</label><input id="csvFile" type="file" accept=".csv,text/csv"></div>
      <div class="row-2" style="grid-template-columns:1fr 1fr">
        <div class="field"><label>Campaign name <span style="text-transform:none;color:var(--ink-faint)">(optional)</span></label><input id="campName" placeholder="defaults to file name"></div>
        <div class="field"><label>Region <span style="text-transform:none;color:var(--ink-faint)">(optional)</span></label><input id="campRegion" placeholder="United States"></div>
      </div>
      <div id="err1" style="color:var(--danger,#c33);font-size:13px"></div>
    </div></div>
    <div class="wiz-foot"><button class="btn primary" onclick="next1()">Continue &rsaquo;</button></div>
  </div>

  <!-- STEP 2 -->
  <div class="wiz-panel" data-step="2">
    <div class="block"><div class="block-body">
      <div class="note-bar">Answer these and we compose the offer brief that every email is written from. Edit the final text directly if you want.</div>
      <div class="field"><label>Who you target</label><input class="ob" data-k="Who" placeholder="e.g. home-service businesses doing $1M-5M"></div>
      <div class="field"><label>The pain</label><input class="ob" data-k="Pain" placeholder="e.g. hours lost to manual quoting and follow-up"></div>
      <div class="field"><label>The outcome you deliver</label><input class="ob" data-k="Outcome" placeholder="e.g. AI absorbs the repetitive back-office work"></div>
      <div class="field"><label>The offer</label><input class="ob" data-k="Offer" placeholder="e.g. free AI operations audit, then we build and run the systems"></div>
      <div class="field"><label>Risk reversal</label><input class="ob" data-k="Risk reversal" placeholder="e.g. measurable ROI in 2-3 months or you do not pay"></div>
      <div class="field"><label>Final offer brief <span style="text-transform:none;color:var(--ink-faint)">(editable)</span></label><textarea id="offerBrief" rows="7" placeholder="Composed from the fields above."></textarea></div>
    </div></div>
    <div class="wiz-foot"><button class="btn" onclick="goStep(1)">&lsaquo; Back</button><button class="btn primary" onclick="goStep(3)">Continue &rsaquo;</button></div>
  </div>

  <!-- STEP 3 -->
  <div class="wiz-panel" data-step="3">
    <div class="block"><div class="block-body">
      <div class="note-bar">How many emails and how far apart. Start from a preset, then tweak. First email always sends on day 0.</div>
      <div class="preset-row">
        <div class="preset-chip" onclick="preset('short')"><b>Short</b><span>3 emails</span></div>
        <div class="preset-chip" onclick="preset('standard')"><b>Standard</b><span>5 emails</span></div>
        <div class="preset-chip" onclick="preset('aggressive')"><b>Aggressive</b><span>7 emails</span></div>
      </div>
      <div id="seqEditor"></div>
      <button class="btn sm" id="addStepBtn" onclick="addStep()" style="margin-top:6px">+ Add email</button>
      <div id="seqSummary" class="note-bar" style="margin-top:14px"></div>
    </div></div>
    <div class="wiz-foot"><button class="btn" onclick="goStep(2)">&lsaquo; Back</button><button class="btn primary" onclick="goStep(4)">Continue &rsaquo;</button></div>
  </div>

  <!-- STEP 4 -->
  <div class="wiz-panel" data-step="4">
    <div class="block"><div class="block-body">
      <div id="previewBox"></div>
    </div></div>
    <div class="wiz-foot">
      <button class="btn" onclick="goStep(3)">&lsaquo; Back</button>
      <button class="btn" onclick="runPreview()">Regenerate preview</button>
      <button class="btn primary" id="launchBtn" onclick="launch()">Launch campaign &rsaquo;</button>
    </div>
  </div>
</div>
<script>
var ROLES = __ROLES__;
var PRESETS = __PRESETS__;
var seqSteps = JSON.parse(JSON.stringify(PRESETS.standard));
var offerDirty = false;

function goStep(n){
  document.querySelectorAll('.wiz-panel').forEach(function(p){p.classList.toggle('active', +p.dataset.step===n);});
  document.querySelectorAll('.wiz-pill').forEach(function(p){
    var i=+p.dataset.pill; p.classList.toggle('active', i===n); p.classList.toggle('done', i<n);
  });
  if(n===4) runPreview();
  window.scrollTo({top:0,behavior:'smooth'});
}
function next1(){
  var f=document.getElementById('csvFile');
  if(!f.files.length){document.getElementById('err1').textContent='Pick a CSV file first.';return;}
  document.getElementById('err1').textContent='';
  goStep(2);
}
// ── Offer compose ──
function composeOffer(){
  if(offerDirty) return;
  var parts=[];
  document.querySelectorAll('.ob').forEach(function(el){
    if(el.value.trim()) parts.push(el.dataset.k+': '+el.value.trim());
  });
  document.getElementById('offerBrief').value=parts.join('\\n');
}
document.querySelectorAll('.ob').forEach(function(el){el.addEventListener('input',composeOffer);});
document.getElementById('offerBrief').addEventListener('input',function(){offerDirty=true;});
// ── Sequence editor ──
function preset(name){ seqSteps=JSON.parse(JSON.stringify(PRESETS[name])); renderSeq(); }
function roleOptions(sel){
  return ROLES.map(function(r){return '<option value="'+r.key+'"'+(r.key===sel?' selected':'')+'>'+r.label+'</option>';}).join('');
}
function renderSeq(){
  var ed=document.getElementById('seqEditor'); ed.innerHTML='';
  seqSteps.forEach(function(s,i){
    var row=document.createElement('div'); row.className='seq-row';
    var gap = i===0 ? '<input value="Day 0" disabled style="text-align:center">'
                    : '<input type="number" min="1" max="60" value="'+(s.gap_days||1)+'" onchange="setGap('+i+',this.value)">';
    row.innerHTML='<div class="lbl">Email '+(i+1)+'</div>'+
      '<select onchange="setRole('+i+',this.value)">'+roleOptions(s.role)+'</select>'+
      gap+
      (seqSteps.length>3 && i>0 ? '<div class="seq-x" onclick="rmStep('+i+')">&times;</div>' : '<div></div>');
    ed.appendChild(row);
  });
  document.getElementById('addStepBtn').style.display = seqSteps.length>=7?'none':'';
  var span=seqSteps.reduce(function(a,s,i){return a+(i===0?0:(parseInt(s.gap_days)||0));},0);
  document.getElementById('seqSummary').innerHTML='<b>'+seqSteps.length+' emails</b> over '+span+' days. Follow-ups stop automatically if a lead replies.';
}
function setRole(i,v){seqSteps[i].role=v;}
function setGap(i,v){seqSteps[i].gap_days=Math.max(1,parseInt(v)||1);renderSeq();}
function rmStep(i){if(seqSteps.length>3){seqSteps.splice(i,1);renderSeq();}}
function addStep(){if(seqSteps.length<7){seqSteps.push({role:'value-drop',gap_days:4});renderSeq();}}
function config(){return seqSteps.map(function(s,i){return {role:s.role,gap_days:i===0?0:(parseInt(s.gap_days)||1)};});}
// ── Form data ──
function formData(){
  var fd=new FormData();
  fd.append('file', document.getElementById('csvFile').files[0]);
  fd.append('name', document.getElementById('campName').value);
  fd.append('region', document.getElementById('campRegion').value);
  fd.append('offer_brief', document.getElementById('offerBrief').value);
  fd.append('sequence_config', JSON.stringify(config()));
  return fd;
}
// ── Preview ──
function runPreview(){
  var box=document.getElementById('previewBox');
  box.innerHTML='<div><span class="spin"></span>Enriching a sample lead and drafting email 1...</div>';
  fetch('/build/preview',{method:'POST',body:formData()}).then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
  .then(function(res){
    if(!res.ok){box.innerHTML='<div style="color:var(--danger,#c33)">'+(res.d.error||'Preview failed')+'</div>';return;}
    var d=res.d, s=d.signals, sigs='';
    function sig(l,v){return v?('<div class="sig"><b>'+l+'</b>'+v+'</div>'):'';}
    sigs=sig('Rating',s.rating)+sig('Reviews',s.reviews)+sig('Type',s.type)+sig('Intent',s.intent)+sig('Decision maker',s.decision_maker);
    box.innerHTML=
      '<div class="note-bar"><b>'+d.count+' leads</b> ready. Sending <b>'+d.total_steps+' emails</b> over <b>'+d.span_days+' days</b>. Below is a real, hyper-personalized draft for one of your leads.</div>'+
      '<div style="font-size:13px;color:var(--ink-faint);margin-top:10px">Sample lead</div>'+
      '<div style="font-weight:600;font-size:15px">'+(d.business||'')+' <span style="font-weight:400;color:var(--ink-faint)">'+(d.city||'')+' &middot; '+(d.email||'')+'</span></div>'+
      (sigs?('<div class="sig-grid">'+sigs+'</div>'):'<div style="font-size:12px;color:var(--ink-faint);margin:8px 0">No enrichment signals found for this lead.</div>')+
      '<div style="font-size:13px;color:var(--ink-faint);margin-top:6px">Email 1 of '+d.total_steps+'</div>'+
      '<div class="pv-email"><div class="subj">'+esc(d.draft.subject)+'</div><div class="bd">'+esc(d.draft.body)+'</div></div>'+
      '<div class="note-bar" style="margin-top:14px">Launching creates the campaign <b>paused</b>. You confirm the send on the next screen, then it runs on autopilot.</div>';
  }).catch(function(){box.innerHTML='<div style="color:var(--danger,#c33)">Network error. Try again.</div>';});
}
function esc(t){var d=document.createElement('div');d.textContent=t||'';return d.innerHTML;}
// ── Launch ──
function launch(){
  var b=document.getElementById('launchBtn'); b.disabled=true; b.innerHTML='<span class="spin"></span>Creating...';
  fetch('/build/create',{method:'POST',body:formData()}).then(function(r){return r.json().then(function(d){return {ok:r.ok,d:d};});})
  .then(function(res){
    if(!res.ok){b.disabled=false;b.innerHTML='Launch campaign &rsaquo;';alert(res.d.error||'Failed');return;}
    window.location=res.d.redirect;
  }).catch(function(){b.disabled=false;b.innerHTML='Launch campaign &rsaquo;';alert('Network error.');});
}
renderSeq();
</script>
"""

SEQUENCE_PRESETS = {
    "short": [
        {"role": "first-touch", "gap_days": 0},
        {"role": "value-drop", "gap_days": 4},
        {"role": "breakup", "gap_days": 5},
    ],
    "standard": [
        {"role": "first-touch", "gap_days": 0},
        {"role": "bump", "gap_days": 3},
        {"role": "fomo", "gap_days": 4},
        {"role": "value-drop", "gap_days": 4},
        {"role": "breakup", "gap_days": 5},
    ],
    "aggressive": seq.DEFAULT_SEQUENCE,
}


def _parse_csv_leads(file_storage, campaign_id):
    """Parse an uploaded CSV into deduped lead rows. Returns (rows, total_seen)."""
    import csv as _csv, io as _io
    raw = file_storage.read().decode("utf-8-sig", errors="replace")
    file_storage.stream.seek(0)
    reader = _csv.DictReader(_io.StringIO(raw))
    headers = list(reader.fieldnames or [])
    rows, seen = [], set()
    for r in reader:
        lead = _csv_row_to_lead(r, campaign_id)
        if lead and lead["business"].lower() not in seen:
            seen.add(lead["business"].lower())
            rows.append(lead)
    return rows, len(seen), headers


def _safe_sequence_config(raw):
    """Validate a client-sent sequence_config JSON string into a clean list."""
    try:
        steps = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    steps = seq.steps_of(steps)  # normalize + clamp roles/gaps
    return steps[:7] if len(steps) >= 3 else None


@app.route("/build")
def build_wizard():
    roles = json.dumps([{"key": k, "label": lbl, "desc": d} for k, lbl, d in seq.ROLE_META])
    presets = json.dumps(SEQUENCE_PRESETS)
    body = (BUILD_HTML
            .replace("__ROLES__", roles)
            .replace("__PRESETS__", presets))
    return shell("build", "workspace / build", "Build a campaign",
                 "Upload leads, set the offer, choose the cadence, preview, launch.", body)


@app.route("/build/preview", methods=["POST"])
def build_preview():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Upload a CSV first."}), 400
    offer = (request.form.get("offer_brief") or "").strip() or None
    config = _safe_sequence_config(request.form.get("sequence_config"))
    if not config:
        return jsonify({"error": "Pick a sequence (3 to 7 emails)."}), 400

    rows, total, headers = _parse_csv_leads(f, 0)
    if not rows:
        cols = ", ".join(headers[:12]) or "(none detected)"
        return jsonify({"error": f"No company/business name found. Columns in your CSV: {cols}. "
                                 f"Rename the company column to 'business' or 'company'."}), 400

    sample = dict(rows[0])
    try:
        from lib import enrich
        patch = enrich.enrich_lead(sample)
        merged = {**sample, **patch}
    except Exception:
        merged = sample
        patch = {}

    sig = merged.get("signals") or {}
    dm = merged.get("decision_maker") or {}
    try:
        draft = seq.draft_one(merged, {"id": 0, "opens": 0, "clicks": 0, "current_step": 0}, 1, offer, config)
    except Exception as e:
        return jsonify({"error": f"Draft failed: {str(e)[:160]}"}), 500

    span = seq.cold_offset_days(config, len(config))
    return jsonify({
        "count": total,
        "business": merged.get("business"),
        "email": merged.get("email") or "(no email found)",
        "city": merged.get("city") or merged.get("country") or "",
        "signals": {
            "rating": sig.get("rating"),
            "reviews": sig.get("user_rating_count") or sig.get("review_count"),
            "type": sig.get("business_type"),
            "intent": merged.get("intent_score"),
            "decision_maker": (f"{dm.get('name')} ({dm.get('title')})" if dm.get("name") else None),
        },
        "draft": {"subject": draft.get("subject", ""), "body": draft.get("body", "")},
        "total_steps": len(config),
        "span_days": span,
    })


@app.route("/build/create", methods=["POST"])
def build_create():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Upload a CSV first."}), 400
    config = _safe_sequence_config(request.form.get("sequence_config"))
    if not config:
        return jsonify({"error": "Pick a sequence (3 to 7 emails)."}), 400

    region = (request.form.get("region") or "").strip() or "United States"
    name = (request.form.get("name") or "").strip() or _slug("csv", f.filename.rsplit(".", 1)[0])
    offer = (request.form.get("offer_brief") or "").strip() or None

    base = {
        "name": name, "niche": "csv-upload", "region": region,
        "offer_brief": offer, "daily_scrape_target": 0, "active": False,
        "notes": json.dumps({"source": "builder"}),
    }
    # Deploy-safe: if sequence_config column isn't migrated yet, fall back to a
    # campaign without it (engine then uses the default 7-step drip).
    cfg_note = None
    try:
        camp = sb.insert("campaigns", {**base, "sequence_config": {"steps": config}}, on_conflict="name")[0]
    except Exception:
        camp = sb.insert("campaigns", base, on_conflict="name")[0]
        cfg_note = "saved (cadence pending DB migration — using default 7-step until applied)"

    rows, _, _ = _parse_csv_leads(f, camp["id"])
    if rows:
        sb.insert("leads", rows, on_conflict="campaign_id,business")
    return jsonify({"redirect": f"/campaigns/{camp['id']}", "note": cfg_note, "leads": len(rows)})


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
    <form method="get" id="filters" style="width:230px;flex-shrink:0">
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
        intent_html = f'<span style="font-weight:600;color:{"var(--good)" if (intent or 0)>=60 else "var(--ink-soft)"}">{intent}</span>' if intent is not None else '<span class="muted">—</span>'
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
          <div style="display:flex;gap:8px">
            <button class="btn sm" type="button" onclick="composeSel()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4v16h16v-7M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z"/></svg> Compose</button>
            <form method="post" action="/leads/enroll" id="enrollForm" onsubmit="return collect()">
              <input type="hidden" name="lead_ids" id="enrollIds">
              <button class="btn primary sm" type="submit"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> Add to sequence</button>
            </form>
          </div>
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

    info_icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>'
    body = f"""
    <div class="help">{info_icon}<div>Tick the leads you want, then a bar appears at the top: <b>Compose</b> sends your own copy as one-off emails now, or <b>Add to sequence</b> enrolls them in the automated 7-step follow-up. Only leads with an email can be contacted.</div></div>
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
      function composeSel(){{
        const ids=[...document.querySelectorAll('.lcb:checked')].map(c=>c.value);
        if(!ids.length){{alert('Select at least one lead');return;}}
        window.location='/compose?lead_ids='+ids.join(',');
      }}
    </script>"""
    return shell("leads", "workspace / leads", "Leads", f"{total} leads · select some, then Compose or add to follow-up", body)


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


# ───────── Compose: Gmail-style one-off sends ─────────
def _manual_seq_id(lead: dict):
    """Sequence id used only to TRACK a manual send (opens/replies correlate by
    sequence_id). Reuse the lead's sequence if any, else park a new one as
    status='manual' — the tick only picks status='active', so it never enters
    the automated 7-step cadence. Returns id or None."""
    if not lead.get("campaign_id"):
        return None
    rows = sb.select("sequences", {"select": "id", "lead_id": f"eq.{lead['id']}"}, limit=1)
    if rows:
        return rows[0]["id"]
    ins = sb.insert("sequences", {
        "lead_id": lead["id"], "campaign_id": lead["campaign_id"],
        "status": "manual", "current_step": 0, "next_send_at": None,
    }, on_conflict="lead_id")
    return ins[0]["id"] if ins else None


def _merge(text: str, lead: dict) -> str:
    """Fill {business} / {city} / {country} tokens per recipient."""
    return (text.replace("{business}", lead.get("business") or "")
                .replace("{city}", lead.get("city") or "")
                .replace("{country}", lead.get("country") or ""))


@app.route("/compose", methods=["GET"])
def compose_page():
    import html as _html
    pre_ids = {x.strip() for x in (request.args.get("lead_ids") or "").split(",") if x.strip().isdigit()}
    leads = sb.select("leads", {"select": "id,business,email,city,country",
                                "email": "not.is.null", "order": "created_at.desc"}, limit=1000)
    rows_html = ""
    for l in leads:
        checked = "checked" if str(l["id"]) in pre_ids else ""
        loc = ", ".join([x for x in [l.get("city"), l.get("country")] if x])
        hay = _html.escape(((l.get("business") or "") + " " + (l.get("email") or "") + " " + loc).lower())
        rows_html += (
          f'<label class="rcpt" data-s="{hay}">'
          f'<input type="checkbox" class="rcb" value="{l["id"]}" {checked} onchange="syncR()">'
          f'<span class="rb">{_html.escape(l.get("business") or "")}</span>'
          f'<span class="re">{_html.escape(l.get("email") or "")}</span>'
          f'<span class="rl">{_html.escape(loc)}</span></label>')

    sent, failed = request.args.get("sent"), request.args.get("failed")
    blast = request.args.get("blast")
    flash = ""
    if sent is not None:
        msg = f'Sent {sent}'
        if failed and failed != "0":
            msg += f' · {failed} failed'
        link = (f' · <a href="/blasts/{_html.escape(blast)}" style="color:var(--good);text-decoration:underline">view tracking →</a>'
                if blast else '')
        flash = f'<div class="note-bar" style="border-color:var(--good);color:var(--good)">{_html.escape(msg)}{link}</div>'
    elif request.args.get("err") == "missing":
        flash = '<div class="note-bar" style="border-color:var(--danger,#c33);color:var(--danger,#c33)">Pick a recipient and fill subject + body.</div>'

    me = getattr(g, "user", {}) or {}
    my_email = me.get("email")
    from_opts = "".join(
        f'<option value="{_html.escape(a)}" {"selected" if a == my_email else ""}>{_html.escape(a)}</option>'
        for a in users.FROM_OPTIONS)
    sig_opts = "".join(
        f'<option value="{i}">{_html.escape(s["label"])}</option>'
        for i, s in enumerate(me.get("signatures", [])))

    info_icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>'
    body = f"""
    {flash}
    <div class="help">{info_icon}<div><b>This sends individual one-off emails</b> to the leads you pick — exactly your copy, personalized per recipient. It does <b>not</b> enroll anyone in the automated 7-step follow-up (that lives under “Auto follow-up”). Tip: write <code>{{business}}</code> or <code>{{city}}</code> anywhere and it’s filled in for each lead.</div></div>
    <form method="post" action="/compose/send" id="composeForm" onsubmit="return collectR()">
    <input type="hidden" name="lead_ids" id="rcptIds">
    <div style="display:flex;gap:20px;align-items:flex-start">
      <div style="width:340px;flex-shrink:0">
        <div class="block" style="margin-bottom:0">
          <div class="block-head"><div><div class="block-title" style="font-size:15px">Recipients</div><div class="block-sub"><span id="rcount">0</span> selected</div></div></div>
          <div class="block-body" style="padding:14px">
            <input id="rsearch" placeholder="Filter by name, email, city" oninput="filterR()" style="width:100%;margin-bottom:8px">
            <div style="display:flex;gap:10px;margin-bottom:8px"><a onclick="selR(true)" style="font-size:12px;color:var(--accent);cursor:pointer">select all (visible)</a><a onclick="selR(false)" style="font-size:12px;color:var(--ink-mute);cursor:pointer">clear</a></div>
            <div id="rlist" style="max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:8px">{rows_html or '<div class="empty" style="padding:18px">No leads with an email yet.</div>'}</div>
            <div class="field" style="margin-top:12px"><label>Extra emails <span style="text-transform:none;color:var(--ink-faint)">(comma-separated, optional)</span></label><input name="extra_emails" placeholder="someone@acme.com"></div>
          </div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        <div class="block">
          <div class="block-head"><div><div class="block-title" style="font-size:15px">Message</div><div class="block-sub">Tokens: <code>{{business}}</code> <code>{{city}}</code> are filled per recipient.</div></div>
            <div class="block-actions"><button type="button" class="btn sm" onclick="aiDraft()" id="aiBtn"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.9 5.8L20 10l-6.1 1.2L12 17l-1.9-5.8L4 10l6.1-1.2z"/></svg> AI draft</button></div>
          </div>
          <div class="block-body">
            <div class="field"><label>Subject</label><input name="subject" id="subject" required placeholder="Subject line"></div>
            <div class="field"><label>Body</label><textarea name="body" id="cbody" rows="18" required placeholder="Write your email here.&#10;&#10;Hi {{business}} team,&#10;&#10;...&#10;&#10;Tokens like {{business}} and {{city}} are replaced per recipient."></textarea></div>
            <div class="row-2" style="grid-template-columns:1fr 1fr;gap:14px">
              <div class="field"><label>From</label><select name="from_email">{from_opts}</select></div>
              <div class="field"><label>Signature</label><select name="signature_idx">{sig_opts}</select></div>
            </div>
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--ink-soft);margin:4px 0 12px"><input type="checkbox" name="signature" checked style="width:15px;height:15px"> Append signature for {_html.escape(me.get('name',''))}</label>
            <div style="display:flex;gap:10px"><button class="btn primary" type="submit"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> Send individually (<span id="rcount2">0</span>)</button></div>
            <div class="block-sub" style="margin-top:10px">Each recipient gets a separate email. These are one-off sends and do not enroll the lead in the automated sequence.</div>
          </div>
        </div>
      </div>
    </div>
    </form>
    <style>
      .rcpt{{display:grid;grid-template-columns:18px 1fr;column-gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;font-size:13px}}
      .rcpt:last-child{{border-bottom:none}} .rcpt:hover{{background:var(--bg-soft,rgba(0,0,0,.02))}}
      .rcpt .rb{{font-weight:600;color:var(--ink)}} .rcpt .re,.rcpt .rl{{grid-column:2;color:var(--ink-mute);font-size:12px}}
      #composeForm code{{font-family:var(--font-mono);font-size:11px;background:var(--bg-soft,rgba(0,0,0,.04));padding:1px 5px;border-radius:4px}}
    </style>
    <script>
      function syncR(){{
        const n=document.querySelectorAll('.rcb:checked').length;
        document.getElementById('rcount').textContent=n;
        document.getElementById('rcount2').textContent=n;
      }}
      function filterR(){{
        const q=document.getElementById('rsearch').value.toLowerCase();
        document.querySelectorAll('.rcpt').forEach(el=>{{el.style.display=el.dataset.s.includes(q)?'':'none';}});
      }}
      function selR(on){{document.querySelectorAll('.rcpt').forEach(el=>{{if(el.style.display!=='none')el.querySelector('.rcb').checked=on;}});syncR();}}
      function collectR(){{
        const ids=[...document.querySelectorAll('.rcb:checked')].map(c=>c.value);
        const extra=(document.querySelector('[name=extra_emails]').value||'').trim();
        if(!ids.length && !extra){{alert('Pick at least one recipient');return false;}}
        document.getElementById('rcptIds').value=ids.join(',');return true;
      }}
      async function aiDraft(){{
        const first=document.querySelector('.rcb:checked');
        if(!first){{alert('Select a recipient to draft for');return;}}
        const btn=document.getElementById('aiBtn');btn.disabled=true;const t=btn.textContent;btn.textContent='Drafting...';
        try{{
          const r=await fetch('/compose/draft',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{lead_id:first.value}})}});
          const d=await r.json();
          if(d.error){{alert(d.error);}}else{{document.getElementById('subject').value=d.subject||'';document.getElementById('cbody').value=d.body||'';}}
        }}catch(e){{alert('Draft failed');}}finally{{btn.disabled=false;btn.textContent=t;}}
      }}
      syncR();
    </script>"""
    return shell("compose", "workspace / compose", "Send email", "Write your copy and send it to the leads you pick", body)


@app.route("/compose/send", methods=["POST"])
def compose_send():
    import re as _re
    ids = [int(x) for x in (request.form.get("lead_ids") or "").split(",") if x.strip().isdigit()]
    extra = [e.strip().lower() for e in _re.split(r"[,\s;]+", request.form.get("extra_emails") or "") if "@" in e]
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    sign = request.form.get("signature") == "on"
    if not subject or not body or (not ids and not extra):
        return redirect("/compose?err=missing")

    # Per-user identity: From address (validated against the allowed list),
    # display name, and the chosen signature variant.
    me = getattr(g, "user", {}) or {}
    from_email = request.form.get("from_email")
    if from_email not in users.FROM_OPTIONS:
        from_email = me.get("email")
    # Signature + display name follow the SELECTED From address (Khush, Vineet,
    # Amaan), falling back to the logged-in user for shared/unknown addresses.
    sender = users.by_email(from_email) or me
    from_name = sender.get("name")
    sigs = sender.get("signatures", [])
    try:
        sig_text = sigs[int(request.form.get("signature_idx") or 0)]["text"]
    except (ValueError, IndexError, KeyError, TypeError):
        sig_text = sigs[0]["text"] if sigs else None

    def _send(to, subj, bdy, sid):
        return seq.send_manual(to, subj, bdy, sid, append_signature=sign,
                               signature=sig_text, from_email=from_email, from_name=from_name)

    # Every composer send is recorded as a "blast" so it has its own tracking
    # (open/click/reply rate) at /blasts — independent of campaigns. The Resend
    # webhook correlates per-recipient events back here by resend_id. All blast
    # bookkeeping is best-effort: if the blasts tables are unavailable (e.g. the
    # migration hasn't run yet) it must never block the actual email send.
    blast_id = None
    try:
        blast = sb.insert("blasts", {
            "subject": subject, "body": body,
            "from_email": from_email, "from_name": from_name,
            "sent_by": me.get("id") or me.get("email"),
        })
        blast_id = blast[0]["id"] if blast else None
    except Exception:
        blast_id = None

    def _track(email, res, *, business=None, lead_id=None):
        """Log a blast_recipients row (one per outbound, success or failure)."""
        if not blast_id:
            return
        try:
            sb.insert("blast_recipients", {
                "blast_id": blast_id, "email": email, "business": business,
                "lead_id": lead_id, "resend_id": res.get("resend_id"),
                "status": "failed" if "error" in res else "sent",
            })
        except Exception:
            pass

    sent = failed = 0
    if ids:
        in_list = ",".join(str(i) for i in ids)
        leads = sb.select("leads", {"select": "*", "id": f"in.({in_list})"}, limit=5000)
        for l in leads:
            to = l.get("email")
            if not to:
                failed += 1
                continue
            sid = _manual_seq_id(l)
            res = _send(to, _merge(subject, l), _merge(body, l), sid or 0)
            _track(to, res, business=l.get("business"), lead_id=l.get("id"))
            if "error" in res:
                failed += 1
                continue
            sent += 1
            # Campaign leads also flow into the global KPIs via sequence_events.
            if sid:
                sb.insert("sequence_events", {
                    "sequence_id": sid, "step": 0, "event_type": "sent",
                    "resend_id": res.get("resend_id"),
                    "meta": {"subject": subject, "kind": "manual", "by": me.get("id"),
                             "from": from_email, "blast_id": blast_id},
                })
    for e in extra:
        res = _send(e, _merge(subject, {}), _merge(body, {}), 0)
        _track(e, res)
        failed += 1 if "error" in res else 0
        sent += 0 if "error" in res else 1

    if blast_id:
        try:
            sb.update("blasts", {"id": blast_id}, {"recipients": sent})
        except Exception:
            pass
    return redirect(f"/compose?sent={sent}&failed={failed}&blast={blast_id or ''}")


def _blast_stats(recips: list) -> dict:
    """Aggregate per-blast recipient rows into headline counts + rates."""
    delivered = [r for r in recips if r.get("status") != "failed"]
    n = len(delivered)
    o = sum(1 for r in delivered if r.get("opened"))
    c = sum(1 for r in delivered if r.get("clicked"))
    rep = sum(1 for r in delivered if r.get("replied"))
    b = sum(1 for r in delivered if r.get("bounced"))
    f = sum(1 for r in recips if r.get("status") == "failed")
    return {"n": n, "opened": o, "clicked": c, "replied": rep, "bounced": b,
            "failed": f, "orate": _rate(o, n), "crate": _rate(c, n),
            "rrate": _rate(rep, n)}


@app.route("/blasts")
def blasts_page():
    import html as _html
    blasts = sb.select("blasts", {"select": "*", "order": "created_at.desc"}, limit=200)
    by_blast = defaultdict(list)
    if blasts:
        ids = ",".join(str(b["id"]) for b in blasts)
        recips = sb.select("blast_recipients",
                           {"select": "blast_id,status,opened,clicked,replied,bounced",
                            "blast_id": f"in.({ids})"}, limit=50000)
        for r in recips:
            by_blast[r["blast_id"]].append(r)

    rows = ""
    for bl in blasts:
        s = _blast_stats(by_blast.get(bl["id"], []))
        subj = _html.escape((bl.get("subject") or "(no subject)")[:80])
        when = (bl.get("created_at") or "")[:16].replace("T", " ")
        frm = _html.escape(bl.get("from_email") or "")
        rows += (
          f'<tr onclick="location.href=\'/blasts/{bl["id"]}\'" style="cursor:pointer">'
          f'<td class="subj">{subj}</td>'
          f'<td class="when">{when}</td>'
          f'<td>{frm}</td>'
          f'<td style="text-align:right">{s["n"]}</td>'
          f'<td style="text-align:right">{s["orate"]}%</td>'
          f'<td style="text-align:right">{s["crate"]}%</td>'
          f'<td style="text-align:right">{s["rrate"]}%</td></tr>')
    if not rows:
        rows = ('<tr><td colspan="7" style="padding:40px;text-align:center;color:var(--ink-mute)">'
                'No sends yet. Use <strong>Send email</strong> to send your first one-off blast.</td></tr>')

    body = f"""
    <div class="help"><div>Every one-off send from <b>Send email</b> is tracked here — including emails to manually typed-in addresses that aren’t saved leads. Open and click rates fill in as recipients engage (bot opens within 35s are filtered out). Click a row for per-recipient detail.</div></div>
    <div class="block"><div class="block-body" style="padding:0">
    <table class="tbl">
      <thead><tr><th>Subject</th><th>Sent</th><th>From</th><th style="text-align:right">Recipients</th><th style="text-align:right">Open rate</th><th style="text-align:right">Click rate</th><th style="text-align:right">Reply rate</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div></div>
    <style>.tbl{{width:100%;border-collapse:collapse;font-size:13px}}.tbl th{{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);color:var(--ink-mute);font-size:11px;text-transform:uppercase;letter-spacing:.04em}}.tbl td{{padding:11px 14px;border-bottom:1px solid var(--line)}}.tbl tbody tr:hover{{background:var(--bg-soft)}}.tbl .subj{{font-weight:600;color:var(--ink)}}.tbl .when{{color:var(--ink-mute);font-variant-numeric:tabular-nums}}</style>"""
    return shell("blasts", "workspace / sends", "Sent emails", "One-off sends and their tracking", body)


@app.route("/blasts/<int:bid>")
def blast_detail(bid):
    import html as _html
    rows_b = sb.select("blasts", {"select": "*", "id": f"eq.{bid}"}, limit=1)
    if not rows_b:
        return redirect("/blasts")
    bl = rows_b[0]
    recips = sb.select("blast_recipients",
                       {"select": "*", "blast_id": f"eq.{bid}", "order": "created_at.asc"},
                       limit=50000)
    s = _blast_stats(recips)

    def _state(r):
        if r.get("status") == "failed":
            return chip("bounced", "failed")
        if r.get("replied"):
            return chip("replied", "replied")
        if r.get("bounced"):
            return chip("bounced", "bounced")
        if r.get("clicked"):
            return chip("clicked", "clicked")
        if r.get("opened"):
            return chip("opened", "opened")
        return chip("sent", "sent")

    rrows = "".join(
      f'<tr><td>{_html.escape(r.get("business") or "")}</td>'
      f'<td class="email">{_html.escape(r.get("email") or "")}</td>'
      f'<td>{_state(r)}</td></tr>' for r in recips) or \
      '<tr><td colspan="3" style="padding:30px;text-align:center;color:var(--ink-mute)">No recipients.</td></tr>'

    subj = _html.escape(bl.get("subject") or "(no subject)")
    when = (bl.get("created_at") or "")[:16].replace("T", " ")
    frm = _html.escape(bl.get("from_email") or "")
    body = f"""
    <div style="margin-bottom:14px"><a href="/blasts" style="font-size:13px;color:var(--accent)">← All sends</a></div>
    <div class="block"><div class="block-body">
      <div style="font-weight:600;font-size:15px;margin-bottom:4px">{subj}</div>
      <div class="muted" style="font-size:12px;color:var(--ink-mute)">{when} · from {frm}</div>
    </div></div>
    <div class="kpis" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0">
      <div class="kpi"><div class="kpi-label">Recipients</div><div class="kpi-num">{s['n']}</div><div class="kpi-meta">{s['failed']} failed</div></div>
      <div class="kpi"><div class="kpi-label">Open rate</div><div class="kpi-num">{s['orate']}<span class="unit">%</span></div><div class="kpi-meta">{s['opened']} opened</div></div>
      <div class="kpi"><div class="kpi-label">Click rate</div><div class="kpi-num">{s['crate']}<span class="unit">%</span></div><div class="kpi-meta">{s['clicked']} clicked</div></div>
      <div class="kpi"><div class="kpi-label">Reply rate</div><div class="kpi-num">{s['rrate']}<span class="unit">%</span></div><div class="kpi-meta">{s['replied']} replied</div></div>
    </div>
    <div class="block"><div class="block-body" style="padding:0">
    <table class="tbl">
      <thead><tr><th>Business</th><th>Email</th><th>Status</th></tr></thead>
      <tbody>{rrows}</tbody>
    </table>
    </div></div>
    <style>.tbl{{width:100%;border-collapse:collapse;font-size:13px}}.tbl th{{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);color:var(--ink-mute);font-size:11px;text-transform:uppercase;letter-spacing:.04em}}.tbl td{{padding:10px 14px;border-bottom:1px solid var(--line)}}.tbl .email{{color:var(--ink-mute)}}</style>"""
    return shell("blasts", "workspace / sends", "Send detail", subj, body)


@app.route("/compose/draft", methods=["POST"])
def compose_draft():
    data = request.get_json(silent=True) or {}
    rows = sb.select("leads", {"select": "*", "id": f"eq.{data.get('lead_id')}"}, limit=1) if data.get("lead_id") else []
    if not rows:
        return jsonify({"error": "pick a recipient to draft for"}), 400
    lead = rows[0]
    offer = None
    if lead.get("campaign_id"):
        c = sb.select("campaigns", {"select": "offer_brief", "id": f"eq.{lead['campaign_id']}"}, limit=1)
        offer = c[0].get("offer_brief") if c else None
    try:
        d = seq.draft_one(lead, {"id": 0, "opens": 0, "clicks": 0, "current_step": 0}, 1, offer)
        return jsonify({"subject": d.get("subject", ""), "body": d.get("body", "")})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


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


@app.route("/campaigns")
def campaigns_list():
    camps = sb.select("campaigns", {"select": "*", "order": "created_at.desc"}, limit=500)
    leads = sb.select("leads", {"select": "campaign_id,enrichment_status,email"}, limit=200000)
    agg = defaultdict(lambda: {"total": 0, "ready": 0, "enriching": 0})
    for l in leads:
        a = agg[l["campaign_id"]]
        a["total"] += 1
        if l.get("enrichment_status") == "enriched" and l.get("email"):
            a["ready"] += 1
        elif l.get("enrichment_status") == "pending":
            a["enriching"] += 1

    rows = ""
    for c in camps:
        a = agg.get(c["id"], {"total": 0, "ready": 0, "enriching": 0})
        ready = a["ready"]
        if c["active"]:
            badge = chip("active", "● Live")
            action = f'<form method="post" action="/campaigns/{c["id"]}/toggle" style="display:inline"><button class="btn sm">Pause</button></form>'
        elif ready > 0:
            badge = chip("paused", "Paused")
            action = f'<form method="post" action="/campaigns/{c["id"]}/start" style="display:inline"><button class="btn primary sm">Start outreach → {ready} ▸</button></form>'
        else:
            badge = chip("paused", "Paused")
            action = '<span class="muted" style="font-size:12px">enriching…</span>' if a["enriching"] else '<span class="muted" style="font-size:12px">no leads</span>'
        rows += f"""<tr>
          <td><a href="/campaigns/{c['id']}" style="text-decoration:none;color:inherit"><div class="biz">{c['name']}</div><div class="email">{c.get('niche') or ''} · {c.get('region') or ''}</div></a></td>
          <td>{badge}</td>
          <td class="when">{a['total']}</td>
          <td class="when" style="font-weight:600;color:var(--good)">{ready}</td>
          <td class="when">{a['enriching']}</td>
          <td style="text-align:right">{action}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="6" style="padding:40px;text-align:center;color:var(--ink-mute)">No campaigns yet. Click <strong>New campaign</strong> to start.</td></tr>'

    body = f"""
    <div class="help"><div>A campaign = a batch of leads + your offer. Find or upload leads, wait for them to enrich (emails found automatically), then <strong>Start outreach</strong> to begin the automated 7-step drip. Nothing sends while a campaign is paused.</div></div>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-bottom:14px"><a class="btn" href="/scrape">Find leads</a><a class="btn primary" href="/build">+ New campaign</a></div>
    <div class="block"><table class="act-table">
      <thead><tr><th>Campaign</th><th>Status</th><th>Leads</th><th>Ready</th><th>Enriching</th><th></th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>"""
    return shell("campaigns", "workspace / campaigns", "Campaigns", "Launch and manage your outreach campaigns", body)


@app.route("/campaigns", methods=["POST"])
def create_campaign():
    _new_campaign(request.form)
    return redirect(url_for("campaigns_list"))


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

    # ---- Launch panel: show send-readiness + one clear primary action ----
    cap = int(os.environ.get("LEADGEN_DAILY_CAP", "120"))
    sendable = len(sb.select("leads", {"select": "id", "campaign_id": f"eq.{cid}",
        "enrichment_status": "eq.enriched", "email": "not.is.null"}, limit=100000))
    enriching = len(sb.select("leads", {"select": "id", "campaign_id": f"eq.{cid}",
        "enrichment_status": "eq.pending"}, limit=100000))
    noemail = len(sb.select("leads", {"select": "id", "campaign_id": f"eq.{cid}",
        "enrichment_status": "eq.enriched", "email": "is.null"}, limit=100000))

    if c["active"]:
        status_badge = chip("active", "● Live")
        cta = f'<form method="post" action="/campaigns/{cid}/toggle"><button class="btn">Pause outreach</button></form>'
        note = f"<strong>Live.</strong> Emails send automatically to enriched leads — up to {cap}/day across all live campaigns — following the 7-step drip. Pause anytime."
    else:
        status_badge = chip("paused", "Paused")
        if sendable > 0:
            cta = (f'<form method="post" action="/campaigns/{cid}/start">'
                   f'<button class="btn primary" style="font-size:14px;padding:11px 20px">'
                   f'Start outreach → email {sendable} lead{"s" if sendable != 1 else ""} ▸</button></form>')
            note = (f"<strong>{sendable} lead{'s are' if sendable != 1 else ' is'} ready.</strong> "
                    f"Clicking Start begins the drip (step 1 first, then 6 follow-ups), capped at {cap}/day. "
                    f"Nothing sends until you click.")
        else:
            cta = '<button class="btn" disabled style="opacity:.5;cursor:not-allowed">No leads ready yet</button>'
            note = ("Leads are still enriching — emails are found automatically every ~15 min. "
                    "Check back shortly, then Start outreach.") if enriching else \
                   ("No sendable leads. Use <strong>Scrape more</strong> or upload a CSV, "
                    "wait for enrichment, then Start outreach.")

    launch = f"""
    <div class="block" style="margin-top:-12px;margin-bottom:20px">
      <div class="block-body" style="display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap">
        <div style="display:flex;gap:30px;align-items:center">
          <div>{status_badge}</div>
          <div><div class="kpi-num" style="font-size:26px">{sendable}</div><div class="kpi-label">ready to email</div></div>
          <div><div class="kpi-num" style="font-size:26px">{enriching}</div><div class="kpi-label">enriching</div></div>
          <div><div class="kpi-num" style="font-size:26px">{noemail}</div><div class="kpi-label">no email</div></div>
        </div>
        <div style="display:flex;gap:10px;align-items:center">
          <form method="post" action="/campaigns/{cid}/scrape" style="display:inline"><button class="btn sm">Scrape more</button></form>
          {cta}
        </div>
      </div>
      <div class="block-body" style="padding-top:0"><div class="note-bar">{note}</div></div>
    </div>"""
    body = f"""
    {launch}
    {offer}
    <div class="row-2">
      <div class="block"><div class="block-head"><div class="block-title">Leads</div><div class="block-sub">{len(leads)}</div></div>
        <table class="act-table"><thead><tr><th>Business</th><th>Enrich</th><th>Intent</th><th>Added</th></tr></thead><tbody>{lrows or '<tr><td colspan=4 style="padding:24px;text-align:center;color:var(--ink-mute)">No leads yet.</td></tr>'}</tbody></table></div>
      <div class="block"><div class="block-head"><div class="block-title">Scrape runs</div></div>
        <table class="act-table"><thead><tr><th>Started</th><th>Status</th><th>Scraped</th></tr></thead><tbody>{runrows or '<tr><td colspan=3 style="padding:24px;text-align:center;color:var(--ink-mute)">No runs.</td></tr>'}</tbody></table></div>
    </div>"""
    return shell("campaigns", f"campaigns / {c['name']}", c["niche"], f"{c['name']} · {c['region']}", body)


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
    info_icon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>'
    help = f'<div class="help">{info_icon}<div>This is the <b>automated</b> follow-up: enrolled leads get a 7-step nurture over 28 days, written and sent for you. To send your own copy instead, use <b>Send email</b>. Enroll leads from the <b>Leads</b> page.</div></div>'
    return shell("sequences", "workspace / sequences", "Auto follow-up", "Hands-off 7-step nurture · 28-day cadence",
                 help + stat + table, badges={"sequences": by.get("active", 0)})


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
    tr = "".join(f'<tr><td class="when">{e["ts"][:19].replace("T"," ")}</td><td>{chip(e["event_type"], e["event_type"])}</td><td class="when">{e.get("sequence_id") or ""}</td><td class="when">{e.get("step") or ""}</td><td class="email">{(e.get("resend_id") or "")[:28]}</td></tr>' for e in rows if e["event_type"] not in ("opened_bot","clicked_bot"))
    body = f'<div class="block"><table class="act-table"><thead><tr><th>When (UTC)</th><th>Event</th><th>Seq</th><th>Step</th><th>Resend ID</th></tr></thead><tbody>{tr or "<tr><td colspan=5 style=padding:32px;text-align:center;color:var(--ink-mute)>No events yet.</td></tr>"}</tbody></table></div>'
    return shell("events", "workspace / activity", "Activity", "Every send, open, click, bounce, reply", body)


@app.route("/settings")
def settings_page():
    def st(name):
        return "on" if os.environ.get(name) else "off"
    conns = [
        ("Supabase", "SUPABASE_SERVICE_KEY", "state of truth"),
        ("OpenAI", "OPENAI_API_KEY", "email drafting + enrichment"),
        ("Resend", "RESEND_API_KEY", "email sending"),
        ("Resend webhook", "RESEND_WEBHOOK_SECRET", "open/click/bounce events"),
        ("Google Places", "GOOGLE_PLACES_API_KEY", "scraping"),
        ("Cron secret", "CRON_SECRET", "protects cron endpoints"),
    ]
    rows = "".join(f'<div class="conn-row"><span class="conn-dot {st(env)}"></span><div style="flex:1"><div style="font-weight:500">{name}</div><div class="muted" style="font-size:12px">{desc}</div></div><span class="mono muted" style="font-size:11px">{"✓ set" if os.environ.get(env) else "missing"}</span></div>' for name, env, desc in conns)
    daily_cap = os.environ.get("LEADGEN_DAILY_CAP", "120")
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


# ════════════════ Replies inbox ════════════════

_INTENT_CHIP = {
    "interested": ("active", "interested"),
    "objection":  ("paused",  "objection"),
    "not_now":    ("done",    "not now"),
    "stop":       ("bounced", "stop"),
    "other":      ("delivered", "other"),
}


@app.route("/replies")
def replies_page():
    import html as _html
    try:
        replies = sb.select(
            "replies",
            {"select": "*", "status": "eq.pending", "order": "created_at.desc"},
            limit=200,
        )
    except Exception:
        replies = []

    flash = ""
    if request.args.get("sent") == "1":
        rid = request.args.get("id", "")
        flash = (
            f'<div class="note-bar" style="border-color:var(--good);color:var(--good)">'
            f'Reply sent. <a href="/replies" style="color:var(--good);text-decoration:underline">Back to inbox</a></div>'
        )
    elif request.args.get("dismissed") == "1":
        flash = '<div class="note-bar">Reply dismissed.</div>'
    elif request.args.get("err"):
        flash = (f'<div class="note-bar" style="border-color:var(--warn);color:var(--warn)">'
                 f'{_html.escape(request.args.get("err", ""))}</div>')

    cards = ""
    for r in replies:
        chip_cls, chip_label = _INTENT_CHIP.get(r.get("intent") or "other", ("delivered", "other"))
        biz = _html.escape(r.get("business") or "")
        frm = _html.escape(r.get("from_email") or "")
        subj_in = _html.escape(r.get("in_subject") or "")
        body_in = _html.escape(r.get("in_body") or "")
        draft_subj = _html.escape(r.get("draft_subject") or "")
        draft_body = _html.escape(r.get("draft_body") or "")
        when = (r.get("created_at") or "")[:16].replace("T", " ")
        rid = int(r["id"])
        cards += f"""
<div class="block" style="margin-bottom:18px">
  <div class="block-head" style="padding:14px 18px 10px">
    <div style="flex:1">
      <div style="font-weight:600;font-size:14px">{biz or frm}</div>
      <div style="font-size:12px;color:var(--ink-mute);margin-top:1px">{frm} &middot; {when}</div>
    </div>
    <span class="chip {chip_cls}">{chip_label}</span>
  </div>
  <div class="block-body" style="padding:0 18px 14px">
    <div style="font-size:12px;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Their message</div>
    <div style="font-weight:500;margin-bottom:4px">{subj_in}</div>
    <pre style="white-space:pre-wrap;font-size:12px;color:var(--ink-soft);font-family:inherit;margin:0 0 16px;background:var(--bg-soft,rgba(0,0,0,.03));padding:10px 12px;border-radius:6px;border:1px solid var(--line)">{body_in}</pre>
    <form method="post" action="/replies/send">
      <input type="hidden" name="reply_id" value="{rid}">
      <div class="field"><label>Reply subject</label>
        <input name="draft_subject" value="{draft_subj}" required style="width:100%"></div>
      <div class="field"><label>Reply body <span style="text-transform:none;color:var(--ink-faint)">(signature appended automatically)</span></label>
        <textarea name="draft_body" rows="8" required style="width:100%">{draft_body}</textarea></div>
      <div style="display:flex;gap:10px;margin-top:10px">
        <button class="btn primary" type="submit" style="width:auto;padding:8px 18px">Send reply</button>
      </div>
    </form>
    <form method="post" action="/replies/dismiss" style="margin-top:8px">
      <input type="hidden" name="reply_id" value="{rid}">
      <button class="btn" type="submit"
        style="width:auto;padding:6px 14px;font-size:12px;background:none;border:1px solid var(--line);color:var(--ink-mute)">
        Dismiss</button>
    </form>
  </div>
</div>"""

    if not cards:
        cards = '<div class="block"><div class="block-body" style="padding:40px;text-align:center;color:var(--ink-mute)">Inbox zero. No pending replies.</div></div>'

    body = flash + cards
    return shell("replies", "workspace / replies", "Replies", "Inbound replies awaiting a response", body)


@app.route("/replies/send", methods=["POST"])
def replies_send():
    import html as _html
    rid = request.form.get("reply_id", "").strip()
    if not rid or not rid.isdigit():
        return redirect("/replies?err=Bad+request")

    rows = sb.select("replies", {"select": "*", "id": f"eq.{rid}"}, limit=1)
    if not rows:
        return redirect("/replies?err=Reply+not+found")
    reply = rows[0]

    draft_subject = (request.form.get("draft_subject") or "").strip()
    draft_body = (request.form.get("draft_body") or "").strip()
    if not draft_subject or not draft_body:
        return redirect(f"/replies?err=Subject+and+body+required")

    # Per-user identity: same resolution as compose_send.
    # Signature + display name follow the logged-in user (replies use the
    # dashboard operator's identity, not the original sequence sender).
    me = getattr(g, "user", {}) or {}
    from_email = me.get("email")
    sender = users.by_email(from_email) or me
    from_name = sender.get("name")
    sigs = sender.get("signatures", [])
    sig_text = sigs[0]["text"] if sigs else None

    res = seq.send_manual(
        reply["from_email"],
        draft_subject,
        draft_body,
        reply.get("sequence_id") or 0,
        append_signature=True,
        signature=sig_text,
        from_email=from_email,
        from_name=from_name,
    )

    if "error" in res:
        err = _html.escape((res["error"] or "Send failed")[:120])
        return redirect(f"/replies?err={err}")

    now_ts = datetime.now(timezone.utc).isoformat()
    sb.update(
        "replies",
        {"id": rid},
        {"status": "sent", "handled_by": me.get("id") or me.get("email"), "handled_at": now_ts},
    )
    return redirect(f"/replies?sent=1&id={rid}")


@app.route("/replies/dismiss", methods=["POST"])
def replies_dismiss():
    rid = request.form.get("reply_id", "").strip()
    if not rid or not rid.isdigit():
        return redirect("/replies?err=Bad+request")
    me = getattr(g, "user", {}) or {}
    now_ts = datetime.now(timezone.utc).isoformat()
    sb.update(
        "replies",
        {"id": rid},
        {"status": "dismissed", "handled_by": me.get("id") or me.get("email"), "handled_at": now_ts},
    )
    return redirect("/replies?dismissed=1")
