"""Phase 1 regression tests (plan.md Gate 1).

Each fixed audit bug gets a pin here: C1 pagination, C1a insert-only
enrollment, C3 insert-only scrape, C4 STOP/bounce suppression coverage,
C7 per-item isolation, H5 postal-address gate, B9c windowed first send.
"""
import importlib.util
import os
from datetime import datetime, timezone

import pytest

from lib import supabase as sb_mod
from lib import sequence as seq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    path = os.path.join(ROOT, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def inbound():
    return _load("inbound_under_test", "api", "webhook", "inbound.py")


@pytest.fixture(scope="module")
def resend_hook():
    return _load("resend_under_test", "api", "webhook", "resend.py")


@pytest.fixture(scope="module")
def daily_scrape():
    return _load("daily_scrape_under_test", "api", "cron", "daily_scrape.py")


class FakeGetResponse:
    def __init__(self, rows=None, headers=None):
        self._rows = rows or []
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


# ─────────── C1: select() pagination ───────────
class TestSelectPagination:
    def test_returns_more_than_one_page(self, monkeypatch):
        pages = []

        def fake_get(url, *, headers=None, params=None, timeout=None):
            rng = (headers or {}).get("Range", "")
            pages.append({"range": rng, "params": dict(params or {})})
            start = int(rng.split("-")[0])
            n = 1000 if start == 0 else 500  # second page comes back short
            return FakeGetResponse(rows=[{"id": start + i} for i in range(n)])

        monkeypatch.setattr(sb_mod.requests, "get", fake_get)
        rows = sb_mod.select("leads", {"select": "id"}, limit=100000)
        assert len(rows) == 1500
        assert pages[0]["range"] == "0-999"
        assert pages[1]["range"] == "1000-1999"
        # A stable order is required for disjoint pages.
        assert pages[0]["params"].get("order") == "id.asc"

    def test_single_page_requests_unchanged(self, monkeypatch):
        calls = []

        def fake_get(url, *, headers=None, params=None, timeout=None):
            calls.append({"headers": dict(headers or {}), "params": dict(params or {})})
            return FakeGetResponse(rows=[{"id": 1}])

        monkeypatch.setattr(sb_mod.requests, "get", fake_get)
        rows = sb_mod.select("leads", {"select": "id"}, limit=50)
        assert rows == [{"id": 1}]
        assert calls[0]["params"]["limit"] == "50"
        assert "Range" not in calls[0]["headers"]

    def test_count_uses_content_range(self, monkeypatch):
        def fake_get(url, *, headers=None, params=None, timeout=None):
            assert "count=exact" in (headers or {}).get("Prefer", "")
            return FakeGetResponse(rows=[{"id": 1}], headers={"Content-Range": "0-0/4321"})

        monkeypatch.setattr(sb_mod.requests, "get", fake_get)
        assert sb_mod.count("sequence_events", {"event_type": "eq.sent"}) == 4321

    def test_insert_ignore_duplicates_prefer_header(self, monkeypatch):
        seen = {}

        def fake_post(url, *, headers=None, params=None, json=None, timeout=None):
            seen.update({"headers": dict(headers or {}), "params": dict(params or {})})
            return FakeGetResponse(rows=[])

        monkeypatch.setattr(sb_mod.requests, "post", fake_post)
        sb_mod.insert("leads", [{"x": 1}], on_conflict="campaign_id,business", ignore_duplicates=True)
        assert "resolution=ignore-duplicates" in seen["headers"]["Prefer"]
        assert seen["params"]["on_conflict"] == "campaign_id,business"


# ─────────── C1a + B9c: enrollment is insert-only and window-scheduled ───────────
class TestAutocreate:
    ACTIVE = {3: {"id": 3, "region": "United States", "sequence_config": None}}

    def test_never_updates_existing_and_uses_send_window(self, tick, monkeypatch):
        first_send = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
        inserts = []

        def fake_select(table, params=None, limit=None):
            if table == "leads":
                assert params.get("sequences") == "is.null", "anti-join filter must be in SQL"
                assert params.get("email") == "not.is.null"
                return [{"id": 1, "campaign_id": 3, "email": "a@b.com", "country": None, "sequences": None}]
            if table == "suppressions":
                return []
            raise AssertionError(f"unexpected select {table}")

        def fake_insert(table, rows, *, on_conflict=None, ignore_duplicates=False):
            inserts.append({"table": table, "rows": rows, "on_conflict": on_conflict,
                            "ignore": ignore_duplicates})
            return rows

        monkeypatch.setattr(tick.sb, "select", fake_select)
        monkeypatch.setattr(tick.sb, "insert", fake_insert)
        monkeypatch.setattr(tick.sb, "update",
                            lambda *a, **k: pytest.fail("enrollment must never UPDATE sequences"))
        monkeypatch.setattr(tick.seq, "next_send_at", lambda *a, **k: first_send)

        created = tick._autocreate(dict(self.ACTIVE))
        assert created == 1
        ins = inserts[0]
        assert ins["on_conflict"] == "lead_id" and ins["ignore"] is True
        assert ins["rows"][0]["next_send_at"] == first_send.isoformat()

    def test_suppressed_lead_not_enrolled(self, tick, monkeypatch):
        def fake_select(table, params=None, limit=None):
            if table == "leads":
                return [{"id": 1, "campaign_id": 3, "email": "opted-out@b.com", "sequences": None}]
            if table == "suppressions":
                return [{"email": "opted-out@b.com"}]
            return []

        monkeypatch.setattr(tick.sb, "select", fake_select)
        monkeypatch.setattr(tick.sb, "insert",
                            lambda *a, **k: pytest.fail("suppressed lead must not be enrolled"))
        assert tick._autocreate(dict(self.ACTIVE)) == 0

    def test_suppression_read_failure_fails_closed(self, tick, monkeypatch):
        def fake_select(table, params=None, limit=None):
            if table == "suppressions":
                raise RuntimeError("supabase down")
            return []

        monkeypatch.setattr(tick.sb, "select", fake_select)
        assert tick._suppressed_for({"a@b.com"}) == {"a@b.com"}


# ─────────── C7 + B23: one failing item never kills the tick ───────────
class TestTickIsolation:
    def test_failed_item_paused_and_loop_continues(self, tick, monkeypatch):
        s1 = {"id": 1, "lead_id": 11, "campaign_id": 3}
        s2 = {"id": 2, "lead_id": 12, "campaign_id": 3}
        pauses = []

        monkeypatch.setattr(tick, "_active_campaigns", lambda: {3: {}})
        monkeypatch.setattr(tick, "_autocreate", lambda active: 0)
        monkeypatch.setattr(tick, "_sent_today", lambda: 0)
        monkeypatch.setattr(tick, "_due", lambda ids: [s1, s2])
        monkeypatch.setattr(tick, "_suppressed_for", lambda emails: set())
        monkeypatch.setattr(tick.sb, "select", lambda *a, **k: [])
        monkeypatch.setattr(tick.sb, "update",
                            lambda table, match, patch: pauses.append((match, patch)) or [match])

        def fake_process(s, active, suppressed):
            if s["id"] == 1:
                raise RuntimeError("boom")
            return {"seq": s["id"], "sent_step": 1}

        monkeypatch.setattr(tick, "_process_one", fake_process)
        out = tick._run()
        assert out["sent"] == 1
        assert any("error" in r for r in out["results"])
        assert any(p[1].get("paused_reason", "").startswith("send-error") for p in pauses)


# ─────────── H5: postal-address hard gate ───────────
class TestPostalGate:
    def test_live_mode_without_postal_refuses(self, monkeypatch, no_suppressions):
        monkeypatch.setenv("LEADGEN_TEST_MODE", "0")
        monkeypatch.delenv("LEADGEN_POSTAL_ADDRESS", raising=False)
        monkeypatch.setattr(seq, "POSTAL_ADDRESS", "")
        out = seq.send_email({"email": "a@b.com"}, {"subject": "s", "body": "b"}, 1, 1)
        assert "LEADGEN_POSTAL_ADDRESS" in out.get("error", "")

    def test_test_mode_without_postal_allows(self, monkeypatch, no_suppressions, resend_post):
        monkeypatch.setenv("LEADGEN_TEST_MODE", "1")
        monkeypatch.delenv("LEADGEN_POSTAL_ADDRESS", raising=False)
        monkeypatch.setattr(seq, "POSTAL_ADDRESS", "")
        out = seq.send_email({"email": "a@b.com"}, {"subject": "s", "body": "b"}, 1, 1)
        assert out.get("resend_id")

    def test_manual_path_also_gated(self, monkeypatch, no_suppressions):
        monkeypatch.setenv("LEADGEN_TEST_MODE", "0")
        monkeypatch.delenv("LEADGEN_POSTAL_ADDRESS", raising=False)
        monkeypatch.setattr(seq, "POSTAL_ADDRESS", "")
        out = seq.send_manual("a@b.com", "s", "b", 0)
        assert "LEADGEN_POSTAL_ADDRESS" in out.get("error", "")


# ─────────── C4: inbound STOP intent suppresses ───────────
class TestInboundStop:
    def test_stop_reply_inserts_suppression(self, inbound, monkeypatch):
        inserts, updates = [], []
        monkeypatch.setattr(inbound.sb, "insert",
                            lambda table, row, **k: inserts.append((table, row, k)) or [row])
        monkeypatch.setattr(inbound.sb, "update",
                            lambda table, match, patch: updates.append((table, match, patch)) or [])
        monkeypatch.setattr(inbound.sb, "select", lambda *a, **k: [])

        res = inbound._pause_for_reply("lead@biz.com", stop=True)
        assert res["suppressed"] is True
        supp = [i for i in inserts if i[0] == "suppressions"]
        assert supp and supp[0][1]["email"] == "lead@biz.com"
        assert supp[0][2].get("ignore_duplicates") is True

    def test_stop_intent_detection(self, inbound):
        assert inbound._is_stop({"data": {"text": "Please STOP emailing me"}})
        assert inbound._is_stop({"data": {"subject": "unsubscribe"}})
        assert not inbound._is_stop({"data": {"text": "interesting, tell me more"}})
        assert not inbound._is_stop({"data": {"text": "our non-stop service"}})

    def test_plain_reply_does_not_suppress(self, inbound, monkeypatch):
        inserts = []
        monkeypatch.setattr(inbound.sb, "insert",
                            lambda table, row, **k: inserts.append(table) or [row])
        monkeypatch.setattr(inbound.sb, "select", lambda *a, **k: [])
        inbound._pause_for_reply("lead@biz.com", stop=False)
        assert "suppressions" not in inserts


# ─────────── B12/H7: bounce without a sequence still suppresses ───────────
class TestBounceAlwaysSuppresses:
    def test_sequenceless_bounce_suppresses_and_buffers(self, resend_hook, monkeypatch):
        inserts = []
        monkeypatch.setattr(resend_hook.sb, "select", lambda *a, **k: [])
        monkeypatch.setattr(resend_hook.sb, "update", lambda *a, **k: [])
        monkeypatch.setattr(resend_hook.sb, "insert",
                            lambda table, row, **k: inserts.append((table, row)) or [row])

        payload = {"type": "email.bounced",
                   "data": {"email_id": "re_orphan", "to": ["Bad Lead <bad@lead.com>"]}}
        out = resend_hook._record(payload)
        assert out.get("buffered") is True
        supp = [r for t, r in inserts if t == "suppressions"]
        assert supp and supp[0]["email"] == "bad@lead.com" and supp[0]["reason"] == "bounced"


# ─────────── C3: scrape insert-only ───────────
class TestScrapeInsertOnly:
    def test_upsert_leads_is_insert_only_and_counts_true_inserts(self, daily_scrape, monkeypatch):
        def fake_insert(table, rows, *, on_conflict=None, ignore_duplicates=False):
            assert ignore_duplicates is True
            assert on_conflict == "campaign_id,business"
            return rows[:1]  # only 1 of 3 was genuinely new

        monkeypatch.setattr(daily_scrape.sb, "insert", fake_insert)
        n = daily_scrape._upsert_leads([{"a": 1}, {"a": 2}, {"a": 3}])
        assert n == 1
