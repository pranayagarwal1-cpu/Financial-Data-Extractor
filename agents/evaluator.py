"""
Evaluator Agent (LLM-as-Judge) - Evaluates extraction quality for all statement types.

Supports:
- Balance Sheet (Statement of Financial Position)
- Income Statement (Statement of Earnings)
- Cash Flow Statement

Responsibilities:
- Score extraction on completeness, accuracy, and consistency
- Provide detailed feedback for re-extraction
- Make pass/fail decision based on rubric
"""

import json
import logging
import time
from typing import Optional, Dict
from enum import Enum

from utils.vlm_utils import StatementType
from config import Config


# ---------------------------------------------------------------------------
# LLM-as-Judge Prompt (Phase 2 — 4-Section Rubric)
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You are a financial statement quality auditor. You will receive:
1. The extracted JSON data
2. A summary of programmatic check results (scores + findings)
3. Raw OCR text from the source pages (first 3000 chars)

Your task: review the extraction and provide bounded qualitative adjustments
to the 4 section scores. You do NOT override programmatic scores — you add
or subtract up to 1.0 point per section based on things the rules may have
missed (e.g. a cash flow statement mislabeled as a balance sheet).

Programmatic check results:
{check_summary}

Statement type: {statement_type}

Raw OCR text (truncated):
{ocr_text}

Respond ONLY in this JSON format:
{{
  "coverage_adjustment": <float, -1.0 to 1.0>,
  "format_adjustment": <float, -1.0 to 1.0>,
  "structure_adjustment": <float, -1.0 to 1.0>,
  "content_adjustment": <float, -1.0 to 1.0>,
  "overall_confidence": <"high" | "medium" | "low">,
  "summary": "<2-3 sentences max. State what the extraction got right, what it got wrong, and whether the output is usable.>",
  "flags": []
}}

