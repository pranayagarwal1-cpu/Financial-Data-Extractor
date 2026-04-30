"""
VLM utilities for financial statement extraction.

Supports:
- Balance Sheet (Statement of Financial Position)
- Income Statement (Statement of Earnings/Profit & Loss)
- Cash Flow Statement
"""

import re
import json
import time
import logging
from utils.ollama_client import chat
from enum import Enum
from typing import Dict


class StatementType(Enum):
    """Types of financial statements."""
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW = "cash_flow"


# Detection prompts for each statement type
DETECTION_PROMPTS = {
    StatementType.BALANCE_SHEET: (
        "Does this page contain a Balance Sheet (also called "
        "Consolidated Balance Sheet, Statement of Financial Position, "
        "or Statement of Assets and Liabilities)? "
        "Reply with only YES or NO."
    ),
    StatementType.INCOME_STATEMENT: (
        "Does this page contain an Income Statement (also called "
        "Consolidated Statement of Earnings, Statement of Operations, "
        "Profit & Loss Statement, or Statement of Comprehensive Income)? "
        "Reply with only YES or NO."
    ),
    StatementType.CASH_FLOW: (
        "Does this page contain a Cash Flow Statement (also called "
        "Consolidated Statement of Cash Flows or Statement of Cash Flows)? "
        "Reply with only YES or NO."
    ),
}

# Extraction prompts for each statement type
EXTRACTION_PROMPTS = {
    StatementType.BALANCE_SHEET: """Extract the complete balance sheet from this image.
Return ONLY valid JSON (no markdown fences, no explanation, no <think> blocks) with this exact structure:

{
  "title": "Statement title as shown",
  "statement_type": "balance_sheet",
  "periods": ["Period 1", "Period 2"],
  "sections": [
    {
      "name": "Section name (e.g. ASSETS, LIABILITIES, STOCKHOLDERS EQUITY)",
      "rows": [
        {
          "label": "Line item label",
          "values": ["value for period 1", "value for period 2"],
          "is_subtotal": false
        }
      ]
    }
  ]
}

Rules:
- Preserve all line items and their indentation meaning via label text.
- For subtotal/total rows set is_subtotal to true.
- Use null for missing values.
- Keep values as strings exactly as shown (e.g. "$1,234" or "1,234").
""",

    StatementType.INCOME_STATEMENT: """Extract the complete income statement from this image.
Return ONLY valid JSON (no markdown fences, no explanation, no <think> blocks) with this exact structure:

{
  "title": "Statement title as shown",
  "statement_type": "income_statement",
  "periods": ["Period 1", "Period 2"],
  "sections": [
    {
      "name": "Section name (e.g. REVENUE, COST OF REVENUE, OPERATING EXPENSES, NET INCOME)",
      "rows": [
        {
          "label": "Line item label",
          "values": ["value for period 1", "value for period 2"],
          "is_subtotal": false
        }
      ]
    }
  ]
}

Rules:
- Preserve all line items and their indentation meaning via label text.
- For subtotal/total rows (like Gross Profit, Operating Income, Net Income) set is_subtotal to true.
- Use null for missing values.
- Keep values as strings exactly as shown (e.g. "$1,234" or "1,234").
""",

    StatementType.CASH_FLOW: """Extract the complete cash flow statement from this image.
Return ONLY valid JSON (no markdown fences, no explanation, no <think> blocks) with this exact structure:

{
  "title": "Statement title as shown",
  "statement_type": "cash_flow",
  "periods": ["Period 1", "Period 2"],
  "sections": [
    {
      "name": "Section name (e.g. OPERATING ACTIVITIES, INVESTING ACTIVITIES, FINANCING ACTIVITIES)",
      "rows": [
        {
          "label": "Line item label",
          "values": ["value for period 1", "value for period 2"],
          "is_subtotal": false
        }
      ]
    }
  ]
}

Rules:
- Preserve all line items and their indentation meaning via label text.
- For subtotal/total rows (like Net Cash from Operating Activities) set is_subtotal to true.
- Use null for missing values.
- Keep values as strings exactly as shown (e.g. "$1,234" or "(1,234)" for negatives).
""",
}


def strip_vlm_response(raw: str) -> str:
    """
    Clean raw VLM output before JSON parsing.
    Handles:
      - qwen3.5 <think>...</think> reasoning blocks
      - Markdown ```json ... ``` fences
    """
    # Remove <think>...</think> blocks (qwen3.5 thinking mode)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Remove markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rstrip("`").strip()
    return raw.strip()


