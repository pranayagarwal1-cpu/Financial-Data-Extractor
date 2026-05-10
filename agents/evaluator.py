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


def _run_section_sum_checks(data: dict, statement_type: StatementType) -> tuple[bool, str]:
    """
    Verify additive subtotals equal the sum of their non-subtotal line items.
    Only checks sections where subtotals are expected to be sums (COGS, Expenses,
    Assets, Liabilities, Equity). Skips derived metrics (Gross Margin, Operating
    Income, Net Income).
    """
    additive_section_keywords = ["cost", "expense", "asset", "liabilit", "equity"]
    non_additive_total_keywords = ["gross", "margin", "operating income", "net income", "profit"]

    for section in data.get("sections", []):
        section_name = section.get("name", "").lower()
        rows = section.get("rows", [])
        if not rows:
            continue

        num_periods = len(rows[0].get("values", [])) if rows else 0
        if num_periods == 0:
            continue

        is_additive_section = any(kw in section_name for kw in additive_section_keywords)

        subtotals = []
        period_sums = [Decimal("0") for _ in range(num_periods)]
        has_non_subtotal = False
        has_negative_line_item = False

        for row in rows:
            is_sub = row.get("is_subtotal", False)
            vals = row.get("values", [])
            label = row.get("label", "").lower()

            if is_sub:
                # Skip subtotals that are clearly derived metrics, not sums
                if any(kw in label for kw in non_additive_total_keywords):
                    continue
                parsed_vals = [_parse_amount(v) for v in vals]
                subtotals.append({"label": row.get("label", ""), "values": parsed_vals})
            else:
                has_non_subtotal = True
                for i, v in enumerate(vals):
                    if i < num_periods:
                        parsed = _parse_amount(v)
                        if parsed is not None:
                            # Negative line items are typically deductions/adjustments
                            # and should not be blindly added to the sum
                            if parsed < 0:
                                has_negative_line_item = True
                            else:
                                period_sums[i] += parsed

        if not has_non_subtotal or not subtotals:
            continue

        # For non-additive sections, only enforce if subtotal explicitly says "total"
        if not is_additive_section:
            subtotals = [s for s in subtotals if "total" in s["label"].lower()]
            if not subtotals:
                continue

        for sub in subtotals:
            for i, sub_val in enumerate(sub["values"]):
                if sub_val is None or i >= num_periods:
                    continue
                computed = period_sums[i]
                if computed == 0:
                    continue

                # If the section contains negative line items, the subtotal is likely
                # a net figure rather than a simple sum — skip the check
                if has_negative_line_item:
                    continue

                diff = abs(sub_val - computed)
                tolerance = max(Decimal("1"), abs(computed) * Decimal("0.02"))

                # If discrepancy is within tolerance, it's fine
                if diff <= tolerance:
                    continue

                # If subtotal and computed sum are wildly different (ratio > 2x),
                # assume structural complexity rather than extraction error
                if sub_val != 0:
                    ratio = abs(computed) / abs(sub_val)
                    if ratio > Decimal("2") or ratio < Decimal("0.5"):
                        continue

                label = sub["label"]
                period_label = (
                    data.get("periods", [f"col {i + 1}"])[i]
                    if i < len(data.get("periods", []))
                    else f"col {i + 1}"
                )
                return False, (
                    f"Section '{section.get('name', '')}' subtotal '{label}' "
                    f"does not sum: {sub_val} ≠ {computed} (expected sum of line items) "
                    f"for period {period_label} (diff: {diff})"
                )

    return True, ""


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


def _value_in_text(val: str, text: str) -> bool:
    """Check if a value (in any common form) appears in the page text."""
    text_lower = text.lower()
    for form in _value_forms(val):
        if form.lower() in text_lower:
            return True
    return False


