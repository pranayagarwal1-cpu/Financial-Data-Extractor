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


# Evaluation prompts for each statement type
EVALUATION_PROMPTS = {
    StatementType.BALANCE_SHEET: """You are an expert financial document evaluator. Your task is to evaluate the quality of a balance sheet extraction from a PDF.

Evaluate the extracted data against these criteria:

1. **Completeness** (Critical): Does it have all three major sections - Assets, Liabilities, and Stockholders Equity/Shareholders Equity/Net Assets?
2. **Data Integrity**: Do subtotals appear consistent with line items? Does Assets = Liabilities + Equity?
3. **Period Consistency**: Are the same periods used across all sections?
4. **Format Validity**: Is the JSON structure valid with required fields?
5. **Missing Values**: Are there too many null/empty values (< 20% missing is acceptable)?

Here is the extracted balance sheet data:

{extracted_data}

Respond with ONLY valid JSON in this exact format:
{{
  "scores": {{
    "completeness": <score 0-10>,
    "data_integrity": <score 0-10>,
    "period_consistency": <score 0-10>,
    "format_validity": <score 0-10>,
    "missing_values": <score 0-10>
  }},
  "passed": <true/false>,
  "feedback": "<brief explanation of issues found or confirmation of quality>"
}}

Scoring guidelines:
- completeness: 10 if all 3 sections present (Assets, Liabilities, Equity), 0 if any missing
- data_integrity: 10 if no obvious mismatches and equation balances, lower if totals don't make sense
- period_consistency: 10 if same periods throughout
- format_validity: 10 if valid structure with all required fields
- missing_values: 10 if < 20% null/empty, lower for more missing data

An extraction PASSES if:
- completeness == 10 (all 3 sections present)
- format_validity == 10
- Average of all scores >= 7
""",

    StatementType.INCOME_STATEMENT: """You are an expert financial document evaluator. Your task is to evaluate the quality of an income statement extraction from a PDF.

Evaluate the extracted data against these criteria:

1. **Completeness** (Critical): Does it have Revenue, Expenses, and Net Income/Profit sections?
2. **Data Integrity**: Do subtotals appear consistent (e.g., Gross Profit = Revenue - COGS, Operating Income = Gross Profit - Operating Expenses)?
3. **Period Consistency**: Are the same periods used across all sections?
4. **Format Validity**: Is the JSON structure valid with required fields?
5. **Missing Values**: Are there too many null/empty values (< 20% missing is acceptable)?

Here is the extracted income statement data:

{extracted_data}

Respond with ONLY valid JSON in this exact format:
{{
  "scores": {{
    "completeness": <score 0-10>,
    "data_integrity": <score 0-10>,
    "period_consistency": <score 0-10>,
    "format_validity": <score 0-10>,
    "missing_values": <score 0-10>
  }},
  "passed": <true/false>,
  "feedback": "<brief explanation of issues found or confirmation of quality>"
}}

Scoring guidelines:
- completeness: 10 if Revenue, Expenses, and Net Income sections present
- data_integrity: 10 if subtotals reconcile logically
- period_consistency: 10 if same periods throughout
- format_validity: 10 if valid structure with all required fields
- missing_values: 10 if < 20% null/empty

An extraction PASSES if:
- completeness == 10 (Revenue, Expenses, Net Income present)
- format_validity == 10
- Average of all scores >= 7
""",

    StatementType.CASH_FLOW: """You are an expert financial document evaluator. Your task is to evaluate the quality of a cash flow statement extraction from a PDF.

Evaluate the extracted data against these criteria:

1. **Completeness** (Critical): Does it have Operating, Investing, and Financing Activities sections?
2. **Data Integrity**: Do subtotals appear consistent? Does Net Change in Cash reconcile with beginning and ending cash?
3. **Period Consistency**: Are the same periods used across all sections?
4. **Format Validity**: Is the JSON structure valid with required fields?
5. **Missing Values**: Are there too many null/empty values (< 20% missing is acceptable)?

Here is the extracted cash flow statement data:

{extracted_data}

Respond with ONLY valid JSON in this exact format:
{{
  "scores": {{
    "completeness": <score 0-10>,
    "data_integrity": <score 0-10>,
    "period_consistency": <score 0-10>,
    "format_validity": <score 0-10>,
    "missing_values": <score 0-10>
  }},
  "passed": <true/false>,
  "feedback": "<brief explanation of issues found or confirmation of quality>"
}}

Scoring guidelines:
- completeness: 10 if Operating, Investing, and Financing Activities sections present
- data_integrity: 10 if Net Change in Cash + Beginning Cash = Ending Cash
- period_consistency: 10 if same periods throughout
- format_validity: 10 if valid structure with all required fields
- missing_values: 10 if < 20% null/empty

An extraction PASSES if:
- completeness == 10 (all 3 activity sections present)
- format_validity == 10
- Average of all scores >= 7
""",
}


