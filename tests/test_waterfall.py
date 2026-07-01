"""Waterfall engine tests — no network. Run: python3 tests/test_waterfall.py

Stubs the provider registry with deterministic fakes so we exercise the
engine's ordering / stop-at-first / provenance logic, not live HTTP.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web import waterfall as wf
from src.web.waterfall import Provider, ProviderResult, run_waterfall, normalize_config


class _Fake(Provider):
    """A provider that returns a canned result and records that it was called."""
    def __init__(self, key, provides, fields, calls, cost=1.0, requires_env=()):
        super().__init__(key, key.title(), provides, cost=cost, requires_env=requires_env)
        self._fields = fields
        self._calls = calls

    def fetch(self, lead):
        self._calls.append(self.key)
        return ProviderResult(fields=dict(self._fields), confidence=0.9, cost=self.cost,
                              note="fake hit" if self._fields else "miss")


def _install(providers):
    """Swap the module registry for a list of fakes; return the call log."""
    wf.REGISTRY = {p.key: p for p in providers}
    wf.DEFAULT_ORDER = [p.key for p in providers]
    return wf.REGISTRY


def test_stop_at_first():
    calls = []
    _install([
        _Fake("a", ("email",), {"email": "a@x.com"}, calls),
        _Fake("b", ("email",), {"email": "b@x.com"}, calls),
    ])
    out = run_waterfall({"business_name": "Acme"},
                        {"order": ["a", "b"], "stop_at_first": True}, targets=("email",))
    assert out["fields"]["email"] == "a@x.com", out["fields"]
    assert out["provenance"]["email"]["source"] == "a"
    assert calls == ["a"], f"second provider should be skipped, got {calls}"
    assert out["cost"] == 1.0
    print("✓ stop_at_first: chain halts on first hit")


def test_run_all_keeps_first_but_calls_all():
    calls = []
    _install([
        _Fake("a", ("email",), {"email": "a@x.com"}, calls),
        _Fake("b", ("email",), {"email": "b@x.com"}, calls),
    ])
    out = run_waterfall({"business_name": "Acme"},
                        {"order": ["a", "b"], "stop_at_first": False}, targets=("email",))
    assert calls == ["a", "b"], f"run-all should call every provider, got {calls}"
    # first non-empty value still wins; later providers never overwrite
    assert out["fields"]["email"] == "a@x.com"
    print("✓ run_all: every provider called, first value wins")


def test_fallback_when_first_misses():
    calls = []
    _install([
        _Fake("a", ("email",), {}, calls),                 # miss
        _Fake("b", ("email",), {"email": "b@x.com"}, calls),
    ])
    out = run_waterfall({"business_name": "Acme"},
                        {"order": ["a", "b"], "stop_at_first": True}, targets=("email",))
    assert out["fields"]["email"] == "b@x.com"
    assert calls == ["a", "b"], calls
    print("✓ fallback: empty rung falls through to the next")


def test_preexisting_field_short_circuits():
    calls = []
    _install([_Fake("a", ("email",), {"email": "a@x.com"}, calls)])
    out = run_waterfall({"business_name": "Acme", "email": "known@x.com"},
                        {"order": ["a"], "stop_at_first": True}, targets=("email",))
    assert calls == [], "provider must not run when target is already present"
    assert out["resolved"] == ["email"]
    assert "email" not in out["fields"], "no NEW field should be reported"
    print("✓ pre-existing field short-circuits the chain")


def test_unavailable_provider_skipped():
    calls = []
    _install([
        _Fake("paid", ("email",), {"email": "p@x.com"}, calls,
              requires_env=("DEFINITELY_UNSET_KEY_XYZ",)),
        _Fake("free", ("email",), {"email": "f@x.com"}, calls),
    ])
    out = run_waterfall({"business_name": "Acme"},
                        {"order": ["paid", "free"], "stop_at_first": True}, targets=("email",))
    assert out["fields"]["email"] == "f@x.com"
    assert calls == ["free"], f"key-gated provider must be skipped, got {calls}"
    print("✓ provider missing its API key is skipped gracefully")


def test_normalize_config_fills_missing():
    _install([_Fake("a", ("email",), {}, []), _Fake("b", ("email",), {}, [])])
    cfg = normalize_config({"order": ["b"], "enabled": {"b": False}})
    assert cfg["order"] == ["b", "a"], cfg["order"]  # missing keys appended
    assert cfg["enabled"] == {"b": False, "a": True}
    assert cfg["stop_at_first"] is True
    print("✓ normalize_config: appends missing providers, defaults enabled/on")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} waterfall tests passed.")
