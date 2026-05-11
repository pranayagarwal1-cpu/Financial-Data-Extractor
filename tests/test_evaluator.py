"""Smoke tests for evaluator pure-function logic (numeric precheck + equation checks)."""

from decimal import Decimal

import pytest

from agents.evaluator import (
    _build_retry_context,
    _calculate_missing_ratio,
    _has_required_sections,
    _parse_amount,
    _run_equation_checks,
    _run_numeric_precheck,
    CheckFinding,
    PenaltyLedger,
    SectionResult,
)
from utils.vlm_utils import StatementType


class TestParseAmount:
    @pytest.mark.parametrize("raw,expected", [
        ("100", Decimal("100")),
        ("1,234.56", Decimal("1234.56")),
        ("$1,000", Decimal("1000")),
        ("(500)", Decimal("-500")),    # parentheses → negative
        ("(1,234)", Decimal("-1234")),
        ("  -50.5  ", Decimal("-50.5")),
        ("0", Decimal("0")),
    ])
    def test_parses_common_formats(self, raw, expected):
        assert _parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "null", "abc", "—"])
    def test_returns_none_for_missing_or_garbage(self, raw):
        assert _parse_amount(raw) is None


class TestNumericPrecheck:
    def test_clean_data_scores_high(self):
        data = {"sections": [{
            "name": "Revenue", "rows": [
                {"label": "Sales", "values": ["100", "120"]},
                {"label": "Other", "values": ["50", "60"]},
            ]
        }]}
        score, _ = _run_numeric_precheck(data, StatementType.INCOME_STATEMENT)
        assert score == 10.0

    def test_unparsable_values_drop_score(self):
        data = {"sections": [{
            "name": "Revenue", "rows": [
                {"label": "Sales", "values": ["100", "TBD"]},
                {"label": "Other", "values": ["FIXME", "GARBAGE"]},
            ]
        }]}
        score, feedback = _run_numeric_precheck(data, StatementType.INCOME_STATEMENT)
        assert score < 5.0
        assert "unparsable" in feedback

    def test_no_values_returns_zero(self):
        score, feedback = _run_numeric_precheck({"sections": []}, StatementType.BALANCE_SHEET)
        assert score == 0.0
        assert "No numeric values" in feedback

    def test_blank_and_null_are_not_counted_as_unparsable(self):
        data = {"sections": [{
            "name": "Revenue", "rows": [
                {"label": "Sales", "values": ["100", "", None, "null"]},
            ]
        }]}
        score, _ = _run_numeric_precheck(data, StatementType.INCOME_STATEMENT)
        assert score == 10.0


class TestMissingRatio:
    def test_counts_blank_null_and_none(self):
        data = {"sections": [{
            "name": "X", "rows": [
                {"label": "a", "values": ["1", "", None, "null"]},
            ]
        }]}
        assert _calculate_missing_ratio(data) == pytest.approx(0.75)

    def test_empty_data_returns_full_missing(self):
        assert _calculate_missing_ratio({"sections": []}) == 1.0


class TestRequiredSections:
    def test_balance_sheet_with_all_sections_passes(self):
        data = {"sections": [
            {"name": "Assets"}, {"name": "Liabilities"}, {"name": "Stockholders Equity"},
        ]}
        ok, missing = _has_required_sections(data, StatementType.BALANCE_SHEET)
        assert ok
        assert missing == []

    def test_balance_sheet_missing_equity_flagged(self):
        data = {"sections": [{"name": "Assets"}, {"name": "Liabilities"}]}
        ok, missing = _has_required_sections(data, StatementType.BALANCE_SHEET)
        assert not ok
        assert "EQUITY" in missing

    def test_cash_flow_with_all_activities_passes(self):
        data = {"sections": [
            {"name": "Operating Activities"},
            {"name": "Investing Activities"},
            {"name": "Financing Activities"},
        ]}
        ok, _ = _has_required_sections(data, StatementType.CASH_FLOW)
        assert ok