from decimal import Decimal


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


def _calculate_missing_ratio(data: dict) -> float:
    """Calculate the ratio of missing/null values in the extraction."""
    total_values = 0
    missing_values = 0

    for section in data.get("sections", []):
        for row in section.get("rows", []):
            for val in row.get("values", []):
                total_values += 1
                if val is None or val == "" or val == "null":
                    missing_values += 1

    if total_values == 0:
        return 1.0
    return missing_values / total_values


def _has_required_sections(data: dict, statement_type: StatementType) -> tuple[bool, list[str]]:
    """Check if all required sections are present for the statement type."""
    section_names = {s.get("name", "").upper() for s in data.get("sections", [])}

    required_keywords = {
        StatementType.BALANCE_SHEET: ["ASSET", "LIABILIT", "EQUITY"],
        StatementType.INCOME_STATEMENT: ["REVENUE", "EXPENSE", "NET INCOME"],
        StatementType.CASH_FLOW: ["OPERATING", "INVESTING", "FINANCING"],
    }

    keywords = required_keywords.get(statement_type, [])
    found_sections = []
    missing_sections = []

    for keyword in keywords:
        found = any(keyword in name for name in section_names)
        if found:
            found_sections.append(keyword)
        else:
            missing_sections.append(keyword)

    return len(missing_sections) == 0, missing_sections


def _find_subtotal_value(data: dict, keywords: list[str]) -> Optional[Decimal]:
    """Find a subtotal row whose label matches any keyword and return its first numeric value."""
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            if not row.get("is_subtotal"):
                continue
            label = row.get("label", "").lower()
            if any(kw in label for kw in keywords):
                for val in row.get("values", []):
                    parsed = _parse_amount(val)
                    if parsed is not None:
                        return parsed
    return None


