"""Characterization: send_manual and send_email (lib/sequence.py).

All HTTP is mocked via the resend_post recorder (conftest); the autouse
network guard blocks anything that slips past. LEADGEN_TEST_MODE=1 is set
globally in conftest, so successful sends are rewritten to the sandbox
recipient delivered@resend.dev — asserted explicitly below.
"""
import pytest

from lib import sequence as seq


def _suppress(monkeypatch, *emails):
    rows = [{"email": e} for e in emails]
    monkeypatch.setattr(seq.sb, "select", lambda *a, **k: rows)


# ─────────── send_manual ───────────
class TestSendManual:
    def test_blocked_when_recipient_suppressed_no_http(self, monkeypatch, resend_post):
        _suppress(monkeypatch, "optout@lead.com")
        result = seq.send_manual("optout@lead.com", "subject", "body", 0)
        assert result == {"error": "suppressed: recipient is on the suppression list"}
        assert resend_post.calls == []

    def test_suppression_wins_even_in_test_mode(self, monkeypatch, resend_post):
        # The suppression check runs BEFORE the _test_guard rewrite: a
        # suppressed address is refused outright, never redirected to sandbox.
        _suppress(monkeypatch, "optout@lead.com")
        result = seq.send_manual("OptOut@Lead.com", "s", "b", 0)
        assert "error" in result and result["error"].startswith("suppressed")
        assert resend_post.calls == []

    def test_sends_when_not_suppressed(self, no_suppressions, resend_post):
        result = seq.send_manual("clean@lead.com", "subject", "body", 0)
        assert result == {"resend_id": "re_test_123"}
        assert len(resend_post.calls) == 1
        call = resend_post.calls[0]
        assert call["url"] == "https://api.resend.com/emails"
        # LEADGEN_TEST_MODE=1 (conftest) rewrites the recipient to the sandbox.
        assert call["json"]["to"] == ["delivered@resend.dev"]

    def test_idem_key_becomes_idempotency_header_exactly(self, no_suppressions, resend_post):
        seq.send_manual("clean@lead.com", "s", "b", 1, idem_key="blast-1-abc")
        headers = resend_post.calls[0]["headers"]
        assert headers["Idempotency-Key"] == "blast-1-abc"

    def test_no_idempotency_header_when_idem_key_none(self, no_suppressions, resend_post):
        seq.send_manual("clean@lead.com", "s", "b", 1, idem_key=None)
        headers = resend_post.calls[0]["headers"]
        assert "Idempotency-Key" not in headers

    def test_strip_dashes_applied_to_subject(self, no_suppressions, resend_post):
        seq.send_manual("clean@lead.com", "audit — quick question", "body", 0)
        payload = resend_post.calls[0]["json"]
        assert payload["subject"] == "audit, quick question"
        assert "—" not in payload["subject"]

    def test_strip_dashes_applied_to_body_text(self, no_suppressions, resend_post):
        seq.send_manual("clean@lead.com", "s", "we reply — fast", 0)
        payload = resend_post.calls[0]["json"]
        assert "—" not in payload["text"]
        assert "we reply, fast" in payload["text"]

    def test_no_recipient_errors_without_http(self, resend_post):
        assert seq.send_manual("", "s", "b", 0) == {"error": "no recipient"}
        assert resend_post.calls == []

    def test_seq_id_zero_omits_sequence_id_tag(self, no_suppressions, resend_post):
        # seq_id=0 would make the webhook FK-fail attaching events, so only the
        # kind tag is emitted; a real seq_id adds sequence_id + step tags.
        seq.send_manual("clean@lead.com", "s", "b", 0)
        assert resend_post.calls[0]["json"]["tags"] == [{"name": "kind", "value": "manual"}]

        seq.send_manual("clean@lead.com", "s", "b", 55)
        tags = resend_post.calls[1]["json"]["tags"]
        assert {"name": "sequence_id", "value": "55"} in tags
        assert {"name": "step", "value": "0"} in tags
        assert {"name": "kind", "value": "manual"} in tags


# ─────────── send_email (sequence path) ───────────
DRAFT = {"subject": "hello", "body": "the body"}


class TestSendEmail:
    def test_idempotency_key_is_seq_step_format(self, no_suppressions, resend_post):
        result = seq.send_email({"email": "clean@lead.com"}, DRAFT, 42, 3)
        assert result == {"resend_id": "re_test_123"}
        headers = resend_post.calls[0]["headers"]
        assert headers["Idempotency-Key"] == "seq-42-step-3"

    def test_blocked_when_lead_email_suppressed_no_http(self, monkeypatch, resend_post):
        _suppress(monkeypatch, "optout@lead.com")
        result = seq.send_email({"email": "OptOut@Lead.com"}, DRAFT, 42, 3)
        assert result == {"error": "suppressed: recipient is on the suppression list"}
        assert resend_post.calls == []

    def test_missing_lead_email_errors_without_http(self, resend_post):
        assert seq.send_email({}, DRAFT, 42, 3) == {"error": "lead has no email"}
        assert resend_post.calls == []

    def test_missing_resend_key_errors_without_http(self, monkeypatch, resend_post):
        monkeypatch.setattr(seq, "RESEND_API_KEY", "")
        result = seq.send_email({"email": "clean@lead.com"}, DRAFT, 42, 3)
        assert result == {"error": "RESEND_API_KEY missing"}
        assert resend_post.calls == []

    def test_recipient_rewritten_by_test_guard(self, no_suppressions, resend_post):
        seq.send_email({"email": "real.lead@example.com"}, DRAFT, 42, 3)
        assert resend_post.calls[0]["json"]["to"] == ["delivered@resend.dev"]