class TestEquationChecks:
    def test_balance_sheet_balances(self):
        data = {"sections": [
            {"name": "Assets", "rows": [
                {"label": "Total Assets", "values": ["1000"], "is_subtotal": True},
            ]},
            {"name": "Liabilities", "rows": [
                {"label": "Total Liabilities", "values": ["400"], "is_subtotal": True},
            ]},
            {"name": "Equity", "rows": [
                {"label": "Total Equity", "values": ["600"], "is_subtotal": True},
            ]},
        ]}
        passed, _ = _run_equation_checks(data, StatementType.BALANCE_SHEET)
        assert passed

    def test_balance_sheet_imbalance_fails(self):
        # Use large values so the $1K materiality gate does not auto-pass
        data = {"sections": [
            {"name": "Assets", "rows": [
                {"label": "Total Assets", "values": ["10000"], "is_subtotal": True},
            ]},
            {"name": "Liabilities", "rows": [
                {"label": "Total Liabilities", "values": ["4000"], "is_subtotal": True},
            ]},
            {"name": "Equity", "rows": [
                {"label": "Total Equity", "values": ["5000"], "is_subtotal": True},  # off by 1000
            ]},
        ]}
        passed, feedback = _run_equation_checks(data, StatementType.BALANCE_SHEET)
        assert not passed
        assert "does not balance" in feedback

    def test_balance_sheet_within_one_percent_tolerance_passes(self):
        # 1000 vs 400 + 595 = 995 → 0.5% diff, under 1% tolerance
        data = {"sections": [
            {"name": "Assets", "rows": [
                {"label": "Total Assets", "values": ["1000"], "is_subtotal": True},
            ]},
            {"name": "Liabilities", "rows": [
                {"label": "Total Liabilities", "values": ["400"], "is_subtotal": True},
            ]},
            {"name": "Equity", "rows": [
                {"label": "Total Equity", "values": ["595"], "is_subtotal": True},
            ]},
        ]}
        passed, _ = _run_equation_checks(data, StatementType.BALANCE_SHEET)
        assert passed

    def test_income_statement_gross_profit_reconciles(self):
        data = {"sections": [
            {"name": "Revenue", "rows": [
                {"label": "Total Revenue", "values": ["1000"], "is_subtotal": True},
            ]},
            {"name": "COGS", "rows": [
                {"label": "Cost of Goods Sold", "values": ["400"], "is_subtotal": True},
                {"label": "Gross Profit", "values": ["600"], "is_subtotal": True},
            ]},
        ]}
        passed, _ = _run_equation_checks(data, StatementType.INCOME_STATEMENT)
        assert passed

    def test_income_statement_gross_profit_mismatch_fails(self):
        # Use large values so the $1K materiality gate does not auto-pass
        data = {"sections": [
            {"name": "Revenue", "rows": [
                {"label": "Total Revenue", "values": ["10000"], "is_subtotal": True},
            ]},
            {"name": "COGS", "rows": [
                {"label": "Cost of Goods Sold", "values": ["4000"], "is_subtotal": True},
                {"label": "Gross Profit", "values": ["5000"], "is_subtotal": True},  # should be 6000
            ]},
        ]}
        passed, feedback = _run_equation_checks(data, StatementType.INCOME_STATEMENT)
        assert not passed
        assert "Gross Profit" in feedback

    def test_cash_flow_reconciles(self):
        data = {"sections": [
            {"name": "Operating", "rows": [
                {"label": "Net Change in Cash", "values": ["100"], "is_subtotal": True},
                {"label": "Cash at Beginning", "values": ["50"], "is_subtotal": True},
                {"label": "Cash at End", "values": ["150"], "is_subtotal": True},
            ]},
        ]}
        passed, _ = _run_equation_checks(data, StatementType.CASH_FLOW)
        assert passed


class TestPenaltyLedger:
    def test_first_charge_applies(self):
        ledger = PenaltyLedger()
        assert ledger.charge("A6", "row_1") is True
        assert ledger.charge("A6", "row_1") is False

    def test_different_keys_independent(self):
        ledger = PenaltyLedger()
        assert ledger.charge("A6", "row_1") is True
        assert ledger.charge("A6", "row_2") is True
        assert ledger.charge("D3", "row_1") is True


class TestRetryContext:
    def test_passed_does_not_retry(self):
        coverage = SectionResult(8.0, [CheckFinding("A1", "PASS", "ok")])
        fmt = SectionResult(9.0, [CheckFinding("B1", "PASS", "ok")])
        struct = SectionResult(9.0, [CheckFinding("C1", "PASS", "ok")])
        content = SectionResult(8.0, [CheckFinding("D1", "PASS", "ok")])
        ctx = _build_retry_context(coverage, fmt, struct, content, 8.5, passed=True)
        assert ctx["should_retry"] is False

    def test_hard_fail_triggers_retry(self):
        coverage = SectionResult(8.0, [CheckFinding("A1", "FAIL", "missing")])
        fmt = SectionResult(9.0, [CheckFinding("B1", "PASS", "ok")])
        struct = SectionResult(9.0, [CheckFinding("C1", "PASS", "ok")])
        content = SectionResult(8.0, [CheckFinding("D1", "PASS", "ok")])
        ctx = _build_retry_context(coverage, fmt, struct, content, 5.0, passed=False)
        assert ctx["should_retry"] is True
        assert "A1" in ctx["hard_fail_checks"]

    def test_never_retry_checks_block_retry(self):
        coverage = SectionResult(8.0, [CheckFinding("A3", "FAIL", "page coverage")])
        fmt = SectionResult(9.0, [CheckFinding("B1", "PASS", "ok")])
        struct = SectionResult(9.0, [CheckFinding("C7", "ADVISORY", "orientation")])
        content = SectionResult(8.0, [CheckFinding("D1", "PASS", "ok")])
        ctx = _build_retry_context(coverage, fmt, struct, content, 7.0, passed=False)
        assert ctx["only_never_retry_fails"] is True
        # A3 is a never-retry check, so retry should be blocked even though score > 6
        assert ctx["should_retry"] is False

    def test_missed_rows_prompt_addendum(self):
        coverage = SectionResult(5.0, [CheckFinding("A5", "FAIL", "missed")])
        fmt = SectionResult(9.0, [CheckFinding("B1", "PASS", "ok")])
        struct = SectionResult(9.0, [CheckFinding("C1", "PASS", "ok")])
        content = SectionResult(8.0, [CheckFinding("D1", "PASS", "ok")])
        ctx = _build_retry_context(coverage, fmt, struct, content, 5.0, passed=False)
        assert "missed_or_hallucinated_rows" in ctx["categories"]
        assert "Re-examine" in ctx["targeted_prompt_addendum"]

    def test_equation_failure_prompt_addendum(self):
        coverage = SectionResult(8.0, [CheckFinding("A1", "PASS", "ok")])
        fmt = SectionResult(9.0, [CheckFinding("B1", "PASS", "ok")])
        struct = SectionResult(5.0, [CheckFinding("C1", "FAIL", "does not balance")])
        content = SectionResult(8.0, [CheckFinding("D1", "PASS", "ok")])
        ctx = _build_retry_context(coverage, fmt, struct, content, 5.0, passed=False)
        assert "equation_failure" in ctx["categories"]
        assert "accounting equation" in ctx["targeted_prompt_addendum"]
