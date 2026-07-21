"""Characterization: _test_guard, strip_dashes, passes_open_gate (lib/sequence.py)."""
import pytest

from lib import sequence as seq


# ─────────── _test_guard (F02 recipient rewrite) ───────────
class TestTestGuard:
    @pytest.mark.parametrize("flag", ["1", "true", "yes", "on", " ON ", "TRUE"])
    def test_on_rewrites_any_recipient(self, monkeypatch, flag):
        monkeypatch.setenv("LEADGEN_TEST_MODE", flag)
        monkeypatch.setenv("LEADGEN_TEST_RECIPIENT", "delivered@resend.dev")
        assert seq._test_guard("real.lead@example.com") == "delivered@resend.dev"

    def test_on_default_recipient_is_admin(self, monkeypatch):
        monkeypatch.setenv("LEADGEN_TEST_MODE", "1")
        monkeypatch.delenv("LEADGEN_TEST_RECIPIENT", raising=False)
        assert seq._test_guard("real.lead@example.com") == "admin@oltaflock.ai"

    def test_off_is_passthrough(self, monkeypatch):
        monkeypatch.delenv("LEADGEN_TEST_MODE", raising=False)
        assert seq._test_guard("real.lead@example.com") == "real.lead@example.com"

    @pytest.mark.parametrize("flag", ["0", "false", "no", "off", ""])
    def test_non_truthy_values_are_passthrough(self, monkeypatch, flag):
        monkeypatch.setenv("LEADGEN_TEST_MODE", flag)
        assert seq._test_guard("real.lead@example.com") == "real.lead@example.com"


# ─────────── strip_dashes (no em/en dashes, ever) ───────────
class TestStripDashes:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # em dash becomes a comma clause break, spaced or not
            ("before — after", "before, after"),
            ("before—after", "before, after"),
            # en dash becomes a hyphen (ranges like 2-3 survive)
            ("range 2–3 months", "range 2-3 months"),
            # figure dash / horizontal bar / two-em dash all become clause breaks
            ("fig ‒ dash", "fig, dash"),
            ("horiz ― bar", "horiz, bar"),
            ("two ⸺ em", "two, em"),
            # artifact cleanup: ", ," collapses, ", ." becomes "."
            ("a —, b", "a, b"),
            ("a — . b", "a. b"),
            # characterized quirk: a trailing em dash leaves a trailing comma
            ("trailing—", "trailing,"),
            # plain text untouched; regular hyphens untouched
            ("hello", "hello"),
            ("well-known fix", "well-known fix"),
        ],
    )
    def test_characterized_outputs(self, raw, expected):
        assert seq.strip_dashes(raw) == expected

    def test_falsy_inputs_pass_through(self):
        assert seq.strip_dashes("") == ""
        assert seq.strip_dashes(None) is None


# ─────────── passes_open_gate ───────────
class TestPassesOpenGate:
    def test_gate_constant_is_step_4(self):
        # Pinned via LEADGEN_OPEN_GATE_FROM_STEP=4 in conftest (also the default).
        assert seq.OPEN_GATE_FROM_STEP == 4

    @pytest.mark.parametrize("step", [1, 2, 3])
    def test_steps_below_gate_always_pass(self, step):
        assert seq.passes_open_gate({"opens": 0}, step) is True
        assert seq.passes_open_gate({}, step) is True

    @pytest.mark.parametrize("step", [4, 5, 7, 15])
    def test_steps_at_or_after_gate_require_opens(self, step):
        assert seq.passes_open_gate({"opens": 0}, step) is False
        assert seq.passes_open_gate({"opens": 1}, step) is True
        assert seq.passes_open_gate({"opens": 3}, step) is True

    def test_missing_or_null_opens_is_treated_as_zero(self):
        assert seq.passes_open_gate({}, 4) is False
        assert seq.passes_open_gate({"opens": None}, 4) is False