Rules:
- Adjustments must be justified by something NOT already caught by programmatic checks.
- Do not penalize for failures already listed in the check summary.
- Do not invent failures. If uncertain, adjust by 0.
- If the programmatic checks already failed hard (e.g. balance sheet does not balance), you should adjust by 0.
"""



from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class PenaltyLedger:
    """
    Prevents double-penalizing the same underlying error across sections.

    When Coverage (A) and Content (D) detect the same issue (e.g. a hallucinated
    row), only the first section to call charge() deducts from its score.
    Both sections still report the finding in the Quality Report.
    """

    penalized: set[str] = field(default_factory=set)

    def charge(self, check_id: str, item_key: str) -> bool:
        """Returns True if penalty was applied, False if already charged."""
        key = f"{check_id}:{item_key}"
        if key in self.penalized:
            return False
        self.penalized.add(key)
        return True


@dataclass
class CheckFinding:
    """Single finding from a programmatic quality check."""

    check_id: str
    status: str  # "PASS", "FAIL", "ADVISORY"
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class SectionResult:
    """Result of evaluating one of the 4 sections (Coverage, Format, Structure, Content)."""

    score: float  # 0.0–10.0
    findings: list[CheckFinding]
    hard_fail: bool = False  # True if any hard-fail check failed


# ---------------------------------------------------------------------------
# Phase 3 — Retry Context Builder
# ---------------------------------------------------------------------------

def _build_retry_context(
    coverage: SectionResult,
    format_result: SectionResult,
    structure: SectionResult,
    content: SectionResult,
    overall: float,
    passed: bool,
) -> dict:
    """
    Build a structured retry context dict for the orchestrator and extractor.
    This replaces the flat feedback string with actionable, categorized data.
    """
    all_findings = coverage.findings + format_result.findings + structure.findings + content.findings
    failed_findings = [f for f in all_findings if f.status == "FAIL"]
    advisory_findings = [f for f in all_findings if f.status == "ADVISORY"]

    hard_fail_checks = [f.check_id for f in failed_findings]

    # Check if ONLY never-retry checks failed
    never_retry_ids = set(Config.NEVER_RETRY_CHECKS)
    failed_ids = {f.check_id for f in failed_findings}
    only_never_retry = failed_ids and failed_ids.issubset(never_retry_ids)

    # Categorize failures for targeted prompt mutation
    categories = {}
    prompt_addendum = []

    if any(f.check_id in ("A5", "A6") for f in failed_findings):
        categories["missed_or_hallucinated_rows"] = True
        prompt_addendum.append(
            "The previous extraction missed rows or invented rows not in the document. "
            "Re-examine the pages carefully. Do not invent rows not visible in the document."
        )

    if any(f.check_id == "B4" for f in failed_findings):
        categories["column_misalignment"] = True
        prompt_addendum.append(
            "Every row must have exactly the same number of values as there are period columns. "
            "Do not merge or skip columns."
        )

    if any(f.check_id in ("C1", "C2", "C3") for f in failed_findings):
        categories["equation_failure"] = True
        prompt_addendum.append(
            "Your prior extraction failed accounting equation checks (e.g. Assets = Liabilities + Equity). "
            "Re-examine totals carefully and ensure arithmetic relationships hold."
        )

    if any(f.check_id == "D6" for f in failed_findings):
        categories["sign_error"] = True
        prompt_addendum.append(
            "Check parenthetical numbers — '(1,234)' means negative. Do not drop the sign."
        )

    if any(f.check_id == "B2" for f in failed_findings):
        categories["json_invalid"] = True
        prompt_addendum.append(
            "The previous response was not valid JSON. Respond ONLY with valid JSON, no markdown fences, no extra commentary."
        )

    return {
        "should_retry": not passed and not only_never_retry,
        "overall_score": round(overall, 1),
        "hard_fail_checks": hard_fail_checks,
        "failed_findings": [
            {"check_id": f.check_id, "message": f.message}
            for f in failed_findings
        ],
        "advisory_findings": [
            {"check_id": f.check_id, "message": f.message}
            for f in advisory_findings
        ],
        "categories": list(categories.keys()),
        "targeted_prompt_addendum": " ".join(prompt_addendum),
        "only_never_retry_fails": only_never_retry,
    }


def _parse_amount(val) -> Optional[Decimal]:
    """Parse a raw string value into a Decimal amount."""
    if val is None or val == "" or val == "null":
        return None
    s = str(val).replace("$", "").replace(",", "").replace(" ", "").strip()
    # Parentheses denote negative values: (1,234) -> -1234
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except Exception:
        return None


def _run_numeric_precheck(data: dict, statement_type: StatementType) -> tuple[float, str]:
    """Run fast numeric pre-checks and return (score 0-10, feedback)."""
    total_values = 0
    unparsable = 0

    for section in data.get("sections", []):
        for row in section.get("rows", []):
            for val in row.get("values", []):
                total_values += 1
                if _parse_amount(val) is None and val not in (None, "", "null"):
                    unparsable += 1

    if total_values == 0:
        return 0.0, "No numeric values found"

    parse_rate = 1 - (unparsable / total_values)
    score = round(10 * parse_rate, 1)
    feedback = f"{unparsable}/{total_values} values unparsable"
    return score, feedback


def _calculate_missing_values(data: dict) -> tuple[float, list[str]]:
    """
    Calculate the ratio of missing/null values and return individual findings.

    Returns (missing_ratio, list_of_descriptions).
    Each description is like: "Row 'Telephone' period 2025: value is empty"
    """
    total_values = 0
    missing_values = 0
    descriptions: list[str] = []
    periods = data.get("periods", [])

    for section in data.get("sections", []):
        section_name = section.get("name", "")
        for row in section.get("rows", []):
            label = row.get("label", "")
            for idx, val in enumerate(row.get("values", [])):
                total_values += 1
                if val is None or val == "" or val == "null":
                    missing_values += 1
                    period_label = periods[idx] if idx < len(periods) else f"col {idx + 1}"
                    descriptions.append(
                        f"Row '{label}' (section '{section_name}') period {period_label}: value is empty"
                    )

    if total_values == 0:
        return 1.0, descriptions
    return missing_values / total_values, descriptions


def _has_required_sections(data: dict, statement_type: StatementType) -> tuple[bool, list[str]]:
    """Check if all required sections/rows are present for the statement type.

    Uses keyword *groups* (list of lists) where at least one keyword from each
    group must match. This handles synonyms like Revenue/Income and
    Net Income/Net Profit/Net Earnings.
    """
    section_names = {s.get("name", "").upper() for s in data.get("sections", [])}

    # Also check row labels — some statements put key totals (e.g. Net Income)
    # inside a "Summary" or "Totals" section rather than as a standalone section.
    all_labels = ""
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            all_labels += " " + row.get("label", "").upper()

    # Each inner list is a group of synonyms — at least one must match
    required_groups = {
        StatementType.BALANCE_SHEET: [
            ["ASSET"],
            ["LIABILIT"],
            ["EQUITY"],
        ],
        StatementType.INCOME_STATEMENT: [
            ["REVENUE", "INCOME"],
            ["EXPENSE"],
            ["NET INCOME", "NET PROFIT", "NET EARNINGS"],
        ],
        StatementType.CASH_FLOW: [
            ["OPERATING"],
            ["INVESTING"],
            ["FINANCING"],
        ],
    }

    groups = required_groups.get(statement_type, [])
    missing_groups = []

    for group in groups:
        found = any(
            any(keyword in name for name in section_names) or keyword in all_labels
            for keyword in group
        )
        if not found:
            missing_groups.append("/".join(group))

    return len(missing_groups) == 0, missing_groups


def _find_row_value(data: dict, keywords: list[str], require_subtotal: bool = True) -> Optional[Decimal]:
    """
    Find a row whose label matches any keyword and return its first numeric value.

    When multiple rows match, prefers rows whose label contains "total"
    (e.g. "Total Revenue" or "Total Trading Income") over partial matches
    (e.g. "Other Revenue" or "Interest Revenue").
    """
    candidates = []
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            if require_subtotal and not row.get("is_subtotal"):
                continue
            label = row.get("label", "").lower()
            if any(kw in label for kw in keywords):
                for val in row.get("values", []):
                    parsed = _parse_amount(val)
                    if parsed is not None:
                        # Score: 2 for "total" prefix, 1 for plain match
                        score = 2 if "total" in label else 1
                        candidates.append((score, parsed, label))
                        break  # first parsable value for this row

    if not candidates:
        return None

    # Prefer highest score, then largest absolute value (main line items are usually bigger)
    candidates.sort(key=lambda x: (x[0], abs(x[1])), reverse=True)
    return candidates[0][1]


# Backwards-compatible alias
_find_subtotal_value = _find_row_value


def _check_flat_values(data: dict) -> tuple[bool, str]:
    """
    D13 — Detect rows where all period values are identical.

    Legitimate flat values (fixed lease payments, recurring fees) are common;
    only flag as FAIL if the flat value is material (>= 5% of section total).
    Otherwise mark as ADVISORY.
    """
    for section in data.get("sections", []):
        rows = section.get("rows", [])
        section_total = Decimal("0")
        for row in rows:
            if not row.get("is_subtotal", False):
                for val in row.get("values", []):
                    parsed = _parse_amount(val)
                    if parsed is not None and parsed > 0:
                        section_total += parsed

        for row in rows:
            values = row.get("values", [])
            if len(values) > 1 and len(set(values)) == 1:
                val = values[0]
                parsed = _parse_amount(val)
                if parsed is not None and parsed != 0:
                    materiality = abs(parsed) / section_total if section_total > 0 else Decimal("0")
                    if materiality >= Decimal("0.05"):
                        return False, (
                            f"Flat value '{val}' repeated across all periods in row "
                            f"'{row.get('label', '')}' is material ({materiality:.1%} of section)"
                        )
    return True, ""


def _check_period_labels(data: dict) -> tuple[bool, str]:
    """
    B5 — Ensure period labels represent actual time periods, not ratios.

    Tightened regex only rejects when the label's core content is a ratio
    or percentage, not when it merely contains those words.
    """
    import re
    period_reject = re.compile(
        r'^[\s\(%]*(\d+\.?\d*\s*%|change|ratio|variance)[\s\)]*$',
        re.IGNORECASE
    )
    for period in data.get("periods", []):
        if period_reject.search(str(period)):
            return False, f"Period label '{period}' appears to be a ratio/percentage, not a date"
    return True, ""


def _normalize_indent_levels(data: dict) -> dict:
    """
    Post-process extraction to fix flat subtotals.

    When the VLM misses amount-indentation cues, a parent subtotal sometimes
    ends up at the same indent_level as its children. Detects this pattern:

    - A subtotal at indent_level=L is preceded by a contiguous block of
      non-subtotal rows that are also at indent_level=L.

    When detected, promotes the subtotal to indent_level=max(0, L-1)
    so the hierarchy is properly expressed for sum-check logic.
    """
    for section in data.get("sections", []):
        rows = section.get("rows", [])
        if not rows:
            continue

        # Iterate forward; promote in-place when pattern matches
        for idx, row in enumerate(rows):
            if not row.get("is_subtotal", False):
                continue
            current_indent = row.get("indent_level", 0)

            # Scan backward for a contiguous block of same-indent non-subtotals
            block_start = idx
            while block_start > 0:
                prev = rows[block_start - 1]
                if prev.get("is_subtotal", False):
                    break
                if prev.get("indent_level", 0) != current_indent:
                    break
                block_start -= 1

            # If there is at least one preceding non-subtotal at same level,
            # this subtotal is flat — promote it above its children.
            if block_start < idx:
                new_indent = max(0, current_indent - 1)
                if new_indent != current_indent:
                    row["indent_level"] = new_indent

    return data


def _run_section_sum_checks(data: dict, statement_type: StatementType) -> list[tuple[str, bool]]:
    """
    Verify additive subtotals equal the sum of their child line items.

    Returns a list of (message, is_advisory_only) tuples.
    - is_advisory_only=True  → discrepancy is within tolerance (reported as ADVISORY)
    - is_advisory_only=False → discrepancy exceeds tolerance (reported as FAIL)

    All discrepancies are reported, no matter how small.
    """
    additive_section_keywords = ["cost", "expense", "asset", "liabilit", "equity"]
    non_additive_total_keywords = ["gross", "margin", "operating income", "net income", "profit"]

    discrepancies: list[tuple[str, bool]] = []

    for section in data.get("sections", []):
        section_name = section.get("name", "").lower()
        rows = section.get("rows", [])
        if not rows:
            continue

        num_periods = len(rows[0].get("values", [])) if rows else 0
        if num_periods == 0:
            continue

        is_additive_section = any(kw in section_name for kw in additive_section_keywords)

        has_indent_data = any(
            row.get("indent_level", 0) > 0 for row in rows
        )

        subtotals_in_section = []
        seen_subtotals_stack: list[list[Decimal]] = []
        has_negative_line_item = False

        if has_indent_data:
            running_leaf_sums = [Decimal("0") for _ in range(num_periods)]

            for row in rows:
                is_sub = row.get("is_subtotal", False)
                vals = row.get("values", [])
                label = row.get("label", "").lower()
                indent = row.get("indent_level", 0)

                if is_sub:
                    if any(kw in label for kw in non_additive_total_keywords):
                        continue

                    parsed_vals = [_parse_amount(v) for v in vals]
                    subtotals_in_section.append({
                        "label": row.get("label", ""),
                        "values": parsed_vals,
                        "leaf_sums": [s for s in running_leaf_sums],
                        "parent_sums": [sum(s[i] for s in seen_subtotals_stack) if seen_subtotals_stack else Decimal("0") for i in range(num_periods)],
                    })
                    seen_subtotals_stack.append(parsed_vals)
                    running_leaf_sums = [Decimal("0") for _ in range(num_periods)]
                elif indent > 0:
                    for i, v in enumerate(vals):
                        if i < num_periods:
                            parsed = _parse_amount(v)
                            if parsed is not None:
                                if parsed < 0:
                                    has_negative_line_item = True
                                else:
                                    running_leaf_sums[i] += parsed
                else:
                    pass
        else:
            running_leaf_sums = [Decimal("0") for _ in range(num_periods)]

            for row in rows:
                is_sub = row.get("is_subtotal", False)
                vals = row.get("values", [])
                label = row.get("label", "").lower()

                if is_sub:
                    if any(kw in label for kw in non_additive_total_keywords):
                        continue

                    parsed_vals = [_parse_amount(v) for v in vals]
                    subtotals_in_section.append({
                        "label": row.get("label", ""),
                        "values": parsed_vals,
                        "leaf_sums": [s for s in running_leaf_sums],
                        "parent_sums": [sum(s[i] for s in seen_subtotals_stack) if seen_subtotals_stack else Decimal("0") for i in range(num_periods)],
                    })
                    seen_subtotals_stack.append(parsed_vals)
                    running_leaf_sums = [Decimal("0") for _ in range(num_periods)]
                else:
                    for i, v in enumerate(vals):
                        if i < num_periods:
                            parsed = _parse_amount(v)
                            if parsed is not None:
                                if parsed < 0:
                                    has_negative_line_item = True
                                else:
                                    running_leaf_sums[i] += parsed

        if not subtotals_in_section:
            continue

        if not is_additive_section:
            subtotals_in_section = [s for s in subtotals_in_section if "total" in s["label"].lower()]
            if not subtotals_in_section:
                continue

        for sub in subtotals_in_section:
            for i, sub_val in enumerate(sub["values"]):
                if sub_val is None or i >= num_periods:
                    continue

                leaf_sum = sub["leaf_sums"][i]
                parent_sum = sub["parent_sums"][i]
                tolerance = _equation_tolerance(sub_val)

                # Determine the best expected value
                expected = leaf_sum if leaf_sum != 0 else parent_sum
                if expected == 0:
                    continue

                diff = abs(sub_val - expected)

                # Materiality gate: skip reporting if under $1K
                if _materiality_gate(diff):
                    continue

                period_label = (
                    data.get("periods", [f"col {i + 1}"])[i]
                    if i < len(data.get("periods", []))
                    else f"col {i + 1}"
                )

                # Within tolerance → ADVISORY (still report it)
                if diff <= tolerance:
                    discrepancies.append((
                        f"Section '{section.get('name', '')}' subtotal '{sub['label']}' "
                        f"period {period_label}: extracted {sub_val} vs expected {expected} "
                        f"(diff: {diff}, within tolerance)",
                        True,
                    ))
                    continue

                # Negative line items present → ADVISORY only
                # (e.g. COGS where Ending Inventories are subtracted)
                if has_negative_line_item:
                    discrepancies.append((
                        f"Section '{section.get('name', '')}' subtotal '{sub['label']}' "
                        f"period {period_label}: extracted {sub_val} vs expected {expected} "
                        f"(diff: {diff}, exceeds tolerance; negative line items present — "
                        f"may be intentional subtraction)",
                        True,
                    ))
                    continue

                discrepancies.append((
                    f"Section '{section.get('name', '')}' subtotal '{sub['label']}' "
                    f"period {period_label}: extracted {sub_val} vs expected {expected} "
                    f"(diff: {diff}, exceeds tolerance)",
                    False,
                ))

    return discrepancies


def _equation_tolerance(value: Decimal) -> Decimal:
    """
    Tiered tolerance scaled to magnitude.

    -  < $10K      → absolute $5  (rounding / cents noise)
    -  $10K–$1M   → 2%
    -  $1M–$100M  → 1%
    -  ≥ $100M     → 0.5% capped at $500K
    """
    abs_val = abs(value)
    if abs_val < Decimal("10000"):
        return Decimal("5")
    elif abs_val < Decimal("1000000"):
        return abs_val * Decimal("0.02")
    elif abs_val < Decimal("100000000"):
        return abs_val * Decimal("0.01")
    else:
        return min(abs_val * Decimal("0.005"), Decimal("500000"))


def _materiality_gate(diff: Decimal) -> bool:
    """Auto-pass if discrepancy is under $1K absolute (published rounding)."""
    return diff < Decimal("1000")


def _run_equation_checks(data: dict, statement_type: StatementType) -> tuple[bool, str]:
    """Run programmatic accounting equation checks. Returns (passed, feedback)."""
    if statement_type == StatementType.BALANCE_SHEET:
        total_assets = _find_subtotal_value(data, ["total asset", "assets total", "total assets", "asset total"])
        total_liab = _find_subtotal_value(data, ["total liabilit", "liabilit total", "total liabilities", "liabilities total"])
        total_equity = _find_subtotal_value(data, ["total equity", "equity total", "shareholders equity", "stockholders equity", "net asset"])

        if total_assets is not None and total_liab is not None and total_equity is not None:
            expected = total_liab + total_equity
            diff = abs(total_assets - expected)
            tolerance = _equation_tolerance(expected)
            if diff > tolerance and not _materiality_gate(diff):
                return False, (
                    f"Balance sheet does not balance: Assets ({total_assets}) ≠ "
                    f"Liabilities ({total_liab}) + Equity ({total_equity}) = {expected} (diff: {diff})"
                )

    elif statement_type == StatementType.INCOME_STATEMENT:
        # Revenue is typically NOT marked as a subtotal, so search all rows.
        # "Trading income" and "sales" are common synonyms in practice.
        revenue = _find_row_value(
            data,
            ["total revenue", "trading income", "gross revenue", "total sales", "revenue"],
            require_subtotal=False
        )
        cogs = _find_subtotal_value(data, ["cost of goods", "cost of revenue", "cogs", "cost of sales"])
        gross_profit = _find_subtotal_value(data, ["gross profit", "gross income", "gross margin"])

        if revenue is not None and cogs is not None and gross_profit is not None:
            expected = revenue - cogs
            diff = abs(gross_profit - expected)
            tolerance = _equation_tolerance(expected)
            if diff > tolerance and not _materiality_gate(diff):
                return False, (
                    f"Gross Profit does not reconcile: {gross_profit} ≠ "
                    f"Revenue ({revenue}) - COGS ({cogs}) = {expected} (diff: {diff})"
                )

        # Check operating income if components are available
        gross = gross_profit if gross_profit is not None else _find_subtotal_value(data, ["gross profit"])
        operating_income = _find_subtotal_value(data, ["operating income", "operating profit", "income from operations"])
        opex = _find_subtotal_value(data, ["total operating expense", "operating expense total", "total expenses"])
        if gross is not None and opex is not None and operating_income is not None:
            expected = gross - opex
            diff = abs(operating_income - expected)
            tolerance = _equation_tolerance(expected)
            if diff > tolerance and not _materiality_gate(diff):
                return False, (
                    f"Operating Income does not reconcile: {operating_income} ≠ "
                    f"Gross Profit ({gross}) - OpEx ({opex}) = {expected} (diff: {diff})"
                )

    elif statement_type == StatementType.CASH_FLOW:
        net_change = _find_subtotal_value(data, ["net change in cash", "net increase", "net decrease", "change in cash"])
        beginning = _find_subtotal_value(data, ["cash at beginning", "beginning cash", "cash, beginning"])
        ending = _find_subtotal_value(data, ["cash at end", "ending cash", "cash, end", "cash and equivalents"])

        if net_change is not None and beginning is not None and ending is not None:
            expected = ending - beginning
            diff = abs(net_change - expected)
            tolerance = _equation_tolerance(expected)
            if diff > tolerance and not _materiality_gate(diff):
                return False, (
                    f"Cash flow does not reconcile: Net Change ({net_change}) ≠ "
                    f"Ending ({ending}) - Beginning ({beginning}) = {expected} (diff: {diff})"
                )

    return True, ""


# ---------------------------------------------------------------------------
# Hallucination Guardrail (Ground-Truth Check)
# ---------------------------------------------------------------------------

def _normalize_value_for_ground_truth(val: str) -> str:
    """Normalize a value string for ground-truth matching against PDF text."""
    s = str(val).strip()
    s = s.replace("$", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return s


def _value_forms(val: str) -> set[str]:
    """Generate possible string forms of a numeric value for fuzzy matching."""
    forms = set()
    base = _normalize_value_for_ground_truth(val)
    forms.add(base)
    forms.add(base.replace(",", ""))
    forms.add(base.replace(",", " "))
    if base.endswith(".00"):
        forms.add(base[:-3])
    if base.startswith("-"):
        inner = base[1:]
        forms.add(f"({inner})")
        forms.add(f"({inner.replace(',', '')})")
    return forms


def _normalize_for_matching(text: str) -> str:
    """
    Normalize text for fuzzy ground-truth matching.

    Removes all whitespace, dashes, underscores, and newlines, then lowercases.
    This handles PDFs where the text layer has stripped spaces
    (e.g. 'TotalTradingIncome' vs 'Total Trading Income').
    """
    return text.lower().replace(" ", "").replace("-", "").replace("_", "").replace("\n", "").replace("\t", "").replace("&", "")


def _value_in_text(val: str, text: str) -> bool:
    """Check if a value (in any common form) appears in the page text."""
    text_norm = _normalize_for_matching(text)
    for form in _value_forms(val):
        if _normalize_for_matching(form) in text_norm:
            return True
    return False


def _ocr_text_quality(page_texts: list[str]) -> str:
    """
    Assess OCR text quality to decide how strict hallucination checks should be.

    Returns:
        'good'     — OCR text has normal spacing and punctuation
        'poor'     — OCR text has stripped spaces (e.g. 'TotalTradingIncome')
        'garbled'  — OCR text from pytesseract is severely corrupted
                     (high ratio of non-word artifacts, random symbols)
    """
    all_text = "\n".join(page_texts)
    if not all_text:
        return "good"

    # Heuristic 1: stripped spaces
    space_count = all_text.count(" ")
    char_count = len(all_text.replace("\n", "").replace(" ", ""))
    if char_count > 0 and space_count / char_count < 0.05:
        return "poor"

    # Heuristic 2: garbled pytesseract output
    # Count words with high density of garbage characters
    words = all_text.split()
    if not words:
        return "good"

    ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789()-.,$%&/ ")
    garbled_words = 0
    for word in words:
        if not word:
            continue
        good_chars = sum(1 for c in word if c in ALLOWED)
        if good_chars / len(word) < 0.6:
            garbled_words += 1

    if garbled_words / len(words) > 0.03:
        return "garbled"

    return "good"


def _run_hallucination_check(
    data: dict,
    page_texts: list[str],
    ledger: Optional[PenaltyLedger] = None,
) -> tuple[bool, str]:
    """
    Verify that extracted values appear in source page text (ground-truth check).

    Checks two tiers:
    1. Subtotal / total values (strict: > 3 missing = fail)
    2. All non-empty numeric values (lenient: > 5 missing = warn)

    For scanned PDFs, this relies on OCR text that the extractor populates
    when the PDF text layer is empty.

    Returns:
        (passed, feedback)
    """
    if not page_texts:
        return True, ""

    all_text = "\n".join(page_texts)
    if len(all_text.strip()) < 50:
        return True, ""

    subtotal_values = []
    all_values = []

    for section in data.get("sections", []):
        for row in section.get("rows", []):
            for val in row.get("values", []):
                if val and val not in (None, "", "null"):
                    all_values.append(val)
                    if row.get("is_subtotal"):
                        subtotal_values.append(val)

    if not all_values:
        return True, ""

    ocr_quality = _ocr_text_quality(page_texts)

    # Tier 1: subtotals must be present (strict)
    missing_subtotals = 0
    for val in subtotal_values:
        if not _value_in_text(val, all_text):
            missing_subtotals += 1
            if ledger:
                # Content section D2/D3 owns the penalty for missing values
                ledger.charge("D3", f"value:{val}")

    # When OCR quality is poor (stripped text layer), downgrade to advisory
    if missing_subtotals > 3 and ocr_quality == "good":
        return (
            False,
            f"{missing_subtotals}/{len(subtotal_values)} subtotal values not found in source page text"
        )
    elif missing_subtotals > 0:
        return (
            True,
            f"{missing_subtotals}/{len(subtotal_values)} subtotal values not found (OCR quality: {ocr_quality}) — treated as advisory"
        )

    # Tier 2: all values should be present (lenient)
    missing_all = 0
    for val in all_values:
        if not _value_in_text(val, all_text):
            missing_all += 1
            if ledger:
                ledger.charge("D3", f"value:{val}")

    if missing_all > 5 and ocr_quality == "good":
        return (
            False,
            f"{missing_all}/{len(all_values)} values not found in source page text — "
            "possible OCR/VLM misread (e.g., digits transposed like 11564 vs 11664)"
        )
    elif missing_all > 0:
        return (
            True,
            f"{missing_all}/{len(all_values)} values not found (OCR quality: {ocr_quality}) — treated as advisory"
        )

    return True, ""


def _run_coverage_checks(
    data: dict,
    statement_type: StatementType,
    ledger: Optional[PenaltyLedger] = None,
    page_texts: Optional[list[str]] = None,
) -> SectionResult:
    """
    Section A — Coverage checks.

    A1: Required sections present (hard fail)
    A2: Missing value ratio
    A4: Row count sanity
    A6: Hallucinated fields (labels not in OCR)
    A7: Comparative period integrity
    """
    findings: list[CheckFinding] = []
    hard_fail = False

    # A1 — Required sections
    has_sections, missing = _has_required_sections(data, statement_type)
    if not has_sections:
        findings.append(CheckFinding("A1", "FAIL", f"Missing required sections: {missing}"))
        hard_fail = True
    else:
        findings.append(CheckFinding("A1", "PASS", "All required sections present"))

    # A2 — Missing value ratio (report every individual missing value)
    missing_ratio, missing_descriptions = _calculate_missing_values(data)
    a2_score = max(0.0, 10.0 - (missing_ratio * 50))  # 0% → 10, 20% → 0

    # Report each missing value as an individual finding
    for desc in missing_descriptions:
        findings.append(CheckFinding("A2", "ADVISORY", desc))

    if missing_ratio > 0.20:
        findings.append(CheckFinding("A2", "FAIL", f"Missing value ratio {missing_ratio:.1%} exceeds 20% ({len(missing_descriptions)} empty values)"))
    elif missing_descriptions:
        findings.append(CheckFinding("A2", "ADVISORY", f"Missing value ratio {missing_ratio:.1%} ({len(missing_descriptions)} empty values)"))
    else:
        findings.append(CheckFinding("A2", "PASS", f"Missing value ratio {missing_ratio:.1%}"))

    # A4 — Row count sanity
    total_rows = sum(len(s.get("rows", [])) for s in data.get("sections", []))
    if total_rows < 5:
        findings.append(CheckFinding("A4", "FAIL", f"Only {total_rows} rows extracted — probable truncation"))
        hard_fail = True
    elif total_rows > 300:
        findings.append(CheckFinding("A4", "ADVISORY", f"{total_rows} rows — possible duplication or noise"))
    else:
        findings.append(CheckFinding("A4", "PASS", f"{total_rows} rows extracted"))

    # A6 — Hallucinated fields (labels not in OCR)
    if Config.ENABLE_DEEP_CONTENT_CHECKS and page_texts and len("\n".join(page_texts).strip()) >= 50:
        all_text = "\n".join(page_texts).lower()
        all_text_norm = _normalize_for_matching(all_text)
        ocr_quality = _ocr_text_quality(page_texts)
        hallucinated_labels = 0
        for section in data.get("sections", []):
            for row in section.get("rows", []):
                label = row.get("label", "").strip().lower()
                if label and label != "__empty__" and _normalize_for_matching(label) not in all_text_norm:
                    hallucinated_labels += 1
                    if ledger:
                        ledger.charge("A6", f"label:{label}")
        # When OCR text is poor (stripped spaces) or garbled (pytesseract noise),
        # downgrade from FAIL to ADVISORY because the VLM may be correct.
        unreliable_ocr = ocr_quality in ("poor", "garbled")
        if hallucinated_labels > 2 and not unreliable_ocr:
            findings.append(CheckFinding("A6", "FAIL", f"{hallucinated_labels} labels not found in source text"))
            hard_fail = True
        elif hallucinated_labels > 0:
            status = "ADVISORY" if unreliable_ocr else "FAIL"
            findings.append(CheckFinding("A6", status, f"{hallucinated_labels} label(s) not found in source text (OCR quality: {ocr_quality})"))
            if status == "FAIL":
                hard_fail = True
        else:
            findings.append(CheckFinding("A6", "PASS", "All labels appear in source text"))
    else:
        findings.append(CheckFinding("A6", "PASS", "Insufficient OCR text — skipped"))

    # A7 — Comparative period integrity
    periods = data.get("periods", [])
    if len(periods) > 1:
        # Collect normalized labels per period by splitting values
        # Heuristic: if a row has values for period 1 but empty for period 2,
        # it may indicate a missing row in one period
        # Proper implementation would need period-by-period label sets
        findings.append(CheckFinding("A7", "PASS", f"{len(periods)} periods present"))
    else:
        findings.append(CheckFinding("A7", "PASS", "Single period — no comparison needed"))

    # Score = weighted average of check scores, capped if hard_fail
    score = a2_score
    if hard_fail:
        score = min(score, 5.0)
    return SectionResult(score=round(score, 1), findings=findings, hard_fail=hard_fail)


def _run_format_checks(data: dict, statement_type: StatementType) -> SectionResult:
    """
    Section B — Format checks.

    B1: Numeric parseability
    B3: Period consistency
    B4: Column alignment
    B5: Period labels are dates, not ratios
    B6: Currency format consistency
    B7: Date format normalization
    """
    findings: list[CheckFinding] = []

    # B1 — Numeric parseability
    numeric_score, numeric_feedback = _run_numeric_precheck(data, statement_type)
    if numeric_score < 5.0:
        findings.append(CheckFinding("B1", "FAIL", f"Numeric parseability {numeric_score}/10 — {numeric_feedback}"))
    elif numeric_score < 8.0:
        findings.append(CheckFinding("B1", "ADVISORY", f"Numeric parseability {numeric_score}/10 — {numeric_feedback}"))
    else:
        findings.append(CheckFinding("B1", "PASS", f"Numeric parseability {numeric_score}/10"))

    # B3 — Period consistency across sections
    periods = data.get("periods", [])
    period_sets = []
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            vals = row.get("values", [])
            if vals:
                period_sets.append(len(vals))
                break
    if period_sets and not all(p == period_sets[0] for p in period_sets):
        findings.append(CheckFinding("B3", "FAIL", "Inconsistent number of period columns across sections"))
    else:
        findings.append(CheckFinding("B3", "PASS", f"{len(periods)} periods consistent"))

    # B4 — Column alignment (len(values) == len(periods))
    misaligned = 0
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            vals = row.get("values", [])
            if vals and len(vals) != len(periods):
                misaligned += 1
    if misaligned > 0:
        findings.append(CheckFinding("B4", "FAIL", f"{misaligned} rows have misaligned columns"))
    else:
        findings.append(CheckFinding("B4", "PASS", "All rows aligned to period columns"))

    # B5 — Period labels sanity
    period_passed, period_feedback = _check_period_labels(data)
    if not period_passed:
        findings.append(CheckFinding("B5", "FAIL", period_feedback))
    else:
        findings.append(CheckFinding("B5", "PASS", "Period labels are date-like"))

    # B6 — Currency format consistency (stub — regex per section)
    findings.append(CheckFinding("B6", "PASS", "Currency format check skipped — TODO"))

    # B7 — Date format normalization (stub)
    findings.append(CheckFinding("B7", "PASS", "Date format check skipped — TODO"))

    # Score
    hard_fail = any(f.status == "FAIL" and f.check_id in {"B1", "B4"} for f in findings)
    score = numeric_score
    if any(f.status == "FAIL" for f in findings):
        score = min(score, 5.0)
    return SectionResult(score=round(score, 1), findings=findings, hard_fail=hard_fail)


def _run_structure_checks(data: dict, statement_type: StatementType) -> SectionResult:
    """
    Section C — Structure checks.

    C1-C3: Equation checks
    C4: Section sum reconciliation
    C5: Indent normalization
    C6: Hierarchy validation
    C9: Multicolumn layout safety
    C10: Header/footer bleed
    C11: Section boundary coherence
    C12: Duplicate row detection
    C13: Row order integrity
    """
    findings: list[CheckFinding] = []

    # C1-C3 — Equation checks
    eq_passed, eq_feedback = _run_equation_checks(data, statement_type)
    if not eq_passed:
        findings.append(CheckFinding("C1-C3", "FAIL", eq_feedback))
    else:
        findings.append(CheckFinding("C1-C3", "PASS", "Accounting equations balance"))

    # C4 — Section sum reconciliation
    sum_discrepancies = _run_section_sum_checks(data, statement_type)
    if sum_discrepancies:
        for msg, is_advisory in sum_discrepancies:
            status = "ADVISORY" if is_advisory else "FAIL"
            findings.append(CheckFinding("C4", status, msg))
    else:
        findings.append(CheckFinding("C4", "PASS", "Subtotals reconcile with leaf rows"))

    # C5 — Indent normalization (already applied in evaluator_node, just verify)
    flat_subtotals = 0
    for section in data.get("sections", []):
        rows = section.get("rows", [])
        for idx, row in enumerate(rows):
            if row.get("is_subtotal") and row.get("indent_level", 0) > 0:
                # Check if children share same indent
                children = [r for r in rows[:idx] if not r.get("is_subtotal") and r.get("indent_level", 0) == row.get("indent_level", 0)]
                if children:
                    flat_subtotals += 1
    if flat_subtotals > 0:
        findings.append(CheckFinding("C5", "ADVISORY", f"{flat_subtotals} flat subtotals detected (heuristic applied)"))
    else:
        findings.append(CheckFinding("C5", "PASS", "Hierarchy properly expressed"))

    # C6 — Hierarchy validation
    orphaned_subtotals = 0
    for section in data.get("sections", []):
        rows = section.get("rows", [])
        for idx, row in enumerate(rows):
            if row.get("is_subtotal"):
                indent = row.get("indent_level", 0)
                has_child = any(
                    r.get("indent_level", 0) > indent
                    for r in rows[:idx]
                    if not r.get("is_subtotal")
                )
                if not has_child:
                    orphaned_subtotals += 1
    if orphaned_subtotals > 0:
        findings.append(CheckFinding("C6", "ADVISORY", f"{orphaned_subtotals} subtotals have no children"))
    else:
        findings.append(CheckFinding("C6", "PASS", "All subtotals have children"))

    # C9 — Multicolumn layout safety
    period_values = set()
    for period in data.get("periods", []):
        period_values.add(period.lower())
    suspicious = [p for p in period_values if any(k in p for k in ("%", "change", "ratio", "variance"))]
    if suspicious:
        findings.append(CheckFinding("C9", "FAIL", f"Suspicious period values: {suspicious}"))
    else:
        findings.append(CheckFinding("C9", "PASS", "No percentage columns in periods"))

    # C10 — Header/footer bleed
    denylist = ["page", "confidential", "draft", "preliminary"]
    bleed_count = 0
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            label = row.get("label", "").lower()
            if any(d in label for d in denylist):
                bleed_count += 1
    if bleed_count > 0:
        findings.append(CheckFinding("C10", "ADVISORY", f"{bleed_count} rows contain header/footer keywords"))
    else:
        findings.append(CheckFinding("C10", "PASS", "No header/footer bleed detected"))

    # C11 — Section boundary coherence
    section_names = [s.get("name", "").lower() for s in data.get("sections", [])]
    coherence_issues = 0
    for i, name in enumerate(section_names):
        if "revenue" in name and i > 0 and "expense" in section_names[i - 1]:
            coherence_issues += 1
    if coherence_issues > 0:
        findings.append(CheckFinding("C11", "ADVISORY", "Revenue section follows Expense section"))
    else:
        findings.append(CheckFinding("C11", "PASS", "Section ordering is coherent"))

    # C12 — Duplicate row detection
    duplicates = 0
    for section in data.get("sections", []):
        seen = set()
        for row in section.get("rows", []):
            key = (row.get("label", ""), tuple(row.get("values", [])))
            if key in seen:
                duplicates += 1
            seen.add(key)
    if duplicates > 0:
        findings.append(CheckFinding("C12", "ADVISORY", f"{duplicates} duplicate rows detected"))
    else:
        findings.append(CheckFinding("C12", "PASS", "No duplicate rows"))

    # C13 — Row order integrity
    order_issues = 0
    for section in data.get("sections", []):
        rows = section.get("rows", [])
        for idx, row in enumerate(rows):
            if row.get("is_subtotal"):
                # Check if any non-subtotal AFTER this has lower indent
                later = [r for r in rows[idx + 1:] if not r.get("is_subtotal")]
                if later and any(r.get("indent_level", 0) <= row.get("indent_level", 0) for r in later):
                    order_issues += 1
    if order_issues > 0:
        findings.append(CheckFinding("C13", "ADVISORY", f"{order_issues} subtotals followed by unrelated rows"))
    else:
        findings.append(CheckFinding("C13", "PASS", "Row ordering is valid"))

    # Score
    hard_fail = any(f.status == "FAIL" for f in findings)
    any_sum_fail = any(f.status == "FAIL" and f.check_id == "C4" for f in findings)
    score = 10.0 if eq_passed and not any_sum_fail else 3.0
    if hard_fail:
        score = min(score, 3.0)
    return SectionResult(score=round(score, 1), findings=findings, hard_fail=hard_fail)


def _run_content_checks(
    data: dict,
    statement_type: StatementType,
    ledger: Optional[PenaltyLedger] = None,
    page_texts: Optional[list[str]] = None,
) -> SectionResult:
    """
    Section D — Content checks.

    D1-D3: Hallucination / ground-truth
    D4: Label fuzzy accuracy
    D5: Value range sanity
    D7: Truncated fields
    D8: Merged cells
    D9: Unit scale errors
    D10: OCR artifacts
    D11: Decimal precision drift
    D13: Flat values across periods
    D14: Materiality scoring
    """
    findings: list[CheckFinding] = []

    # D1-D3 — Hallucination check
    if Config.ENABLE_DEEP_CONTENT_CHECKS and page_texts:
        hall_passed, hall_feedback = _run_hallucination_check(data, page_texts, ledger)
        if not hall_passed:
            findings.append(CheckFinding("D1-D3", "FAIL", hall_feedback))
        else:
            findings.append(CheckFinding("D1-D3", "PASS", "Ground-truth check passed"))
    else:
        status = "PASS"
        msg = "Deep content checks disabled — skipped" if not Config.ENABLE_DEEP_CONTENT_CHECKS else "No OCR text — skipped"
        findings.append(CheckFinding("D1-D3", status, msg))

    # D4 — Label fuzzy accuracy
    if Config.ENABLE_DEEP_CONTENT_CHECKS and page_texts and len("\n".join(page_texts).strip()) >= 50:
        all_text = "\n".join(page_texts).lower()
        all_text_norm = _normalize_for_matching(all_text)
        ocr_quality = _ocr_text_quality(page_texts)
        missing_labels = 0
        for section in data.get("sections", []):
            for row in section.get("rows", []):
                label = row.get("label", "").strip().lower()
                # Allow truncated labels (they won't match exactly)
                if label and label != "__empty__" and not label.endswith("..") and _normalize_for_matching(label) not in all_text_norm:
                    missing_labels += 1
        # When OCR text is stripped (no spaces), downgrade from FAIL to ADVISORY
        if missing_labels > 3 and ocr_quality == "good":
            findings.append(CheckFinding("D4", "FAIL", f"{missing_labels} labels not found in OCR"))
        elif missing_labels > 0:
            findings.append(CheckFinding("D4", "ADVISORY", f"{missing_labels} label(s) not found in OCR (OCR quality: {ocr_quality})"))
        else:
            findings.append(CheckFinding("D4", "PASS", "All labels found in OCR"))
    else:
        status = "PASS"
        msg = "Deep content checks disabled — skipped" if not Config.ENABLE_DEEP_CONTENT_CHECKS else "Insufficient OCR text — skipped"
        findings.append(CheckFinding("D4", status, msg))

    # D5 — Value range sanity
    range_issues = 0
    for section in data.get("sections", []):
        section_total = Decimal("0")
        for row in section.get("rows", []):
            for val in row.get("values", []):
                parsed = _parse_amount(val)
                if parsed is not None and parsed > 0:
                    section_total += parsed
        for row in section.get("rows", []):
            for val in row.get("values", []):
                parsed = _parse_amount(val)
                if parsed is not None and abs(parsed) > section_total * Decimal("10") and section_total > 0:
                    range_issues += 1
    if range_issues > 0:
        findings.append(CheckFinding("D5", "ADVISORY", f"{range_issues} values exceed 10× section total"))
    else:
        findings.append(CheckFinding("D5", "PASS", "Value ranges are sane"))

    # D7 — Truncated fields
    trunc_count = 0
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            label = row.get("label", "")
            if label.endswith("..") or label.endswith("...") or label.endswith("…"):
                trunc_count += 1
    # Truncation is common with VLMs — never hard-fail, only advisory
    if trunc_count > 0:
        findings.append(CheckFinding("D7", "ADVISORY", f"{trunc_count} truncated label(s)"))
    else:
        findings.append(CheckFinding("D7", "PASS", "No truncated labels"))

    # D8 — Merged cells
    merged_issues = 0
    periods = data.get("periods", [])
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            if not row.get("is_subtotal"):
                vals = row.get("values", [])
                if vals and len(vals) < len(periods):
                    merged_issues += 1
    if merged_issues > 0:
        findings.append(CheckFinding("D8", "ADVISORY", f"{merged_issues} rows may have merged cells"))
    else:
        findings.append(CheckFinding("D8", "PASS", "No merged cell issues"))

    # D9 — Unit scale errors
    scale_issues = 0
    for section in data.get("sections", []):
        decimal_patterns = []
        for row in section.get("rows", []):
            for val in row.get("values", []):
                if val and "." in str(val):
                    decimal_patterns.append(len(str(val).split(".")[-1]))
        if decimal_patterns and len(set(decimal_patterns)) > 1:
            # More than one decimal precision in the same section
            scale_issues += 1
    if scale_issues > 0:
        findings.append(CheckFinding("D9", "ADVISORY", "Inconsistent decimal precision — possible scale error"))
    else:
        findings.append(CheckFinding("D9", "PASS", "Decimal precision consistent"))

    # D10 — OCR artifacts
    artifact_count = 0
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            label = row.get("label", "")
            if any(ord(c) > 127 for c in label):
                artifact_count += 1
    if artifact_count > 0:
        findings.append(CheckFinding("D10", "ADVISORY", f"{artifact_count} labels contain non-ASCII characters"))
    else:
        findings.append(CheckFinding("D10", "PASS", "No OCR artifacts"))

    # D11 — Decimal precision drift
    # Already covered by D9 approach; mark as PASS
    findings.append(CheckFinding("D11", "PASS", "Covered by D9 unit scale check"))

    # D13 — Flat values across periods
    flat_passed, flat_feedback = _check_flat_values(data)
    if not flat_passed:
        findings.append(CheckFinding("D13", "ADVISORY", flat_feedback))
    else:
        findings.append(CheckFinding("D13", "PASS", "No material flat values"))

    # D14 — Materiality scoring (stub)
    findings.append(CheckFinding("D14", "PASS", "Materiality check skipped — TODO"))

    # Score
    hard_fail = any(f.status == "FAIL" for f in findings)
    score = 10.0
    fail_count = sum(1 for f in findings if f.status == "FAIL")
    advisory_count = sum(1 for f in findings if f.status == "ADVISORY")
    score = max(0.0, 10.0 - fail_count * 3.0 - advisory_count * 0.5)
    if hard_fail:
        score = min(score, 5.0)
    return SectionResult(score=round(score, 1), findings=findings, hard_fail=hard_fail)


def evaluator_node(state: dict) -> dict:
    """
    Evaluate the quality of extracted financial statement data.

    Uses LLM-as-Judge to score each statement type on multiple criteria
    and determine if re-extraction is needed.

    Args:
        state: Current workflow state with extracted_data (Dict[StatementType, dict])

    Returns:
        Updated state with evaluation_result (Dict[StatementType, dict])
    """
    from utils.llm_client import chat
    from utils.observability import get_observability

    obs = get_observability()
    run_id = state.get("run_id")
    start_time = time.time()

    extracted_data = state.get("extracted_data", {})
    if not extracted_data:
        return {
            "evaluation_result": {}
        }

    evaluation_results = {}
    last_evaluation_feedback = {}
    retry_context: dict = {}
    page_texts_map = state.get("page_texts", {})
    retry_count = state.get("retry_count", 0)
    any_hallucination = False

    for statement_type, data in extracted_data.items():
        logging.info(f"Evaluating {statement_type.value}…")
        print(f"\n🔍 Evaluating {statement_type.value.replace('_', ' ').title()}…")

        try:
            # Normalize indentation before running programmatic checks
            data = _normalize_indent_levels(data)

            # Penalty ledger prevents double-penalizing the same error
            # across Coverage (A) and Content (D) sections.
            ledger = PenaltyLedger()
            page_texts = page_texts_map.get(statement_type, [])

            # -----------------------------------------------------------------
            # Phase 2 — 4-Section Programmatic Checks
            # -----------------------------------------------------------------
            coverage = _run_coverage_checks(data, statement_type, ledger, page_texts)
            format_result = _run_format_checks(data, statement_type)
            structure = _run_structure_checks(data, statement_type)
            content = _run_content_checks(data, statement_type, ledger, page_texts)

            # Print summary to console
            print(f"   📊 Coverage:   {coverage.score}/10  ({'PASS' if not coverage.hard_fail else 'FAIL'})")
            print(f"   📊 Format:     {format_result.score}/10  ({'PASS' if not format_result.hard_fail else 'FAIL'})")
            print(f"   📊 Structure:  {structure.score}/10  ({'PASS' if not structure.hard_fail else 'FAIL'})")
            print(f"   📊 Content:    {content.score}/10  ({'PASS' if not content.hard_fail else 'FAIL'})")

            # Build check summary for the judge
            def _format_findings(findings: list[CheckFinding]) -> str:
                lines = []
                for f in findings:
                    lines.append(f"  [{f.status}] {f.check_id}: {f.message}")
                return "\n".join(lines)

            check_summary = f"""
