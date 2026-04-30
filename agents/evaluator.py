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
    from utils.ollama_client import chat
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

    for statement_type, data in extracted_data.items():
        logging.info(f"Evaluating {statement_type.value}…")
        print(f"\n🔍 Evaluating {statement_type.value.replace('_', ' ').title()}…")

        try:
            # Pre-checks
            has_sections, missing = _has_required_sections(data, statement_type)
            missing_ratio = _calculate_missing_ratio(data)

            print(f"   - Required sections present: {has_sections}")
            print(f"   - Missing value ratio: {missing_ratio:.1%}")

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

            evaluation_results[statement_type.value] = {
                "passed": evaluation.get("passed", False),
                "feedback": evaluation.get("feedback", ""),
                "scores": evaluation.get("scores", {})
            }

        except json.JSONDecodeError as e:
            logging.error(f"Error parsing evaluation for {statement_type.value}: {e}")
            print(f"   ⚠️  Error parsing evaluation: {e}")
            evaluation_results[statement_type.value] = {
                "passed": False,
                "feedback": f"Error parsing evaluation: {e}",
                "scores": {}
            }
        except Exception as e:
            logging.error(f"Evaluation error for {statement_type.value}: {e}")
            print(f"   ⚠️  Evaluation error: {e}")
            evaluation_results[statement_type.value] = {
                "passed": False,
                "feedback": f"Evaluation error: {e}",
                "scores": {}
            }

    # Log node timing
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("evaluator", duration_ms, run_id)

    return {"evaluation_result": evaluation_results, "run_id": run_id}
