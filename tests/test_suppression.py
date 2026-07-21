"""Characterization: is_suppressed / _suppressed_set cache (lib/sequence.py).

The module-level _SUPPRESS_CACHE (60s TTL) is reset around every test by the
autouse reset_suppress_cache fixture in conftest.py.
"""
import pytest

from lib import sequence as seq


def _mock_rows(monkeypatch, rows):
    calls = []

    def fake_select(table, params=None, *, limit=None):
        calls.append(table)
        return rows

    monkeypatch.setattr(seq.sb, "select", fake_select)
    return calls


class TestIsSuppressed:
    def test_true_for_listed_email(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("optout@lead.com") is True

    def test_case_insensitive_both_sides(self, monkeypatch):
        # Table rows are lowercased on load; the queried address is lowercased
        # (and stripped) on lookup — any casing combination matches.
        _mock_rows(monkeypatch, [{"email": "OptOut@Lead.COM"}])
        assert seq.is_suppressed("optout@lead.com") is True
        assert seq.is_suppressed("OPTOUT@LEAD.COM") is True
        assert seq.is_suppressed("  optout@lead.com  ") is True

    def test_false_for_unlisted_email(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("clean@lead.com") is False

    def test_false_when_table_empty(self, monkeypatch):
        _mock_rows(monkeypatch, [])
        assert seq.is_suppressed("anyone@lead.com") is False

    def test_fails_closed_when_db_read_raises(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("supabase down")

        monkeypatch.setattr(seq.sb, "select", boom)
        # A wrongly skipped send is recoverable; a send to an opted-out address
        # is not — so an unreadable table means "suppressed".
        assert seq.is_suppressed("anyone@lead.com") is True

    def test_null_and_empty_rows_are_ignored(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": None}, {"email": ""}, {}])
        assert seq.is_suppressed("anyone@lead.com") is False

    def test_empty_and_none_input(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("") is False
        assert seq.is_suppressed(None) is False


class TestSuppressCache:
    def test_cache_hit_within_ttl_skips_db(self, monkeypatch):
        calls = _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("optout@lead.com") is True
        # Swap the mock: within the 60s TTL the stale cached set still answers
        # and the DB is not re-queried (characterized serverless-reuse behavior).
        monkeypatch.setattr(seq.sb, "select", lambda *a, **k: [])
        assert seq.is_suppressed("optout@lead.com") is True
        assert calls == ["suppressions"]  # exactly one DB read

    def test_cache_reset_forces_fresh_read(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("optout@lead.com") is True
        seq._SUPPRESS_CACHE = None
        monkeypatch.setattr(seq.sb, "select", lambda *a, **k: [])
        assert seq.is_suppressed("optout@lead.com") is False

    def test_stale_cache_expires_after_ttl(self, monkeypatch):
        _mock_rows(monkeypatch, [{"email": "optout@lead.com"}])
        assert seq.is_suppressed("optout@lead.com") is True
        # Age the cache past the 60s TTL instead of sleeping.
        ts, emails = seq._SUPPRESS_CACHE
        seq._SUPPRESS_CACHE = (ts - (seq._SUPPRESS_TTL_S + 1), emails)
        monkeypatch.setattr(seq.sb, "select", lambda *a, **k: [])
        assert seq.is_suppressed("optout@lead.com") is False
