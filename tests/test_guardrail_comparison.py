"""
Guardrail comparison test — run extraction with and without guardrails on the same PDF.

Usage:
    python tests/test_guardrail_comparison.py input/file.pdf [--statements all]

Output:
    - output_with_guardrails/    — results from run with guardrails enabled
    - output_without_guardrails/  — results from run with guardrails disabled
    - comparison_report.json     — side-by-side summary

This helps identify whether guardrails (hallucination checks, numeric prechecks,
equation validation, quality degradation tracking) are causing false positives that
prevent valid extractions from being saved.
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# Make project root importable
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import process_single_pdf
from config import Config
from utils.vlm_utils import StatementType


def _run_with_guardrails(pdf_path: str, statement_types: list, output_dir: Path) -> dict:
    """Run extraction with guardrails enabled (default)."""
    Config.DISABLE_GUARDRAILS = False
    return process_single_pdf(
        pdf_path,
        statement_types=statement_types,
        enable_categorization=False,  # Keep comparison focused on extraction
    )


def _run_without_guardrails(pdf_path: str, statement_types: list, output_dir: Path) -> dict:
    """Run extraction with all guardrails disabled."""
    Config.DISABLE_GUARDRAILS = True
    return process_single_pdf(
        pdf_path,
        statement_types=statement_types,
        enable_categorization=False,
    )


def _copy_outputs(state: dict, dest_dir: Path) -> list:
    """Copy output files from a run into a destination directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in state.get("output_files", []):
        src_path = Path(src)
        if src_path.exists():
            dst = dest_dir / src_path.name
            shutil.copy2(src_path, dst)
            copied.append(str(dst))
    return copied


def _summarize(state: dict) -> dict:
    """Extract key metrics from a run state."""
    eval_result = state.get("evaluation_result", {})
    extracted = state.get("extracted_data", {})

    summary = {
        "success": bool(state.get("output_files")),
        "error_message": state.get("error_message"),
        "retry_count": state.get("retry_count", 0),
        "guardrail_flags": state.get("guardrail_flags", []),
        "statements": {},
    }

    for st in StatementType:
        st_eval = eval_result.get(st, {})
        st_data = extracted.get(st)
        summary["statements"][st.value] = {
            "extracted": st_data is not None,
            "sections_count": len(st_data.get("sections", [])) if st_data else 0,
            "evaluation_passed": st_eval.get("passed", False) if st_eval else None,
            "scores": st_eval.get("scores", {}) if st_eval else {},
            "feedback": st_eval.get("feedback", "") if st_eval else "",
        }

    return summary


def run_comparison(pdf_path: str, statement_types: list = None):
    """
    Run extraction twice (with/without guardrails) and produce a comparison report.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        print(f"❌ PDF not found: {pdf_path}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = Path("guardrail_comparison") / f"{pdf_path.stem}_{timestamp}"

    out_with = base_dir / "output_with_guardrails"
    out_without = base_dir / "output_without_guardrails"
    report_path = base_dir / "comparison_report.json"

    resolved_types = statement_types or list(StatementType)

    print("=" * 60)
    print("GUARDRAIL COMPARISON TEST")
    print("=" * 60)
    print(f"📄 PDF: {pdf_path}")
    print(f"📊 Statements: {[st.value for st in resolved_types]}")
    print()

    # ------------------------------------------------------------------
    # Run 1: WITH guardrails
    # ------------------------------------------------------------------
    print("🏃 RUN 1: WITH guardrails enabled")
    print("-" * 40)
    t0 = time.time()
    state_with = _run_with_guardrails(str(pdf_path), resolved_types, out_with)
    t1 = time.time()
    copied_with = _copy_outputs(state_with, out_with)
    print(f"⏱️  Duration: {t1 - t0:.1f}s")
    print()

    # ------------------------------------------------------------------
    # Run 2: WITHOUT guardrails
    # ------------------------------------------------------------------
    print("🏃 RUN 2: WITH guardrails disabled")
    print("-" * 40)
    t0 = time.time()
    state_without = _run_without_guardrails(str(pdf_path), resolved_types, out_without)
    t1 = time.time()
    copied_without = _copy_outputs(state_without, out_without)
    print(f"⏱️  Duration: {t1 - t0:.1f}s")
    print()

    # ------------------------------------------------------------------
    # Build comparison report
    # ------------------------------------------------------------------
    summary_with = _summarize(state_with)
    summary_without = _summarize(state_without)

    report = {
        "pdf": str(pdf_path),
        "timestamp": timestamp,
        "with_guardrails": summary_with,
        "without_guardrails": summary_without,
        "differences": [],
    }

    # Detect meaningful differences
    if summary_with["success"] != summary_without["success"]:
        report["differences"].append(
            f"Success differs: with={summary_with['success']}, without={summary_without['success']}"
        )

    if summary_with["retry_count"] != summary_without["retry_count"]:
        report["differences"].append(
            f"Retry count differs: with={summary_with['retry_count']}, "
            f"without={summary_without['retry_count']}"
        )

    for st in StatementType:
        st_key = st.value
        sw = summary_with["statements"][st_key]
        swo = summary_without["statements"][st_key]

        if sw["evaluation_passed"] != swo["evaluation_passed"]:
            report["differences"].append(
                f"{st_key} evaluation_passed differs: "
                f"with={sw['evaluation_passed']}, without={swo['evaluation_passed']}"
            )

        if sw["sections_count"] != swo["sections_count"]:
            report["differences"].append(
                f"{st_key} sections_count differs: "
                f"with={sw['sections_count']}, without={swo['sections_count']}"
            )

    if not report["differences"]:
        report["differences"].append("No significant differences detected")

    report["output_files"] = {
        "with_guardrails": copied_with,
        "without_guardrails": copied_without,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    print("=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)
    print(f"With guardrails:     success={summary_with['success']}, retries={summary_with['retry_count']}")
    print(f"Without guardrails:  success={summary_without['success']}, retries={summary_without['retry_count']}")
    print()
    print("Differences:")
    for diff in report["differences"]:
        print(f"  - {diff}")
    print()
    print(f"📁 Report saved: {report_path}")
    print(f"📁 Outputs (with):    {out_with}")
    print(f"📁 Outputs (without):  {out_without}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Compare extraction with and without guardrails",
    )
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument(
        "--statements",
        type=str,
        default="all",
        help="Comma-separated statement types (default: all)",
    )
    args = parser.parse_args()

    statement_map = {
        "balance_sheet": StatementType.BALANCE_SHEET,
        "income_statement": StatementType.INCOME_STATEMENT,
        "cash_flow": StatementType.CASH_FLOW,
        "all": list(StatementType),
    }

    types = []
    for s in args.statements.lower().split(","):
        s = s.strip()
        if s in statement_map:
            val = statement_map[s]
            if isinstance(val, list):
                types.extend(val)
            else:
                types.append(val)

    if not types:
        types = list(StatementType)

    run_comparison(args.pdf, types)


if __name__ == "__main__":
    main()
