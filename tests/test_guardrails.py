"""Unit tests for guardrail layer (cost, quality, input validation, concurrency)."""

from unittest.mock import patch, MagicMock

import pytest

from utils.guardrails import (
    CostGuardrail,
    QualityTracker,
    InputValidator,
    ConcurrencyLimiter,
)
from utils.exceptions import CostLimitExceededError, InputValidationError
from config import Config


class TestCostGuardrail:
    def test_charge_within_budget_is_allowed(self):
        cg = CostGuardrail()
        allowed, reason = cg.check("run-1", "claude-sonnet-4-6")
        assert allowed is True
        assert reason is None

    def test_warns_at_80_percent(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_TOKENS_PER_RUN", 100)
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_COST_PER_RUN", 1.0)
        cg = CostGuardrail()
        cg.charge("run-warn", 80, 0.0)
        allowed, reason = cg.check("run-warn", "claude-sonnet-4-6")
        assert allowed is True
        assert cg._runs["run-warn"].warned_80 is True

    def test_hard_stops_at_100_percent_tokens(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_TOKENS_PER_RUN", 100)
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_COST_PER_RUN", 100.0)
        cg = CostGuardrail()
        cg.charge("run-stop", 101, 0.0)
        allowed, reason = cg.check("run-stop", "claude-sonnet-4-6")
        assert allowed is False
        assert "Token budget exceeded" in reason

    def test_hard_stops_at_100_percent_cost(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_TOKENS_PER_RUN", 1_000_000)
        monkeypatch.setattr(Config, "GUARDRAIL_MAX_COST_PER_RUN", 1.0)
        cg = CostGuardrail()
        cg.charge("run-cost", 0, 1.01)
        allowed, reason = cg.check("run-cost", "claude-sonnet-4-6")
        assert allowed is False
        assert "Cost budget exceeded" in reason

    def test_end_run_purges_state(self):
        cg = CostGuardrail()
        cg.charge("run-purge", 50, 0.5)
        assert "run-purge" in cg._runs
        cg.end_run("run-purge")
        assert "run-purge" not in cg._runs


class TestQualityTracker:
    def test_not_degraded_with_good_scores(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_QUALITY_SCORE_THRESHOLD", 5.0)
        qt = QualityTracker(window_size=5)
        qt.record("r1", 7.0)
        qt.record("r2", 8.0)
        qt.record("r3", 9.0)
        assert qt.is_degraded() is False

    def test_global_degraded_after_three_low_scores(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_QUALITY_SCORE_THRESHOLD", 5.0)
        qt = QualityTracker(window_size=5)
        qt.record("r1", 3.0)
        qt.record("r2", 4.0)
        qt.record("r3", 2.0)
        assert qt.is_degraded() is True

    def test_per_pdf_degraded_after_two_low_scores(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_QUALITY_SCORE_THRESHOLD", 5.0)
        qt = QualityTracker(window_size=5)
        pdf = "/tmp/test.pdf"
        qt.record_pdf_score(pdf, 4.0)
        qt.record_pdf_score(pdf, 3.0)
        assert qt.is_degraded(pdf) is True

    def test_per_pdf_reset_clears_degradation(self, monkeypatch):
        monkeypatch.setattr(Config, "GUARDRAIL_QUALITY_SCORE_THRESHOLD", 5.0)
        qt = QualityTracker(window_size=5)
        pdf = "/tmp/test.pdf"
        qt.record_pdf_score(pdf, 4.0)
        qt.record_pdf_score(pdf, 3.0)
        assert qt.is_degraded(pdf) is True
        qt.reset_pdf(pdf)
        assert qt.is_degraded(pdf) is False


class TestInputValidator:
    def test_missing_pdf_raises(self, tmp_path):
        missing = str(tmp_path / "missing.pdf")
        with pytest.raises(InputValidationError, match="not found"):
            InputValidator.validate(missing)

    def test_empty_pdf_raises(self, tmp_path):
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"")
        with pytest.raises(InputValidationError, match="empty"):
            InputValidator.validate(str(empty))

    def test_corrupted_pdf_raises(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with pytest.raises(InputValidationError, match="corrupted or unreadable"):
            InputValidator.validate(str(bad))

    def test_pdf_with_zero_pages_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "no_pages.pdf"
        bad.write_bytes(b"dummy")
        fake_page = MagicMock()
        fake_page.extract_text.return_value = ""
        fake_pdf = MagicMock()
        fake_pdf.pages = []
        with patch("pdfplumber.open", return_value=fake_pdf):
            with pytest.raises(InputValidationError, match="0 pages"):
                InputValidator.validate(str(bad))

    def test_valid_pdf_passes(self, tmp_path):
        valid = tmp_path / "valid.pdf"
        valid.write_bytes(b"dummy pdf bytes")
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Assets 1000"
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = MagicMock(return_value=fake_pdf)
        fake_pdf.__exit__ = MagicMock(return_value=False)
        with patch("pdfplumber.open", return_value=fake_pdf):
            # Should not raise
            InputValidator.validate(str(valid))


class TestConcurrencyLimiter:
    def test_acquire_and_release(self):
        cl = ConcurrencyLimiter(max_concurrent=1)
        assert cl.acquire(timeout=1.0) is True
        cl.release()

    def test_acquire_timeout_when_at_limit(self):
        cl = ConcurrencyLimiter(max_concurrent=1)
        cl.acquire(timeout=1.0)
        # Second acquire should time out immediately (or very quickly)
        assert cl.acquire(timeout=0.1) is False
        cl.release()