def vlm_detect_all_statements(image_path: str, model: str, run_id: str = None) -> Dict[StatementType, bool]:
    """
    Ask the VLM which financial statements are on this page in a single call.

    Args:
        image_path: Path to the rasterized page image
        model: Ollama model name to use
        run_id: Optional run ID for observability tracking

    Returns:
        Dict mapping StatementType to bool (True if present)
    """
    from utils.observability import get_observability
    obs = get_observability()
    start_time = time.time()

    prompt = """Look at this financial document page. Which of the following statements are present?
- Balance Sheet (Statement of Financial Position, Assets, Liabilities, Equity)
- Income Statement (Statement of Earnings, Profit & Loss, Revenue, Expenses, Net Income)
- Cash Flow Statement (Operating Activities, Investing Activities, Financing Activities)

Reply with ONLY valid JSON in this exact format:
{
  "balance_sheet": true,
  "income_statement": true,
  "cash_flow": false
}"""

    response = chat(
        model=model,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [image_path],
        }]
    )

    duration_ms = (time.time() - start_time) * 1000
    obs.log_llm_call(
        model=model,
        duration_ms=duration_ms,
        prompt=prompt,
        response=response["message"]["content"],
        run_id=run_id
    )

    # Parse response
    result = {st: False for st in StatementType}
    content = response["message"]["content"].strip()

    # Clean up markdown fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rstrip("`").strip()

    try:
        import json as json_lib
        parsed = json_lib.loads(content)
        for st in StatementType:
            result[st] = bool(parsed.get(st.value, False))
    except Exception as e:
        logging.warning(f"Failed to parse detection response: {e}")

    return result


def vlm_is_statement_page(image_path: str, statement_type: StatementType, model: str, run_id: str = None) -> bool:
    """
    Ask the VLM whether this page contains a specific financial statement.
    Deprecated: Use vlm_detect_all_statements() for 3x speedup.

    Args:
        image_path: Path to the rasterized page image
        statement_type: Type of statement to detect
        model: Ollama model name to use
        run_id: Optional run ID for observability tracking

    Returns:
        True if the page contains the specified statement
    """
    from utils.observability import get_observability
    obs = get_observability()
    start_time = time.time()

    response = chat(
        model=model,
        messages=[{
            "role": "user",
            "content": DETECTION_PROMPTS[statement_type],
            "images": [image_path],
        }]
    )

    duration_ms = (time.time() - start_time) * 1000
    obs.log_llm_call(
        model=model,
        duration_ms=duration_ms,
        prompt=DETECTION_PROMPTS[statement_type],
        response=response["message"]["content"],
        run_id=run_id
    )

    answer = response["message"]["content"].strip().upper()
    return answer.startswith("YES")


def vlm_extract_statement(image_path: str, statement_type: StatementType, model: str, run_id: str = None) -> dict:
    """
    Ask the VLM to extract a financial statement as structured JSON.

    Args:
        image_path: Path to the rasterized page image
        statement_type: Type of statement to extract
        model: Ollama model name to use
        run_id: Optional run ID for observability tracking

    Returns:
        Dict with keys: title, statement_type, periods, sections
    """
    from utils.observability import get_observability
    obs = get_observability()
    start_time = time.time()

    response = chat(
        model=model,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPTS[statement_type],
            "images": [image_path],
        }]
    )

    duration_ms = (time.time() - start_time) * 1000
    obs.log_llm_call(
        model=model,
        duration_ms=duration_ms,
        prompt=EXTRACTION_PROMPTS[statement_type],
        response=response["message"]["content"],
        run_id=run_id
    )

    raw = response["message"]["content"].strip()
    raw = strip_vlm_response(raw)
    return json.loads(raw)


# Legacy function names for backward compatibility
def vlm_is_balance_sheet_page(image_path: str, model: str) -> bool:
    """Legacy: Check if page contains balance sheet."""
    return vlm_is_statement_page(image_path, StatementType.BALANCE_SHEET, model)


def vlm_extract_balance_sheet(image_path: str, model: str) -> dict:
    """Legacy: Extract balance sheet data."""
    return vlm_extract_statement(image_path, StatementType.BALANCE_SHEET, model)
