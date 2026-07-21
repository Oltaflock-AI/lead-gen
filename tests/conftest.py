"""Shared fixtures for the send-safety characterization suite.

CRITICAL ordering: lib/supabase.py reads os.environ["SUPABASE_URL"] and
os.environ["SUPABASE_SERVICE_KEY"] at IMPORT time, so the dummy env vars below
must be assigned before any `lib` module is imported. Values are force-set (not
setdefault) so a developer shell with real credentials can never leak into the
tests. LEADGEN_TEST_MODE=1 is a belt-and-braces safety net on top of the
network guard: even if a send slipped through, _test_guard would rewrite the
recipient to the resend.dev sandbox address.
"""
import importlib.util
import os
import sys

os.environ["SUPABASE_URL"] = "https://supabase.test.invalid"
os.environ["SUPABASE_SERVICE_KEY"] = "test-service-key"
os.environ["RESEND_API_KEY"] = "re_test_dummy_key"
os.environ["LEADGEN_TEST_MODE"] = "1"
os.environ["LEADGEN_TEST_RECIPIENT"] = "delivered@resend.dev"
os.environ["LEADGEN_OPEN_GATE_FROM_STEP"] = "4"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
import requests as _requests

from lib import sequence as seq  # noqa: E402  (env vars above must come first)


# ─────────── Hard network kill-switch ───────────
@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Every real HTTP request funnels through HTTPAdapter.send — block it.

    Tests that expect an HTTP call monkeypatch requests.post with a recorder,
    which replaces the function reference before the adapter is ever reached.
    Anything else that tries to hit the wire fails loudly here.
    """

    def _blocked(self, request, *args, **kwargs):
        raise RuntimeError(
            f"blocked real network call in tests: {request.method} {request.url}"
        )

    monkeypatch.setattr(_requests.adapters.HTTPAdapter, "send", _blocked)


@pytest.fixture(autouse=True)
def reset_suppress_cache():
    """lib.sequence keeps a module-level 60s-TTL suppression cache; a stale
    entry from one test would silently drive the next test's suppression
    decisions. Reset on both sides of every test."""
    seq._SUPPRESS_CACHE = None
    yield
    seq._SUPPRESS_CACHE = None


# ─────────── Resend fakes ───────────
class FakeResendResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"id": "re_test_123"}


class PostRecorder:
    """Stand-in for requests.post that records every call."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response if response is not None else FakeResendResponse()

    def __call__(self, url, *, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append(
            {"url": url, "headers": dict(headers or {}), "json": json, "timeout": timeout}
        )
        return self._response


@pytest.fixture
def resend_post(monkeypatch):
    """Replace lib.sequence.requests.post with a recording fake."""
    recorder = PostRecorder()
    monkeypatch.setattr(seq.requests, "post", recorder)
    return recorder


@pytest.fixture
def no_suppressions(monkeypatch):
    """Suppression table reads succeed and are empty."""
    monkeypatch.setattr(seq.sb, "select", lambda *a, **k: [])


# ─────────── sequencer_tick loader ───────────
# api/ and api/cron/ are not packages (no __init__.py — Vercel function layout),
# so the module is loaded straight from its file path.
def _load_sequencer_tick():
    path = os.path.join(ROOT, "api", "cron", "sequencer_tick.py")
    spec = importlib.util.spec_from_file_location("sequencer_tick_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def tick():
    return _load_sequencer_tick()
