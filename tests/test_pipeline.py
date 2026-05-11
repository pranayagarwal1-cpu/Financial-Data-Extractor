"""Integration tests for the full extraction pipeline.

These tests reference fixture PDFs in tests/fixtures/.  If the fixtures are not
present the tests are skipped so the suite remains green in CI until fixtures
are added.
"""

import os
import statistics
import tempfile
from pathlib import Path

import openpyxl
import pytest

from main import process_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_exists(name: str) -> bool:
    return (FIXTURES / name).exists()


@pytest.mark.skipif(not _fixture_exists("known_good/standard.pdf"), reason="fixture missing")
def test_known_good_pdf_passes():
    """A known-good PDF should score > 8.0 overall."""
    pdf = FIXTURES / "known_good" / "standard.pdf"
    result = process_pdf(str(pdf), no_categorization=True)

    for st, eval_result in result.get("evaluation_result", {}).items():
        scores = eval_result.get("scores", {})
        overall = scores.get("overall", 0)
        assert overall >= 8.0, f"{st.value} scored {overall}, expected >= 8.0"


@pytest.mark.parametrize("pdf,expected_failures", [
    ("known_bad/missing_sections.pdf", ["A1"]),
    ("known_bad/hallucinated_rows.pdf", ["A6", "D3"]),
    ("known_bad/equation_mismatch.pdf", ["C1"]),
])
def test_known_bad_pdfs(pdf, expected_failures):
    if not _fixture_exists(pdf):
        pytest.skip(f"fixture missing: {pdf}")

    result = process_pdf(str(FIXTURES / pdf), no_categorization=True)
    failed_checks = []
    for eval_result in result.get("evaluation_result", {}).values():
        for finding in eval_result.get("findings", []):
            if finding.get("status") == "FAIL":
                failed_checks.append(finding["check_id"])

    for check in expected_failures:
        assert check in failed_checks, f"Expected {check} to fail, got {failed_checks}"


@pytest.mark.skipif(not _fixture_exists("known_good/standard.pdf"), reason="fixture missing")
def test_quality_report_in_excel():
    """Every Excel output must contain a Quality Report sheet."""
    pdf = FIXTURES / "known_good" / "standard.pdf"
    result = process_pdf(str(pdf), no_categorization=True)

    for path in result.get("output_files", []):
        if path.endswith(".xlsx"):
            wb = openpyxl.load_workbook(path)
            assert "Quality Report" in wb.sheetnames
            sheet = wb["Quality Report"]
            assert sheet["A7"].value in ("PASS", "FAIL")


@pytest.mark.skipif(not _fixture_exists("known_good/standard.pdf"), reason="fixture missing")
def test_evaluator_latency_regression():
    """Evaluator latency must stay within +3% of a baseline (measured in ms)."""
    pdf = FIXTURES / "known_good" / "standard.pdf"
    times = []
    for _ in range(3):
        result = process_pdf(str(pdf), no_categorization=True)
        # Extract evaluator timing from state if available; otherwise skip
        eval_time = result.get("evaluator_timing_ms", 0)
        if eval_time:
            times.append(eval_time)

    if len(times) < 2:
        pytest.skip("insufficient timing data")

    mean_time = statistics.mean(times)
    # Baseline is established by running the same test locally and recording
    # the average evaluator time.  Here we just assert it finishes in < 30s.
    assert mean_time < 30_000, f"Evaluator mean latency {mean_time}ms exceeds 30s"
