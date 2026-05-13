"""
Extraction Agent - Extracts financial statement data from PDF pages.

Supports:
- Balance Sheet (Statement of Financial Position)
- Income Statement (Statement of Earnings)
- Cash Flow Statement

Responsibilities:
- Rasterize statement pages at high DPI
- Call VLM to extract structured JSON data
- Merge data from multi-page statements
- Handle extraction errors and retries
"""

import os
import logging
import re
import time
from pathlib import Path
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.pdf_utils import rasterize_page, _ocr_image
from utils.vlm_utils import vlm_extract_statement, StatementType
from config import Config

# Base directory
BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"


def _extract_page_text(pdf_path: str, page_num: int) -> str:
    """Extract raw text from a single PDF page (1-indexed) using pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            if 1 <= page_num <= len(pdf.pages):
                return pdf.pages[page_num - 1].extract_text() or ""
    except Exception as e:
        logging.warning(f"Could not extract text from page {page_num}: {e}")
    return ""


def _save_ocr_texts(pdf_path: str, statement_pages: dict, page_texts: dict):
    """
    Persist OCR/page text to tmp/extract_{name}/ocr/ for manual inspection.

    Saves one .txt file per page per statement type so users can review
    the exact ground-truth text used by hallucination checks.
    """
    pdf_name = Path(pdf_path).stem
    ocr_dir = TMP_DIR / f"extract_{pdf_name}" / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)

    for st, texts in page_texts.items():
        pages = statement_pages.get(st, [])
        for idx, text in enumerate(texts):
            page_num = pages[idx] if idx < len(pages) else (idx + 1)
            fname = f"{st.value}_p{page_num:04d}.txt"
            path = ocr_dir / fname
            with open(path, "w", encoding="utf-8") as f:
                f.write(text if text else "")
    logging.info(f"OCR text saved to: {ocr_dir}")
    print(f"📄 OCR text saved to: {ocr_dir}")


def get_temp_dir(pdf_path: str) -> str:
    """Get a unique temp directory for a PDF file."""
    pdf_name = Path(pdf_path).stem
    temp_dir = TMP_DIR / f"extract_{pdf_name}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir)


def _build_extraction_prompt(
    statement_type: StatementType,
    feedback: str = "",
    retry_context: Optional[dict] = None,
    ocr_text: str = "",
    retry_count: int = 0,
) -> str:
    """
    Build extraction prompt, appending evaluator feedback on retry.

    Phase 3 — Targeted prompt mutation:
    - Attempt 1: VLM only (generic feedback)
    - Attempt 2: VLM + OCR diff context
    - Attempt 3: Higher-capability model hint + full OCR context
    """
    from utils.vlm_utils import EXTRACTION_PROMPTS
    base = EXTRACTION_PROMPTS[statement_type]
    if not feedback and not retry_context:
        return base

    parts = [base]
    parts.append("IMPORTANT — CORRECTIONS FROM PREVIOUS EXTRACTION ATTEMPT:")

    # Phase 3 — targeted prompt addendum based on failure categories
    if retry_context and retry_context.get("targeted_prompt_addendum"):
        parts.append(retry_context["targeted_prompt_addendum"])

    if feedback:
        parts.append(feedback)

    # Escalation: Attempt 2+ inject OCR text as ground-truth reference
    if retry_count >= 2 and ocr_text:
        # Truncate to avoid overwhelming the context window
        ocr_snippet = ocr_text[:2000].replace("\n", " ")
        parts.append(
            f"GROUND-TRUTH OCR TEXT FROM SOURCE (for reference only): {ocr_snippet}"
        )

    # Escalation: Attempt 3 hint to use best reasoning
    if retry_count >= 3:
        parts.append(
            "This is the final extraction attempt. Use your highest-capability reasoning. "
            "Verify every total with arithmetic before responding."
        )

    parts.append("Please ensure all issues above are fixed in this extraction.")
    return "\n\n".join(parts)


def extractor_node(state: dict) -> dict:
    """
    Extract financial statement data from identified pages.

    Supports selective retry: on re-extraction, only failed statement types
    are re-processed. Passed types from the previous attempt are preserved.
    Feedback from the evaluator is injected into the VLM prompt.

    Args:
        state: Current workflow state with:
            - statement_pages: Dict[StatementType, List[int]]
            - statement_types: List[StatementType] to extract
            - evaluation_result: Optional, used on retry to find failures
            - last_evaluation_feedback: Optional, injected into retry prompt
            - extracted_data: Optional, preserved for passed types on retry

    Returns:
        Updated state with extracted_data: Dict[StatementType, dict]
    """
    from config import Config
    from utils.observability import get_observability

    obs = get_observability()
    run_id = state.get("run_id")
    start_time = time.time()

    pdf_path = state.get("input_pdf")
    statement_pages = state.get("statement_pages", {})
    statement_types = state.get("statement_types", [StatementType.BALANCE_SHEET])
    retry_count = state.get("retry_count", 0)

    if not pdf_path:
        return {"error_message": "No input PDF path provided"}

    if not statement_pages:
        return {"error_message": "No statement pages identified"}

    # Increment retry count
    new_retry_count = retry_count + 1

    # Determine which statement types to extract
    types_to_extract = list(statement_types)
    all_data: Dict[StatementType, dict] = {}

    if retry_count > 0:
        evaluation = state.get("evaluation_result", {})
        failed_types = [
            st for st in statement_types
            if not evaluation.get(st, {}).get("passed", False)
        ]
        if failed_types and len(failed_types) < len(statement_types):
            types_to_extract = failed_types
            # Preserve passed types from previous extraction
            prev_extracted = state.get("extracted_data", {})
            for st in statement_types:
                if st not in failed_types and st in prev_extracted:
                    all_data[st] = prev_extracted[st]
            print(
                f"🔄 Selective retry: {len(failed_types)} of {len(statement_types)} "
                f"statement type(s) need re-extraction"
            )

    # Extract source page texts for hallucination guardrail
    page_texts: Dict[StatementType, List[str]] = {}
    prev_page_texts = state.get("page_texts", {})
    for st in statement_types:
        pages = statement_pages.get(st, [])
        if st in types_to_extract:
            texts = []
            for page_num in pages:
                text = _extract_page_text(pdf_path, page_num)
                texts.append(text)
            page_texts[st] = texts
        elif st in prev_page_texts:
            page_texts[st] = prev_page_texts[st]

    # Get temp directory for this extraction
    tmp_dir = get_temp_dir(pdf_path)
    cache_dir = os.path.join(tmp_dir, "image_cache")
    os.makedirs(cache_dir, exist_ok=True)

    logging.info(f"Extraction attempt {new_retry_count}/{Config.MAX_RETRIES + 1}")
    print(f"🔄 Extraction attempt {new_retry_count}/{Config.MAX_RETRIES + 1}")
    print(f"📂 Temp directory: {tmp_dir}")
    print("Extracting data with VLM…\n")

    # Load evaluator feedback and retry context for targeted prompt injection
    feedback_map = state.get("last_evaluation_feedback", {})
    retry_context_map = state.get("retry_context", {})

    # Phase 3 — Escalation ladder: attempt 3 switches to higher-capability model
    extraction_model = Config.EXTRACTION_MODEL
    if new_retry_count >= 3 and Config.RETRY_EXTRACTION_MODEL:
        extraction_model = Config.RETRY_EXTRACTION_MODEL
        logging.info(f"Retry escalation: using model {extraction_model}")
        print(f"🔼 Retry escalation: switching to {extraction_model}")

    def extract_single_page(statement_type: StatementType, page_num: int) -> Optional[dict]:
        """Extract data from a single page. Returns extracted data or None."""
        ext_prefix = os.path.join(cache_dir, f"extract_{statement_type.value}_p{page_num:04d}")
        img_path = os.path.join(cache_dir, f"p{page_num:04d}.jpg")

        # Only rasterize if not cached
        if not os.path.exists(img_path):
            img_path = rasterize_page(
                pdf_path, page_num, ext_prefix, dpi=Config.EXTRACT_DPI,
                auto_rotate=Config.AUTO_CORRECT_ORIENTATION
            )

        try:
            feedback = feedback_map.get(statement_type, "")
            ctx = retry_context_map.get(statement_type)
            ocr_text = "\n".join(page_texts.get(statement_type, []))
            prompt = _build_extraction_prompt(
                statement_type,
                feedback=feedback,
                retry_context=ctx,
                ocr_text=ocr_text,
                retry_count=new_retry_count,
            ) if (feedback or ctx) else None
            page_data = vlm_extract_statement(
                img_path, statement_type, extraction_model,
                run_id=run_id, prompt=prompt
            )
            logging.info(f"  Page {page_num} extracted successfully")
            return page_data
        except Exception as e:
            logging.error(f"  Error extracting page {page_num}: {e}")
            print(f"  ⚠️  Error extracting page {page_num}: {e}")
            return None

    def extract_statement_type(statement_type: StatementType) -> tuple[StatementType, Optional[dict]]:
        """Extract all pages for a single statement type."""
        pages = statement_pages.get(statement_type, [])
        if not pages:
            logging.info(f"No pages found for {statement_type.value}")
            return (statement_type, None)

        logging.info(f"Extracting {statement_type.value} from pages {pages}")
        print(f"\n📊 Extracting {statement_type.value.replace('_', ' ').title()}…")

        statement_data: Optional[dict] = None
        existing_section_names: set[str] = set()
        normalized_to_actual: dict[str, str] = {}

        def _normalize_section_name(name: str) -> str:
            """Normalize for matching: lower, strip continuation suffixes."""
            name = name.strip().lower()
            name = re.sub(r"\s*\(continued\)", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s*-\s*continued", "", name, flags=re.IGNORECASE)
            return name

        def _row_key(row: dict) -> tuple:
            """Unique key for deduplication."""
            return (row.get("label", ""), tuple(row.get("values", [])))

        for page_num in pages:
            print(f"  Extracting page {page_num}…")
            page_data = extract_single_page(statement_type, page_num)

            if page_data is None:
                continue

            if statement_data is None:
                statement_data = page_data
                existing_section_names = {s["name"] for s in statement_data.get("sections", [])}
                normalized_to_actual = {
                    _normalize_section_name(s["name"]): s["name"]
                    for s in statement_data.get("sections", [])
                }
            else:
                # Merge continuation pages
                for section in page_data.get("sections", []):
                    section_name = section.get("name", "")
                    normalized = _normalize_section_name(section_name)

                    if normalized in normalized_to_actual:
                        actual_name = normalized_to_actual[normalized]
                        for s in statement_data.get("sections", []):
                            if s["name"] == actual_name:
                                existing_rows = {_row_key(r) for r in s.get("rows", [])}
                                for row in section.get("rows", []):
                                    if _row_key(row) not in existing_rows:
                                        s["rows"].append(row)
                                        existing_rows.add(_row_key(row))
                    else:
                        statement_data["sections"].append(section)
                        existing_section_names.add(section_name)
                        normalized_to_actual[normalized] = section_name

                # Merge periods if new ones appeared
                for p in page_data.get("periods", []):
                    if p not in statement_data.get("periods", []):
                        statement_data["periods"].append(p)

        if statement_data:
            logging.info(f"Extraction complete: {len(statement_data.get('sections', []))} sections")
            print(f"  ✅ {statement_type.value.replace('_', ' ').title()}: {len(statement_data.get('sections', []))} sections")
        else:
            logging.warning(f"No data extracted for {statement_type.value}")
            print(f"  ⚠️  No data extracted for {statement_type.value}")

        return (statement_type, statement_data)

    # Extract statement types in parallel (on retry, only failed types)
    # Cap workers to 3 to prevent overwhelming Ollama
    with ThreadPoolExecutor(max_workers=min(len(types_to_extract), 3)) as executor:
        futures = {executor.submit(extract_statement_type, st): st for st in types_to_extract}

        for future in as_completed(futures):
            statement_type, data = future.result(timeout=300)
            if data:
                all_data[statement_type] = data

    # OCR fallback: for scanned PDFs where pdfplumber text is empty,
    # run OCR on the cached rasterized images to build ground-truth text
    # for the hallucination guardrail in the evaluator.
    for st, texts in list(page_texts.items()):
        pages = statement_pages.get(st, [])
        updated_texts = []
        for idx, text in enumerate(texts):
            if len(text.strip()) < 50 and idx < len(pages):
                page_num = pages[idx]
                # pdftoppm names files like extract_{type}_p0001-1.jpg
                # Match the prefix pattern used in extract_single_page
                ext_prefix = os.path.join(cache_dir, f"extract_{st.value}_p{page_num:04d}")
                from pathlib import Path
                matches = sorted(Path(cache_dir).glob(f"extract_{st.value}_p{page_num:04d}-*.jpg"))
                if not matches:
                    # Fallback: look for any jpg in cache dir containing page number
                    matches = sorted(Path(cache_dir).glob(f"*p{page_num:04d}*.jpg"))
                if matches:
                    cached_img = str(matches[-1])
                    ocr_text = _ocr_image(cached_img)
                    if len(ocr_text.strip()) >= 50:
                        logging.info(f"  OCR fallback for page {page_num}: {len(ocr_text)} chars")
                        updated_texts.append(ocr_text)
                        continue
            updated_texts.append(text)
        page_texts[st] = updated_texts

    # Persist OCR text for manual inspection
    _save_ocr_texts(pdf_path, statement_pages, page_texts)

    if all_data:
        # Log node timing
        duration_ms = (time.time() - start_time) * 1000
        obs.log_node_timing("extractor", duration_ms, run_id)

        return {
            "extracted_data": all_data,
            "retry_count": new_retry_count,
            "run_id": run_id,
            "page_texts": page_texts,
        }
    else:
        logging.error("Extraction returned no data")
        print("❌ Extraction returned no data.")
        # IMPORTANT: always return extracted_data (even if empty) so LangGraph
        # overwrites any stale data from a previous attempt.
        return {
            "extracted_data": {},
            "error_message": "Extraction returned no data",
            "retry_count": new_retry_count,
            "run_id": run_id,
            "page_texts": page_texts,
        }
