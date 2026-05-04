// Tiny shared helpers used by every page.

function appendLog(line) {
  const el = document.getElementById('log');
  if (!el) return;
  el.textContent += line + '\n';
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  const el = document.getElementById('log');
  if (el) el.textContent = '';
}

function setProgress(text) {
  const el = document.getElementById('progress');
  if (el) el.textContent = text;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ─────────── Asana modal (shared across pages) ───────────
let _ASANA_META = null;
let _ASANA_PREFILL = {};

async function _loadAsanaMeta() {
  if (_ASANA_META) return _ASANA_META;
  const r = await fetch('/api/asana/meta');
  _ASANA_META = await r.json();
  if (_ASANA_META.error) throw new Error(_ASANA_META.error);
  const proj = document.getElementById('asana-project');
  const asg = document.getElementById('asana-assignee');
  if (proj) {
    proj.innerHTML = '<option value="">— no project —</option>' +
      (_ASANA_META.projects || []).map(p => `<option value="${p.gid}">${escapeHtml(p.name)}</option>`).join('');
  }
  if (asg) {
    asg.innerHTML = '<option value="">— unassigned —</option>' +
      (_ASANA_META.users || []).map(u => `<option value="${u.gid}">${escapeHtml(u.name)}</option>`).join('');
  }
  return _ASANA_META;
}

window.openAsanaTask = async function(prefill = {}) {
  const modal = document.getElementById('asana-modal');
  if (!modal) {
    if (!window.ASANA_ENABLED) alert('Asana not configured. Add ASANA_PAT to .env');
    return;
  }
  _ASANA_PREFILL = prefill || {};
  document.getElementById('asana-status').textContent = '';
  document.getElementById('asana-config-hint').classList.add('hidden');
  document.getElementById('asana-target').textContent = prefill.target || '';
  document.getElementById('asana-name').value = prefill.title || '';
  document.getElementById('asana-notes').value = prefill.notes || '';
  modal.classList.remove('hidden');
  try {
    await _loadAsanaMeta();
  } catch (e) {
    document.getElementById('asana-status').innerHTML = '<span class="text-red-600">' + escapeHtml(e.message) + '</span>';
  }
};

window.closeAsana = function() {
  const m = document.getElementById('asana-modal');
  if (m) m.classList.add('hidden');
};

window.submitAsana = async function() {
  const name = document.getElementById('asana-name').value.trim();
  if (!name) { alert('Title required.'); return; }
  const notes = document.getElementById('asana-notes').value;
  const project = document.getElementById('asana-project').value || null;
  const assignee = document.getElementById('asana-assignee').value || null;
  const status = document.getElementById('asana-status');
  const submitBtn = document.getElementById('asana-submit');
  status.textContent = 'Creating…';
  submitBtn.disabled = true;
  try {
    const r = await fetch('/api/asana/task', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name, notes,
        project_gid: project,
        assignee_gid: assignee,
        csv_name: _ASANA_PREFILL.csv || '',
        business_name: _ASANA_PREFILL.business || '',
      }),
    });
    const data = await r.json();
    if (data.ok) {
      const url = data.task && data.task.permalink_url;
      status.innerHTML = url
        ? `Created. <a href="${url}" target="_blank" rel="noopener" class="text-blue-600 underline">Open in Asana ↗</a>`
        : 'Created.';
      setTimeout(() => closeAsana(), 1500);
    } else {
      status.innerHTML = '<span class="text-red-600">Error: ' + escapeHtml(data.error || 'unknown') + '</span>';
    }
  } catch (e) {
    status.innerHTML = '<span class="text-red-600">' + escapeHtml(e.message) + '</span>';
  }
  submitBtn.disabled = false;
};

// Poll a job for new events every `interval` ms. Calls onEvent for each new
// event, returns a controller {stop()}.
function attachJobPolling(jobId, onEvent, interval = 800) {
  let cursor = 0;
  let stopped = false;
  async function tick() {
    if (stopped) return;
    try {
      const r = await fetch(`/api/jobs/${jobId}/events?cursor=${cursor}`);
      if (r.ok) {
        const data = await r.json();
        cursor = data.cursor;
        for (const ev of data.events) onEvent(ev);
        if (data.status !== 'running') {
          onEvent({type: '_end', status: data.status, result: data.result, error: data.error});
          return;
        }
      }
    } catch (e) { /* keep retrying */ }
    setTimeout(tick, interval);
  }
  tick();
  return { stop() { stopped = true; } };
}

// Read SSE from a GET URL.
async function streamGetSSE(url, onEvent) {
  const resp = await fetch(url);
  if (!resp.ok) {
    onEvent({type: '_error', error: `HTTP ${resp.status}`});
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    buf += decoder.decode(value, {stream: true});
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try { onEvent(JSON.parse(line.slice(6))); }
          catch (e) { console.error('parse error', e, line); }
        }
      }
    }
  }
}

// startJob: POST to a long-running endpoint (returns {job_id}), then begin
// polling /api/jobs/<id>/events for incremental updates and forward each event
// to onEvent. Returns the job_id.
async function startJob(url, body, onEvent) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  const data = await r.json();
  if (!data.job_id) {
    onEvent({type: '_error', error: data.error || 'no job id returned'});
    return null;
  }
  attachJobPolling(data.job_id, onEvent);
  return data.job_id;
}

// POST a JSON body and consume an SSE response, calling `onEvent(parsedJson)` per event.
async function streamSSE(url, body, onEvent) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  if (!resp.ok) {
    const txt = await resp.text();
    appendLog('ERROR: ' + txt);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    buf += decoder.decode(value, {stream: true});
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try { onEvent(JSON.parse(line.slice(6))); }
          catch (e) { console.error('parse error', e, line); }
        }
      }
    }
  }
}
