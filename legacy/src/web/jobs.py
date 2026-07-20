"""In-memory background-job manager.

Long-running operations (scrape, enrich, bulk verify) run in worker threads
so they survive client-page navigation. Each job has an append-only event
buffer that any number of SSE consumers can replay from any cursor.

This is intentionally in-memory only — restart the Flask app and jobs are
lost. Good enough for a single-user local dashboard.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_lock = threading.Lock()
_jobs = {}            # id -> dict
_completed_keep = 50  # max completed jobs retained


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(kind, label):
    """Register a job. Returns the new id."""
    jid = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[jid] = {
            "id": jid,
            "kind": kind,
            "label": label,
            "status": "running",
            "started_at": _now(),
            "events": [],
            "result": None,
            "error": None,
        }
    log.info("job created: %s [%s] %s", jid, kind, label)
    return jid


def append(jid, event):
    """Append one event dict to the job's buffer."""
    with _lock:
        j = _jobs.get(jid)
        if j is not None:
            j["events"].append(event)


def cancel(jid):
    """Request cooperative cancel. Worker must poll `is_cancelled(jid)`
    between units of work and break out — partial results are kept."""
    with _lock:
        j = _jobs.get(jid)
        if j is None or j["status"] != "running":
            return False
        j["cancel_requested"] = True
    log.info("job cancel requested: %s", jid)
    return True


def is_cancelled(jid):
    with _lock:
        j = _jobs.get(jid)
        return bool(j and j.get("cancel_requested"))


def finish(jid, result=None, status="done"):
    with _lock:
        j = _jobs.get(jid)
        if j is None:
            return
        j["status"] = status
        j["result"] = result
        j["finished_at"] = _now()
        kind = j["kind"]
    log.info("job %s: %s [%s]", status, jid, kind)
    _trim_completed()


def fail(jid, error):
    with _lock:
        j = _jobs.get(jid)
        if j is None:
            return
        j["status"] = "failed"
        j["error"] = str(error)
        j["finished_at"] = _now()
        kind = j["kind"]
    log.error("job failed: %s [%s] — %s", jid, kind, error)
    _trim_completed()


def _trim_completed():
    """Cap completed jobs to avoid unbounded memory growth."""
    with _lock:
        completed = [j for j in _jobs.values() if j["status"] != "running"]
        if len(completed) <= _completed_keep:
            return
        to_drop = sorted(completed, key=lambda j: j.get("finished_at", ""))[:-_completed_keep]
        for j in to_drop:
            _jobs.pop(j["id"], None)


def get(jid):
    with _lock:
        return _jobs.get(jid)


def list_active():
    with _lock:
        return [_summary(j) for j in _jobs.values() if j["status"] == "running"]


def list_recent(limit=10):
    with _lock:
        items = sorted(_jobs.values(), key=lambda j: j.get("started_at", ""), reverse=True)
        return [_summary(j) for j in items[:limit]]


def _summary(j):
    progress = None
    leads_count = 0
    last_log = ""
    for ev in reversed(j["events"]):
        if not progress and ev.get("type") == "lead":
            progress = ev.get("progress")
        if not last_log and ev.get("type") in ("log", "lead", "progress", "error"):
            last_log = ev.get("line") or ev.get("name") or ev.get("error") or ""
        if progress and last_log:
            break
    leads_count = sum(1 for ev in j["events"] if ev.get("type") == "lead")
    return {
        "id": j["id"],
        "kind": j["kind"],
        "label": j["label"],
        "status": j["status"],
        "started_at": j["started_at"],
        "finished_at": j.get("finished_at"),
        "events_count": len(j["events"]),
        "leads_count": leads_count,
        "last_log": last_log,
        "progress": progress,
        "result": j.get("result"),
        "error": j.get("error"),
    }


def stream_events(jid, cursor=0, idle_sleep=0.2, max_wait_seconds=600):
    """Generator: yield each new event from `cursor` onward, then a terminal
    `_end` event when the job is done or failed.

    Stops automatically after `max_wait_seconds` of total sleep (≈10 min) so
    abandoned SSE connections don't pile up.
    """
    waited = 0.0
    while True:
        with _lock:
            j = _jobs.get(jid)
            if j is None:
                yield {"type": "_end", "status": "missing"}
                return
            events = list(j["events"])
            status = j["status"]
            result = j.get("result")
            error = j.get("error")

        while cursor < len(events):
            yield events[cursor]
            cursor += 1
            waited = 0  # active

        if status != "running":
            yield {"type": "_end", "status": status, "result": result, "error": error}
            return

        time.sleep(idle_sleep)
        waited += idle_sleep
        if waited >= max_wait_seconds:
            yield {"type": "_end", "status": "timeout"}
            return


def run_in_thread(jid, fn):
    """Run `fn()` on a daemon thread. `fn` should call append/finish/fail."""
    def wrapper():
        try:
            fn()
        except Exception as e:
            log.exception("job %s crashed", jid)
            append(jid, {"type": "error", "error": f"{type(e).__name__}: {e}"})
            fail(jid, e)

    t = threading.Thread(target=wrapper, daemon=True, name=f"job-{jid}")
    t.start()
    return t
