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
import time
from pathlib import Path
from typing import Optional, List, Dict

from utils.pdf_utils import rasterize_page
from utils.vlm_utils import vlm_extract_statement, StatementType
from config import Config

# Base directory
BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"


def get_temp_dir(pdf_path: str) -> str:
    """Get a unique temp directory for a PDF file."""
    pdf_name = Path(pdf_path).stem
    temp_dir = TMP_DIR / f"extract_{pdf_name}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir)


def extractor_node(state: dict) -> dict:
    """
    Extract financial statement data from identified pages.

    Args:
        state: Current workflow state with:
            - statement_pages: Dict[StatementType, List[int]]
            - statement_types: List[StatementType] to extract

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

    # Get temp directory for this extraction
    tmp_dir = get_temp_dir(pdf_path)

    logging.info(f"Extraction attempt {new_retry_count}/{Config.MAX_RETRIES + 1}")
    print(f"🔄 Extraction attempt {new_retry_count}/{Config.MAX_RETRIES + 1}")
    print(f"📂 Temp directory: {tmp_dir}")
    print("Extracting data with VLM…\n")

    all_data: Dict[StatementType, dict] = {}

    for statement_type in statement_types:
        pages = statement_pages.get(statement_type, [])
        if not pages:
            logging.info(f"No pages found for {statement_type.value}")
            continue

        logging.info(f"Extracting {statement_type.value} from pages {pages}")
        print(f"\n📊 Extracting {statement_type.value.replace('_', ' ').title()}…")

        statement_data: Optional[dict] = None
        existing_section_names = set()

        for page_num in pages:
            ext_prefix = os.path.join(tmp_dir, f"extract_{statement_type.value}_p{page_num:04d}")
            img_path = rasterize_page(pdf_path, page_num, ext_prefix, dpi=Config.EXTRACT_DPI)
            logging.info(f"  Extracting page {page_num}")
            print(f"  Extracting page {page_num}…")

            try:
                page_data = vlm_extract_statement(img_path, statement_type, Config.EXTRACTION_MODEL)
                logging.info(f"  Page {page_num} extracted successfully")
                os.remove(img_path)
            except Exception as e:
                logging.error(f"  Error extracting page {page_num}: {e}")
                print(f"  ⚠️  Error extracting page {page_num}: {e}")
                os.remove(img_path)
                continue

            if statement_data is None:
                statement_data = page_data
                existing_section_names = {s["name"] for s in statement_data.get("sections", [])}
            else:
                # Merge continuation pages
                for section in page_data.get("sections", []):
                    section_name = section.get("name", "")
                    if section_name in existing_section_names:
                        # Append rows to existing section
                        for s in statement_data.get("sections", []):
                            if s["name"] == section_name:
                                s["rows"].extend(section.get("rows", []))
                    else:
                        statement_data["sections"].append(section)
                        existing_section_names.add(section_name)

                # Merge periods if new ones appeared
                for p in page_data.get("periods", []):
                    if p not in statement_data.get("periods", []):
                        statement_data["periods"].append(p)

        if statement_data:
            logging.info(f"Extraction complete: {len(statement_data.get('sections', []))} sections")
            print(f"  ✅ {statement_type.value.replace('_', ' ').title()}: {len(statement_data.get('sections', []))} sections")
            all_data[statement_type] = statement_data
        else:
            logging.warning(f"No data extracted for {statement_type.value}")
            print(f"  ⚠️  No data extracted for {statement_type.value}")

    if all_data:
        # Log node timing
        duration_ms = (time.time() - start_time) * 1000
        obs.log_node_timing("extractor", duration_ms, run_id)

        return {
            "extracted_data": all_data,
            "retry_count": new_retry_count,
            "run_id": run_id
        }
    else:
        logging.error("Extraction returned no data")
        print("❌ Extraction returned no data.")
        return {
            "error_message": "Extraction returned no data",
            "retry_count": new_retry_count,
            "run_id": run_id
        }