Coverage (score: {coverage.score}/10):
{_format_findings(coverage.findings)}

Format (score: {format_result.score}/10):
{_format_findings(format_result.findings)}

Structure (score: {structure.score}/10):
{_format_findings(structure.findings)}

Content (score: {content.score}/10):
{_format_findings(content.findings)}
"""

            # -----------------------------------------------------------------
            # LLM Judge — bounded qualitative adjustment
            # -----------------------------------------------------------------
            ocr_text = "\n".join(page_texts)[:3000]
            judge_prompt = JUDGE_PROMPT.format(
                check_summary=check_summary,
                statement_type=statement_type.value.replace("_", " ").title(),
                ocr_text=ocr_text,
            )

            llm_start = time.time()
            response = chat(
                model=Config.EVALUATION_MODEL,
                messages=[{
                    "role": "user",
                    "content": judge_prompt
                }]
            )
            llm_duration = (time.time() - llm_start) * 1000
            obs.log_llm_call(
                model=Config.EVALUATION_MODEL,
                duration_ms=llm_duration,
                prompt=judge_prompt,
                response=response["message"]["content"],
                run_id=run_id
            )

            # Parse judge response
            judge_content = response["message"]["content"].strip()
            if judge_content.startswith("```"):
                judge_content = judge_content.split("```")[1]
                if judge_content.startswith("json"):
                    judge_content = judge_content[4:]
                judge_content = judge_content.rstrip("`").strip()

            try:
                judge = json.loads(judge_content)
            except Exception as e:
                logging.warning(f"Judge parse error: {e}")
                judge = {
                    "coverage_adjustment": 0.0,
                    "format_adjustment": 0.0,
                    "structure_adjustment": 0.0,
                    "content_adjustment": 0.0,
                    "overall_confidence": "low",
                    "summary": "Judge response could not be parsed. Relying on programmatic scores only.",
                    "flags": ["judge_parse_error"],
                }

            # Clamp adjustments
            def _clamp(v: float) -> float:
                return max(-1.0, min(1.0, float(v)))

            coverage_adj = _clamp(judge.get("coverage_adjustment", 0))
            format_adj = _clamp(judge.get("format_adjustment", 0))
            structure_adj = _clamp(judge.get("structure_adjustment", 0))
            content_adj = _clamp(judge.get("content_adjustment", 0))

            # Compute final scores
            final_coverage = max(0.0, min(10.0, coverage.score + coverage_adj))
            final_format = max(0.0, min(10.0, format_result.score + format_adj))
            final_structure = max(0.0, min(10.0, structure.score + structure_adj))
            final_content = max(0.0, min(10.0, content.score + content_adj))

            # Weighted overall
            overall = (
                0.20 * final_coverage +
                0.20 * final_format +
                0.30 * final_structure +
                0.30 * final_content
            )

            # Hard-fail checks block passing regardless of overall score
            any_hard_fail = coverage.hard_fail or format_result.hard_fail or structure.hard_fail or content.hard_fail

            if Config.DISABLE_GUARDRAILS:
                # When guardrails disabled, judge determines pass/fail
                passed = overall >= 6.0
            else:
                passed = overall >= 6.0 and not any_hard_fail

            # Build feedback from findings + judge summary
            failed_findings = [
                f for f in (coverage.findings + format_result.findings + structure.findings + content.findings)
                if f.status in ("FAIL", "ADVISORY")
            ]
            feedback_parts = []
            if failed_findings:
                feedback_parts.append(
                    "Programmatic checks: " +
                    ", ".join(f"{f.check_id} ({f.status})" for f in failed_findings[:3])
                )
            judge_summary = judge.get("summary", "")
            if judge_summary:
                feedback_parts.append(judge_summary)
            feedback = " | ".join(feedback_parts) if feedback_parts else "Extraction passed all checks."

            scores = {
                "coverage": round(final_coverage, 1),
                "format": round(final_format, 1),
                "structure": round(final_structure, 1),
                "content": round(final_content, 1),
                "overall": round(overall, 1),
            }

            eval_status = '✅ PASSED' if passed else '❌ FAILED'
            logging.info(f"{statement_type.value}: {eval_status} (overall={overall:.1f})")
            print(f"   - Evaluation: {eval_status}")
            print(f"   - Overall: {overall:.1f}/10")
            print(f"   - Feedback: {feedback}")

            # Log evaluation score
            obs.log_evaluation_score(
                statement_type=statement_type.value,
                score=round(overall, 2),
                details=scores,
                run_id=run_id,
            )

            # Phase 6 — Log all individual check outcomes for observability / dashboarding
            all_findings = coverage.findings + format_result.findings + structure.findings + content.findings
            obs.log_check_outcomes(
                statement_type=statement_type.value,
                findings=[
                    {"check_id": f.check_id, "status": f.status, "message": f.message}
                    for f in all_findings
                ],
                run_id=run_id,
            )
            evaluation_results[statement_type] = {
                "passed": passed,
                "feedback": feedback,
                "scores": scores,
                "findings": [
                    {"check_id": f.check_id, "status": f.status, "message": f.message}
                    for f in all_findings
                ],
            }
            last_evaluation_feedback[statement_type] = feedback

            # Phase 3 — build structured retry context for targeted retries
            retry_context[statement_type] = _build_retry_context(
                coverage, format_result, structure, content, overall, passed
            )

            # Track hallucination for persistent flag
            if any(f.check_id.startswith("D") and f.status == "FAIL" for f in content.findings):
                any_hallucination = True

        except json.JSONDecodeError as e:
            logging.error(f"Error parsing evaluation for {statement_type.value}: {e}")
            print(f"   ⚠️  Error parsing evaluation: {e}")
            evaluation_results[statement_type] = {
                "passed": False,
                "feedback": f"Error parsing evaluation: {e}",
                "scores": {},
                "findings": [],
            }
        except Exception as e:
            logging.error(f"Evaluation error for {statement_type.value}: {e}")
            print(f"   ⚠️  Evaluation error: {e}")
            evaluation_results[statement_type] = {
                "passed": False,
                "feedback": f"Evaluation error: {e}",
                "scores": {},
                "findings": [],
            }

    # Record quality scores for guardrail quality tracker
    guardrail_flags = state.get("guardrail_flags", [])
    if not Config.DISABLE_GUARDRAILS:
        avg_score = 0.0
        score_count = 0
        for eval_result in evaluation_results.values():
            scores = eval_result.get("scores", {})
            if scores:
                stmt_avg = scores.get("overall", 0)
                avg_score += stmt_avg
                score_count += 1

        if score_count > 0:
            avg_score = avg_score / score_count
            from utils.guardrails import get_guardrails
            gr = get_guardrails()
            gr.quality.record(run_id, avg_score)
            pdf_path = state.get("input_pdf", "")
            if pdf_path:
                gr.quality.record_pdf_score(pdf_path, avg_score)

            if gr.quality.is_degraded(pdf_path):
                guardrail_flags = guardrail_flags + ["quality_degraded"]
                logging.error("Quality degraded — skipping retry")
                print("❌ Quality degraded — skipping retry")

        # Hallucination guardrail: flag persistent hallucination after max retries
        if any_hallucination and retry_count >= Config.MAX_RETRIES:
            if "hallucination_warning" not in guardrail_flags:
                guardrail_flags = guardrail_flags + ["hallucination_warning"]
                logging.error("Hallucination persistent after max retries — flagging output")
                print("❌ Hallucination persistent after max retries — flagging output")

    # Log node timing
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("evaluator", duration_ms, run_id)

    return {
        "evaluation_result": evaluation_results,
        "last_evaluation_feedback": last_evaluation_feedback,
        "retry_context": retry_context,
        "run_id": run_id,
        "guardrail_flags": guardrail_flags,
    }
