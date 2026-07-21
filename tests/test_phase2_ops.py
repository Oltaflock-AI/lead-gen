"""Phase 2 regression tests (plan.md Gate 2, code-level half).

Covers lib/ops (alert rate-limit, heartbeat semantics), watchdog dead-man
detection + reconciler, sequencer B7/B8/auto-retry, supabase GET retry, and
the B13 token-field switch. The infra half of Gate 2 (cron.job rows, kill
test, poke.yml deletion) runs against live Supabase after migration 013.
"""
import importlib.util
import os

import pytest

from lib import llm
from lib import ops
from lib import supabase as sb_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    path = os.path.join(ROOT, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def watchdog():
    return _load("watchdog_under_test", "api", "cron", "watchdog.py")


# ─────────── ops.alert rate limit ───────────
class TestAlertRateLimit:
    def test_second_alert_within_window_sends_no_email(self, monkeypatch):
        inserts, posts = [], []
        monkeypatch.setattr(ops.sb, "insert", lambda t, row, **k: inserts.append(row) or [row])
        # First call: no recent alert-sent row. Second call: one exists.
        state = {"sent": False}

        def fake_select(table, params=None, limit=None):
            return [{"id": 1}] if state["sent"] else []

        monkeypatch.setattr(ops.sb, "select", fake_select)

        class OK:
            status_code = 200
            text = ""

        def fake_post(url, *, headers=None, json=None, timeout=None):
            posts.append(json)
            state["sent"] = True
            return OK()

        monkeypatch.setattr(ops.requests, "post", fake_post)
        monkeypatch.setenv("RESEND_API_KEY", "re_dummy")

        ops.alert("test-source", "first failure")
        ops.alert("test-source", "second failure")
        assert len(posts) == 1, "second alert inside 6h window must not email"
        # but both alerts are logged
        assert sum(1 for r in inserts if r.get("level") == "alert") == 2

    def test_alert_never_raises(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(ops.sb, "insert", boom)
        monkeypatch.setattr(ops.sb, "select", boom)
        ops.alert("test-source", "still must not raise")  # no exception = pass

    def test_heartbeat_only_advances_last_ok_on_success(self, monkeypatch):
        rows = []
        monkeypatch.setattr(ops.sb, "insert", lambda t, row, **k: rows.append(row) or [row])
        ops.heartbeat("job-x", "ok", "fine")
        ops.heartbeat("job-x", "error: kaboom")
        assert "last_ok" in rows[0]
        assert "last_ok" not in rows[1], "a failing run must not refresh last_ok"


# ─────────── watchdog ───────────
class TestWatchdog:
    def test_silent_job_alerts(self, watchdog, monkeypatch):
        alerts = []
        monkeypatch.setattr(watchdog.ops, "alert", lambda src, msg, meta=None: alerts.append(msg))
        monkeypatch.setattr(watchdog.sb, "select", lambda *a, **k: [
            {"job": "sequencer_tick", "last_ok": "2026-07-01T00:00:00+00:00"},
        ])
        dead = watchdog._check_heartbeats()
        assert any("sequencer_tick" in d for d in dead)
        assert alerts and "scheduler dead" in alerts[0]

    def test_reconciler_applies_matched_and_marks_old_unmatched(self, watchdog, monkeypatch):
        updates, inserted = [], []

        def fake_select(table, params=None, limit=None):
            if table == "email_events_raw":
                return [
                    {"id": 1, "resend_id": "re_match", "event_type": "opened",
                     "payload": {"data": {}}, "received_at": "2026-07-20T00:00:00+00:00"},
                    {"id": 2, "resend_id": "re_orphan", "event_type": "opened",
                     "payload": {}, "received_at": "2026-01-01T00:00:00+00:00"},
                ]
            if table == "sequence_events":
                p = params or {}
                if p.get("resend_id") == "eq.re_match":
                    return [{"sequence_id": 9, "step": 2}]
                return []
            if table == "blast_recipients":
                return []
            return []

        monkeypatch.setattr(watchdog.sb, "select", fake_select)
        monkeypatch.setattr(watchdog.sb, "insert", lambda t, row, **k: inserted.append(row) or [row])
        monkeypatch.setattr(watchdog.sb, "update",
                            lambda t, match, patch: updates.append((t, match, patch)) or [match])
        monkeypatch.setattr(watchdog.sb, "rpc", lambda *a, **k: 1)

        out = watchdog._reconcile()
        assert out["applied"] == 1 and out["unmatched"] == 1
        assert inserted[0]["sequence_id"] == 9 and inserted[0]["event_type"] == "opened"
        marked = [u for u in updates if u[0] == "email_events_raw"]
        assert len(marked) == 2


# ─────────── sequencer B7 / auto-retry / B8 ───────────
class TestSequencerRecovery:
    def test_claim_slot_falls_back_open_pre_migration(self, tick, monkeypatch):
        def no_rpc(fn, args=None):
            raise RuntimeError("404 function not found")

        monkeypatch.setattr(tick.sb, "rpc", no_rpc)
        assert tick._claim_slot() is True

    def test_claim_slot_denies_when_cap_hit(self, tick, monkeypatch):
        monkeypatch.setattr(tick.sb, "rpc", lambda fn, args=None: False)
        assert tick._claim_slot() is False

    def test_revive_bounded_by_retry_count(self, tick, monkeypatch):
        updates = []

        def fake_select(table, params=None, limit=None):
            assert params.get("retry_count") == "lt.3"
            return [{"id": 5, "retry_count": 2}]

        monkeypatch.setattr(tick.sb, "select", fake_select)
        monkeypatch.setattr(tick.sb, "update",
                            lambda t, match, patch: updates.append(patch) or [match])
        monkeypatch.setattr(tick.sb, "count", lambda *a, **k: 0)
        out = tick._revive_errored()
        assert out["revived"] == 1
        assert updates[0]["retry_count"] == 3 and updates[0]["status"] == "active"

    def test_due_filters_campaigns_in_sql(self, tick, monkeypatch):
        seen = {}

        def fake_select(table, params=None, limit=None):
            seen.update(params)
            return []

        monkeypatch.setattr(tick.sb, "select", fake_select)
        tick._due({3, 7})
        assert seen.get("campaign_id") in ("in.(3,7)", "in.(7,3)")
        assert tick._due(set()) == []


# ─────────── supabase GET retry ───────────
class TestGetRetry:
    def test_retries_5xx_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(sb_mod.time, "sleep", lambda s: None)
        calls = {"n": 0}

        class R:
            def __init__(self, code, rows):
                self.status_code = code
                self._rows = rows
                self.headers = {}
                self.text = ""
            def raise_for_status(self):
                pass
            def json(self):
                return self._rows

        def fake_get(url, *, headers=None, params=None, timeout=None):
            calls["n"] += 1
            return R(503, []) if calls["n"] < 3 else R(200, [{"id": 1}])

        monkeypatch.setattr(sb_mod.requests, "get", fake_get)
        assert sb_mod.select("leads", {"select": "id"}, limit=1) == [{"id": 1}]
        assert calls["n"] == 3


# ─────────── B13 token field ───────────
class TestTokenField:
    def test_new_models_use_max_completion_tokens(self):
        assert llm._token_field("gpt-5-mini") == "max_completion_tokens"
        assert llm._token_field("o3") == "max_completion_tokens"
        assert llm._token_field("gpt-4o-mini") == "max_tokens"