def _run_hallucination_check(data: dict, page_texts: list[str]) -> tuple[bool, str]:
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

    # Tier 1: subtotals must be present (strict)
    missing_subtotals = 0
    for val in subtotal_values:
        if not _value_in_text(val, all_text):
            missing_subtotals += 1

    if missing_subtotals > 3:
        return (
            False,
            f"{missing_subtotals}/{len(subtotal_values)} subtotal values not found in source page text"
        )

    # Tier 2: all values should be present (lenient)
    missing_all = 0
    for val in all_values:
        if not _value_in_text(val, all_text):
            missing_all += 1

    if missing_all > 5:
        return (
            False,
            f"{missing_all}/{len(all_values)} values not found in source page text — "
            "possible OCR/VLM misread (e.g., digits transposed like 11564 vs 11664)"
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
    page_texts_map = state.get("page_texts", {})
    retry_count = state.get("retry_count", 0)
    any_hallucination = False

    for statement_type, data in extracted_data.items():
        logging.info(f"Evaluating {statement_type.value}…")
        print(f"\n🔍 Evaluating {statement_type.value.replace('_', ' ').title()}…")

        try:
            # Pre-checks
            has_sections, missing = _has_required_sections(data, statement_type)
            missing_ratio = _calculate_missing_ratio(data)

            if not Config.DISABLE_GUARDRAILS:
                # Layer 0: Hallucination guardrail
                page_texts = page_texts_map.get(statement_type, [])
                hallucination_passed, hallucination_feedback = _run_hallucination_check(data, page_texts)
                print(f"   - Hallucination check: {'passed' if hallucination_passed else 'FAILED'}")
                if not hallucination_passed:
                    feedback = (
                        f"Hallucination detected: {hallucination_feedback}. "
                        "Extract ONLY values visibly printed in the image. "
                        "Do not compute or infer totals."
                    )
                    print(f"   ❌ Pre-check FAIL: {feedback}")
                    logging.warning(f"{statement_type.value}: hallucination FAIL — {feedback}")
                    evaluation_results[statement_type] = {
                        "passed": False,
                        "feedback": feedback,
                        "scores": {"data_integrity": 0}
                    }
                    last_evaluation_feedback[statement_type] = feedback
                    obs.log_evaluation_score(
                        statement_type=statement_type.value,
                        score=0.0,
                        details={"data_integrity": 0},
                        run_id=run_id,
                    )
                    any_hallucination = True
                    continue

                numeric_score, numeric_feedback = _run_numeric_precheck(data, statement_type)

                print(f"   - Required sections present: {has_sections}")
                print(f"   - Missing value ratio: {missing_ratio:.1%}")
                print(f"   - Numeric parseability: {numeric_score}/10 ({numeric_feedback})")

                # Hard-fail: too many values are unparsable — LLM judge cannot add value here
                if numeric_score < 5.0:
                    feedback = f"Numeric data largely unparsable: {numeric_feedback}"
                    print(f"   ❌ Pre-check FAIL: {feedback}")
                    logging.warning(f"{statement_type.value}: pre-check FAIL — {feedback}")
                    evaluation_results[statement_type] = {
                        "passed": False,
                        "feedback": feedback,
                        "scores": {"format_validity": 0}
                    }
                    last_evaluation_feedback[statement_type] = feedback
                    obs.log_evaluation_score(
                        statement_type=statement_type.value,
                        score=0.0,
                        details={"format_validity": 0},
                        run_id=run_id,
                    )
                    continue
            else:
                print(f"   - Guardrails disabled: skipping hallucination and numeric pre-checks")

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

            if not Config.DISABLE_GUARDRAILS:
                # Layer 3: Programmatic equation checks (hard override)
                code_passed, code_feedback = _run_equation_checks(data, statement_type)
                if not code_passed and evaluation.get("passed"):
                    print(f"   ⚠️  Forcing FAIL (code check): {code_feedback}")
                    logging.warning(f"{statement_type.value}: forcing FAIL — {code_feedback}")
                    evaluation["passed"] = False
                    scores = evaluation.get("scores", {})
                    scores["data_integrity"] = min(scores.get("data_integrity", 10), 3)
                    evaluation["feedback"] = f"{code_feedback} | {evaluation.get('feedback', '')}"

                # Layer 3b: Section-level sum reconciliation (advisory)
                sum_passed, sum_feedback = _run_section_sum_checks(data, statement_type)
                if not sum_passed:
                    print(f"   ⚠️  Advisory (sum check): {sum_feedback}")
                    logging.warning(f"{statement_type.value}: advisory — {sum_feedback}")
                    # Do NOT force fail — revenue sections often contain deductions.
                    # Reduce data_integrity score and surface feedback for the LLM evaluator.
                    scores = evaluation.get("scores", {})
                    scores["data_integrity"] = min(scores.get("data_integrity", 10), 5)
                    evaluation["feedback"] = f"{sum_feedback} | {evaluation.get('feedback', '')}"

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

    # Record quality scores for guardrail quality tracker
    guardrail_flags = state.get("guardrail_flags", [])
    if not Config.DISABLE_GUARDRAILS:
        avg_score = 0.0
        score_count = 0
        for eval_result in evaluation_results.values():
            scores = eval_result.get("scores", {})
            if scores:
                stmt_avg = sum(scores.values()) / len(scores)
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
        "run_id": run_id,
        "guardrail_flags": guardrail_flags,
    }