def _run_equation_checks(data: dict, statement_type: StatementType) -> tuple[bool, str]:
    """Run programmatic accounting equation checks. Returns (passed, feedback)."""
    if statement_type == StatementType.BALANCE_SHEET:
        total_assets = _find_subtotal_value(data, ["total asset", "assets total", "total assets", "asset total"])
        total_liab = _find_subtotal_value(data, ["total liabilit", "liabilit total", "total liabilities", "liabilities total"])
        total_equity = _find_subtotal_value(data, ["total equity", "equity total", "shareholders equity", "stockholders equity", "net asset"])

        if total_assets is not None and total_liab is not None and total_equity is not None:
            expected = total_liab + total_equity
            diff = abs(total_assets - expected)
            tolerance = max(Decimal("1"), expected * Decimal("0.01"))
            if diff > tolerance:
                return False, (
                    f"Balance sheet does not balance: Assets ({total_assets}) ≠ "
                    f"Liabilities ({total_liab}) + Equity ({total_equity}) = {expected} (diff: {diff})"
                )

    elif statement_type == StatementType.INCOME_STATEMENT:
        revenue = _find_subtotal_value(data, ["total revenue", "revenue total", "gross revenue", "net revenue"])
        cogs = _find_subtotal_value(data, ["cost of goods", "cost of revenue", "cogs", "cost of sales"])
        gross_profit = _find_subtotal_value(data, ["gross profit", "gross income", "gross margin"])

        if revenue is not None and cogs is not None and gross_profit is not None:
            expected = revenue - cogs
            diff = abs(gross_profit - expected)
            tolerance = max(Decimal("1"), abs(expected) * Decimal("0.01"))
            if diff > tolerance:
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
            tolerance = max(Decimal("1"), abs(expected) * Decimal("0.01"))
            if diff > tolerance:
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
            tolerance = max(Decimal("1"), abs(expected) * Decimal("0.01"))
            if diff > tolerance:
                return False, (
                    f"Cash flow does not reconcile: Net Change ({net_change}) ≠ "
                    f"Ending ({ending}) - Beginning ({beginning}) = {expected} (diff: {diff})"
                )

    return True, ""


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

    for statement_type, data in extracted_data.items():
        logging.info(f"Evaluating {statement_type.value}…")
        print(f"\n🔍 Evaluating {statement_type.value.replace('_', ' ').title()}…")

        try:
            # Pre-checks
            has_sections, missing = _has_required_sections(data, statement_type)
            missing_ratio = _calculate_missing_ratio(data)
            numeric_score, numeric_feedback = _run_numeric_precheck(data, statement_type)

            print(f"   - Required sections present: {has_sections}")
            print(f"   - Missing value ratio: {missing_ratio:.1%}")
            print(f"   - Numeric parseability: {numeric_score}/10 ({numeric_feedback})")

            # Get statement-specific prompt
            prompt = EVALUATION_PROMPTS[statement_type].format(
                extracted_data=json.dumps(data, indent=2)
            )

            # Call LLM for evaluation with timing
            llm_start = time.time()
            response = chat(
                model=Config.EVALUATION_MODEL,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            llm_duration = (time.time() - llm_start) * 1000
            obs.log_llm_call(
                model=Config.EVALUATION_MODEL,
                duration_ms=llm_duration,
                prompt=prompt,
                response=response["message"]["content"],
                run_id=run_id
            )

            # Parse evaluation response
            eval_content = response["message"]["content"].strip()

            # Clean up markdown fences
            if eval_content.startswith("```"):
                eval_content = eval_content.split("```")[1]
                if eval_content.startswith("json"):
                    eval_content = eval_content[4:]
                eval_content = eval_content.rstrip("`").strip()

            evaluation = json.loads(eval_content)

            # Layer 3: Programmatic equation checks (hard override)
            code_passed, code_feedback = _run_equation_checks(data, statement_type)
            if not code_passed and evaluation.get("passed"):
                print(f"   ⚠️  Forcing FAIL (code check): {code_feedback}")
                logging.warning(f"{statement_type.value}: forcing FAIL — {code_feedback}")
                evaluation["passed"] = False
                scores = evaluation.get("scores", {})
                scores["data_integrity"] = min(scores.get("data_integrity", 10), 3)
                evaluation["feedback"] = f"{code_feedback} | {evaluation.get('feedback', '')}"

            eval_status = '✅ PASSED' if evaluation.get('passed') else '❌ FAILED'
            logging.info(f"{statement_type.value}: {eval_status}")
            print(f"   - Evaluation: {eval_status}")
            print(f"   - Feedback: {evaluation.get('feedback', 'No feedback')}")

            # Log evaluation score
            avg_score = sum(evaluation.get("scores", {}).values()) / max(len(evaluation.get("scores", {})), 1)
            obs.log_evaluation_score(
                statement_type=statement_type.value,
                score=round(avg_score, 2),
                details=evaluation.get("scores", {}),
                run_id=run_id
            )

            evaluation_results[statement_type] = {
                "passed": evaluation.get("passed", False),
                "feedback": evaluation.get("feedback", ""),
                "scores": evaluation.get("scores", {})
            }
            last_evaluation_feedback[statement_type] = evaluation.get("feedback", "")

        except json.JSONDecodeError as e:
            logging.error(f"Error parsing evaluation for {statement_type.value}: {e}")
            print(f"   ⚠️  Error parsing evaluation: {e}")
            evaluation_results[statement_type] = {
                "passed": False,
                "feedback": f"Error parsing evaluation: {e}",
                "scores": {}
            }
        except Exception as e:
            logging.error(f"Evaluation error for {statement_type.value}: {e}")
            print(f"   ⚠️  Evaluation error: {e}")
            evaluation_results[statement_type] = {
                "passed": False,
                "feedback": f"Evaluation error: {e}",
                "scores": {}
            }

    # Log node timing
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("evaluator", duration_ms, run_id)

    return {
        "evaluation_result": evaluation_results,
        "last_evaluation_feedback": last_evaluation_feedback,
        "run_id": run_id,
    }
