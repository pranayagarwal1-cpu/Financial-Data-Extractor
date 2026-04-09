"""
LLM-based financial statement page detection.

Instead of keyword matching, we extract all text and let the LLM identify
which pages contain each type of financial statement. This handles international
documents with varying terminology.
"""

import ollama
import pdfplumber
import json
import re
import time
from typing import List, Optional, Dict

from utils.vlm_utils import StatementType


# Detection descriptions for each statement type
STATEMENT_DESCRIPTIONS = {
    StatementType.BALANCE_SHEET: """Balance Sheet (also known as):
- Statement of Financial Position
- Statement of Assets and Liabilities
- Statement of Net Assets
- Consolidated Balance Sheet

Shows: Assets, Liabilities, Equity/Net Assets""",

    StatementType.INCOME_STATEMENT: """Income Statement (also known as):
- Statement of Earnings
- Statement of Operations
- Profit & Loss Statement (P&L)
- Statement of Comprehensive Income
- Consolidated Statement of Income

Shows: Revenue, Expenses, Net Income/Profit""",

    StatementType.CASH_FLOW: """Cash Flow Statement (also known as):
- Consolidated Statement of Cash Flows
- Statement of Changes in Cash

Shows: Operating Activities, Investing Activities, Financing Activities, Cash at Beginning/End""",
}


def extract_all_page_texts(pdf_path: str) -> List[dict]:
    """
    Extract text from all pages of a PDF.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        List of dicts with page_num and text content
    """
    pages_data = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            pages_data.append({
                "page_num": page_num,
                "text": text if text else ""
            })

    return pages_data


def _detect_statements_vlm_fallback(
    pdf_path: str,
    statement_types: List[StatementType],
    model: str,
    run_id: Optional[str] = None
) -> Dict[StatementType, List[int]]:
    """
    VLM-based fallback for scanned PDFs without text layers.

    Rasterizes each page and uses vision model to detect financial statements.

    Args:
        pdf_path: Path to the PDF file
        statement_types: List of statement types to detect
        model: Ollama model to use
        run_id: Optional run ID for observability tracking

    Returns:
        Dict mapping StatementType to list of page numbers
    """
    from config import Config
    from utils.pdf_utils import rasterize_page
    from utils.vlm_utils import vlm_is_statement_page
    import tempfile
    import os

    result = {st: [] for st in statement_types}

    # Get total page count
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)

    print(f"🔍 Analyzing {total_pages} pages using vision model...")

    # Create temp directory for page images
    with tempfile.TemporaryDirectory() as tmp_dir:
        for page_num in range(1, total_pages + 1):
            print(f"  Processing page {page_num}/{total_pages}...")

            # Rasterize page
            image_prefix = os.path.join(tmp_dir, f"page_{page_num}")
            try:
                image_path = rasterize_page(pdf_path, page_num, image_prefix)
            except Exception as e:
                print(f"  ⚠️  Failed to rasterize page {page_num}: {e}")
                continue

            # Check each statement type
            for st in statement_types:
                is_match = vlm_is_statement_page(image_path, st, model, run_id)
                if is_match:
                    result[st].append(page_num)
                    print(f"    ✅ Found {st.value} on page {page_num}")

            # Clean up image
            try:
                os.remove(image_path)
            except:
                pass

    # Summary
    for st, pages in result.items():
        if pages:
            print(f"✅ {st.value}: pages {pages}")
        else:
            print(f"❌ {st.value}: not found")

    return result


def find_statement_pages_llm(
    pdf_path: str,
    statement_types: List[StatementType] = None,
    model: Optional[str] = None,
    run_id: Optional[str] = None
) -> Dict[StatementType, List[int]]:
    """
    Use LLM to identify pages for each specified financial statement type.

    Sends all page texts to the LLM in a single call and asks it to
    identify which pages contain each type of statement.

    Args:
        pdf_path: Path to the PDF file
        statement_types: List of statement types to detect (default: all three)
        model: Ollama model to use
        run_id: Optional run ID for observability tracking

    Returns:
        Dict mapping StatementType to list of page numbers
    """
    from config import Config
    from utils.observability import get_observability

    obs = get_observability()
    model = model or Config.EXTRACTION_MODEL
    statement_types = statement_types or list(StatementType)

    # Extract all page texts
    print("📄 Extracting text from PDF…")
    pages_data = extract_all_page_texts(pdf_path)
    total_pages = len(pages_data)

    # Check if text extraction was successful (hybrid detection fallback)
    total_text_length = sum(len(p["text"]) for p in pages_data)
    has_text_layer = total_text_length > 100  # At least 100 chars across all pages

    if not has_text_layer:
        print("⚠️  No text layer detected (scanned PDF). Falling back to VLM-based image analysis…")
        return _detect_statements_vlm_fallback(pdf_path, statement_types, model, run_id)

    # Build page summaries
    page_summaries = []
    for p in pages_data:
        text_preview = p["text"][:500] if p["text"] else "[No text extracted]"
        page_summaries.append(f"Page {p['page_num']}:\n{text_preview}")

    pages_context = "\n\n---\n\n".join(page_summaries)

    # Build the list of statements to find
    statements_to_find = "\n\n".join([
        f"{i+1}. {STATEMENT_DESCRIPTIONS[st]}"
        for i, st in enumerate(statement_types)
    ])

    prompt = f"""You are analyzing a financial document to identify different financial statement pages.

The document has {total_pages} pages. Below is the text content from each page:

{pages_context}

---

TASK: Identify which page(s) contain each of the following financial statements:

{statements_to_find}

IMPORTANT:
- Return ONLY a JSON object with statement type keys and page number arrays as values
- If a statement is not found, return an empty array for that type
- Page numbers should be integers (1-indexed)

RESPONSE FORMAT (JSON object only, no explanation):
{{
    "balance_sheet": [19],
    "income_statement": [18],
    "cash_flow": [20]
}}
"""

    print(f"🤖 Asking LLM to identify financial statement pages (model: {model})…")

    start_time = time.time()
    response = ollama.chat(
        model=model,
        messages=[{
            "role": "user",
            "content": prompt
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
    content = response["message"]["content"].strip()

    # Clean up markdown fences
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rstrip("`").strip()

    # Extract JSON object
    json_match = re.search(r'\{.*?\}', content, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group())
            if isinstance(result, dict):
                # Convert string keys to StatementType and validate page numbers
                output = {}
                for st in statement_types:
                    key = st.value
                    pages = result.get(key, [])
                    if isinstance(pages, list):
                        valid_pages = [
                            p for p in pages
                            if isinstance(p, int) and 1 <= p <= total_pages
                        ]
                        output[st] = valid_pages
                    else:
                        output[st] = []
                return output
        except Exception as e:
            print(f"⚠️  Error parsing JSON: {e}")

    print("⚠️  Could not parse LLM response, returning empty results")
    return {st: [] for st in statement_types}


def find_balance_sheet_pages_llm(pdf_path: str, model: Optional[str] = None) -> List[int]:
    """
    Legacy function: Find balance sheet pages only.

    Args:
        pdf_path: Path to the PDF file
        model: Ollama model to use

    Returns:
        List of page numbers containing balance sheets
    """
    result = find_statement_pages_llm(pdf_path, [StatementType.BALANCE_SHEET], model)
    return result.get(StatementType.BALANCE_SHEET, [])
