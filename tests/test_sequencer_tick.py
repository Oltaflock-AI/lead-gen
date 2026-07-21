"""Characterization: _process_one claim-lock and suppression pause
(api/cron/sequencer_tick.py). The module is loaded from its file path by the
session-scoped `tick` fixture in conftest (api/cron is not a package).

tick.sb and tick.seq are the shared lib.supabase / lib.sequence modules, so
monkeypatching attributes on them covers every call site.
"""
import pytest


DUE_SEQ = {
    "id": 7,
    "lead_id": 1,
    "campaign_id": 3,
    "current_step": 1,
    "opens": 0,
    "next_send_at": "2026-07-20T00:00:00+00:00",
}
ACTIVE = {3: {"id": 3, "offer_brief": None, "region": None, "sequence_config": None}}


class SendSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"resend_id": "re_spy"}


class TestClaimLock:
    def test_empty_update_means_claimed_by_other_and_no_send(self, tick, monkeypatch):
        updates = []

        def fake_update(table, match, patch):
            updates.append({"table": table, "match": match, "patch": patch})
            return []  # 0 rows matched -> another runner already claimed it

        def fail_select(*a, **k):
            raise AssertionError("lead must not be fetched after a lost claim")

        spy = SendSpy()
        monkeypatch.setattr(tick.sb, "update", fake_update)
        monkeypatch.setattr(tick.sb, "select", fail_select)
        monkeypatch.setattr(tick.seq, "send_email", spy)

        result = tick._process_one(dict(DUE_SEQ), ACTIVE, set())

        assert result == {"seq": 7, "skipped": "claimed-by-other"}
        assert spy.calls == []
        # The claim is optimistic: it matches on the CURRENT next_send_at value
        # (id + timestamp) so a concurrent runner's earlier claim makes this
        # update touch zero rows.
        assert len(updates) == 1
        assert updates[0]["table"] == "sequences"
        assert updates[0]["match"] == {"id": 7, "next_send_at": DUE_SEQ["next_send_at"]}
        assert "next_send_at" in updates[0]["patch"]


class TestSuppressionPause:
    def test_suppressed_lead_paused_and_never_sent(self, tick, monkeypatch):
        updates = []

        def fake_update(table, match, patch):
            updates.append({"table": table, "match": match, "patch": patch})
            return [{"id": 7}]  # claim succeeds; later updates also "succeed"

        def fake_select(table, params=None, *, limit=None):
            if table == "leads":
                # Mixed case on purpose: the tick lowercases before the lookup.
                return [{"id": 1, "email": "OptOut@Lead.com", "campaign_id": 3}]
            return []

        spy = SendSpy()
        monkeypatch.setattr(tick.sb, "update", fake_update)
        monkeypatch.setattr(tick.sb, "select", fake_select)
        monkeypatch.setattr(tick.seq, "send_email", spy)

        result = tick._process_one(dict(DUE_SEQ), ACTIVE, {"optout@lead.com"})

        assert result == {"seq": 7, "skipped": "suppressed"}
        assert spy.calls == []
        pause = updates[-1]
        assert pause["match"] == {"id": 7}
        assert pause["patch"] == {
            "status": "paused",
            "paused_reason": "suppressed",
            "next_send_at": None,
        }

    def test_clean_lead_is_not_paused_by_suppression_branch(self, tick, monkeypatch):
        """Control case: a non-suppressed lead sails past the suppression check
        (it then proceeds toward drafting; we stop it at the engagement lookup
        to keep this test focused on the suppression branch alone)."""

        class StopHere(Exception):
            pass

        def fake_update(table, match, patch):
            return [{"id": 7}]

        def fake_select(table, params=None, *, limit=None):
            if table == "leads":
                return [{"id": 1, "email": "clean@lead.com", "campaign_id": 3}]
            return []

        def stop(*a, **k):
            raise StopHere()

        spy = SendSpy()
        monkeypatch.setattr(tick.sb, "update", fake_update)
        monkeypatch.setattr(tick.sb, "select", fake_select)
        monkeypatch.setattr(tick.seq, "engagement_detail", stop)
        monkeypatch.setattr(tick.seq, "send_email", spy)

        with pytest.raises(StopHere):
            tick._process_one(dict(DUE_SEQ), ACTIVE, {"optout@lead.com"})
        assert spy.calls == []  # got past suppression, but nothing was sent
