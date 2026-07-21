"""One-click unsubscribe: token binding, RFC 8058 headers, endpoint behavior."""
import base64
import importlib.util
import pathlib

import pytest

from lib import sequence as seq

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def dashboard():
    spec = importlib.util.spec_from_file_location("api_index", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _url_for(email: str) -> str:
    e = base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")
    return f"/unsubscribe?e={e}&t={seq.unsub_token(email)}"


def test_token_deterministic_and_case_insensitive():
    assert seq.unsub_token("A@b.com") == seq.unsub_token("a@b.com")
    assert seq.unsub_token("a@b.com") != seq.unsub_token("c@d.com")


def test_unsub_url_contains_token():
    url = seq.unsub_url("lead@example.com")
    assert url.startswith(seq.PUBLIC_URL + "/unsubscribe?e=")
    assert seq.unsub_token("lead@example.com") in url


def test_headers_are_one_click_https_only():
    h = seq._unsub_headers("lead@example.com")
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "https://" in h["List-Unsubscribe"]
    assert "mailto:" not in h["List-Unsubscribe"]


def test_get_invalid_token_400(dashboard):
    client = dashboard.app.test_client()
    e = base64.urlsafe_b64encode(b"x@y.com").decode().rstrip("=")
    assert client.get(f"/unsubscribe?e={e}&t=deadbeef").status_code == 400
    assert client.get("/unsubscribe").status_code == 400


def test_get_valid_shows_confirm(dashboard):
    r = dashboard.app.test_client().get(_url_for("lead@example.com"))
    assert r.status_code == 200
    assert b"lead@example.com" in r.data


def test_post_valid_suppresses_and_pauses(dashboard, monkeypatch):
    calls = {"insert": [], "update": []}

    class FakeSB:
        @staticmethod
        def insert(table, row, **kw):
            calls["insert"].append((table, row, kw))
            return [row]

        @staticmethod
        def select(table, params=None, *, limit=None):
            if table == "leads":
                return [{"id": 7}]
            if table == "sequences":
                return [{"id": 42}]
            return []

        @staticmethod
        def update(table, match, patch):
            calls["update"].append((table, match, patch))
            return [{}]

    monkeypatch.setattr(dashboard, "sb", FakeSB)
    r = dashboard.app.test_client().post(_url_for("Lead@Example.com"))
    assert r.status_code == 200
    table, row, kw = calls["insert"][0]
    assert table == "suppressions"
    assert row["email"] == "lead@example.com"
    assert row["source"] == "one-click"
    assert kw.get("on_conflict") == "email"
    assert calls["update"][0][2]["paused_reason"] == "unsubscribed"


def test_post_suppression_failure_returns_500(dashboard, monkeypatch):
    class FailSB:
        @staticmethod
        def insert(*a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(dashboard, "sb", FailSB)
    r = dashboard.app.test_client().post(_url_for("lead@example.com"))
    assert r.status_code == 500
