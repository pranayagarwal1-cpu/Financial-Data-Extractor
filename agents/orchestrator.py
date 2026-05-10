"""
Orchestrator Agent - Coordinates the multi-statement financial extraction workflow.

Responsibilities:
- Initialize extraction state
- Detect financial statement pages using LLM-based text analysis
- Trigger extraction agent for all statement types
- Coordinate retry loop based on evaluator feedback
- Trigger output saving when extraction passes evaluation
"""

import os
import logging
import time
from typing import Any, Dict, List
from pathlib import Path

from utils.pdf_utils import get_page_count, rasterize_page
from utils.llm_detector import find_statement_pages_llm
from utils.vlm_utils import StatementType
from utils.guardrails import get_guardrails
from utils.exceptions import InputValidationError
from config import Config


# Base directories
BASE_DIR = Path(__file__).parent.parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"


def get_temp_dir(pdf_path: str) -> str:
    """Get a unique temp directory for a PDF file."""
    pdf_name = Path(pdf_path).stem
    temp_dir = TMP_DIR / f"extract_{pdf_name}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir)


def setup_logging(pdf_path: str) -> Path:
    """Set up logging for this extraction run."""
    from datetime import datetime

    pdf_name = Path(pdf_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = TMP_DIR / f"extract_{pdf_name}" / f"log_{timestamp}.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Remove previous FileHandlers so each PDF gets its own log file
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            logger.removeHandler(h)

    # Add file handler for this PDF
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    # Ensure a single stream handler exists (console output)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(stream_handler)

    return log_file


def orchestrator_node(state: dict) -> dict:
    """
    Orchestrator node that detects financial statement pages using LLM text analysis.

    Detects all requested statement types in a single LLM call:
    - Balance Sheet
    - Income Statement
    - Cash Flow Statement

    Args:
        state: Current workflow state with:
            - input_pdf: Path to PDF
            - statement_types: List of StatementType to detect

    Returns:
        Updated state with statement_pages dict
    """
    from utils.observability import get_observability
    obs = get_observability()

    pdf_path = state.get("input_pdf")
    statement_types = state.get("statement_types", [StatementType.BALANCE_SHEET])

    if not pdf_path:
        return {"error_message": "No input PDF path provided"}

    # Input validation — fail fast before any LLM calls or temp directory creation
    if not Config.DISABLE_GUARDRAILS:
        gr = get_guardrails()
        try:
            gr.input.validate(pdf_path)
        except InputValidationError as e:
            logging.error(f"Input validation failed: {e}")
            print(f"❌ Input validation failed: {e}")
            return {
                "error_message": str(e),
                "statement_pages": {},
                "guardrail_flags": ["input_validation_failed"]
            }

    # Start observability run if not already started
    run_id = state.get("run_id")
    if not run_id:
        run_id = obs.start_run(pdf_path, statement_types)

    start_time = time.time()

    # Set up logging
    log_file = setup_logging(pdf_path)
    logging.info(f"Starting extraction for: {pdf_path}")
    logging.info(f"Statement types to detect: {[st.value for st in statement_types]}")

    print(f"📄 Processing: {pdf_path}\n")
    print(f"📝 Log file: {log_file}\n")
    print(f"📊 Detecting: {[st.value.replace('_', ' ').title() for st in statement_types]}")

    # Create temp directory
    temp_dir = get_temp_dir(pdf_path)
    logging.info(f"Temp directory: {temp_dir}")

    # LLM-based detection - all statements in ONE call
    statement_pages = find_statement_pages_llm(pdf_path, statement_types, Config.EXTRACTION_MODEL, run_id)

    # Log node timing
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("orchestrator", duration_ms, run_id)

    # Log results
    for st, pages in statement_pages.items():
        if pages:
            logging.info(f"{st.value}: pages {pages}")
            print(f"  ✅ {st.value.replace('_', ' ').title()}: pages {pages}")
        else:
            logging.info(f"{st.value}: not found")
            print(f"  – {st.value.replace('_', ' ').title()}: not found")

    # Check if at least one statement was found
    total_pages_found = sum(len(pages) for pages in statement_pages.values())
    if total_pages_found == 0:
        logging.warning("No financial statements found")
        print("\n❌ No financial statements found in the PDF.")
        return {
            "error_message": "No financial statements found",
            "statement_pages": {},
            "log_file": str(log_file),
            "run_id": run_id
        }

    return {
        "statement_pages": statement_pages,
        "retry_count": 0,
        "log_file": str(log_file),
        "run_id": run_id
    }


def should_retry(state: dict) -> str:
    """
    Conditional edge function to determine next step after evaluator.

    Returns:
        'extractor' to retry extraction,
        'categorizer' to proceed to categorization (if enabled),
        'save_outputs' to skip categorization and save directly,
        'end' to terminate without saving (degraded quality or total failure)
    """
    evaluation = state.get("evaluation_result", {})
    retry_count = state.get("retry_count", 0)
    enable_categorization = state.get("enable_categorization", True)
    extracted_data = state.get("extracted_data", {})

    # Check if ALL statements passed evaluation
    all_passed = all(
        eval_result.get("passed", False)
        for eval_result in evaluation.values()
    ) if evaluation else False

    if all_passed:
        logging.info("All statements passed evaluation")
        print("✅ All statements passed evaluation!")
        if enable_categorization:
            return "categorizer"
        print("📦 Categorization skipped — saving extracted data directly.")
        return "save_outputs"

    # Quality degradation guardrail — do NOT save degraded output
    guardrail_flags = state.get("guardrail_flags", [])
    if not Config.DISABLE_GUARDRAILS and "quality_degraded" in guardrail_flags:
        logging.error("Quality degraded — terminating without saving output")
        print("❌ Quality degraded — output will NOT be saved")
        return "end"

    # If no data exists after extraction failure, do not retry
    if not extracted_data:
        logging.error("No extracted data available — terminating without saving")
        print("❌ No extracted data available — output will NOT be saved")
        return "end"

    if retry_count < Config.MAX_RETRIES:
        logging.warning(f"Extraction quality insufficient, retrying ({retry_count + 1}/{Config.MAX_RETRIES})")
        print(f"⚠️  Extraction quality insufficient. Retrying ({retry_count + 1}/{Config.MAX_RETRIES})…")
        return "extractor"

    logging.error("Max retries reached. Output will NOT be saved.")
    print("❌ Max retries reached. Output will NOT be saved.")
    return "end"


def should_retry_categorization(state: dict) -> str:
    """
    Conditional edge function to determine if re-categorization is needed.

    Returns:
        'categorizer' to retry, 'save_outputs' to proceed
    """
    cat_evaluation = state.get("cat_evaluation_result", {})
    cat_retry_count = state.get("cat_retry_count", 0)

    all_passed = all(
        eval_result.get("passed", False)
        for eval_result in cat_evaluation.values()
    ) if cat_evaluation else False

    if all_passed:
        logging.info("Categorization quality sufficient")
        print("✅ Categorization quality sufficient!")
        return "save_outputs"

    if cat_retry_count < Config.MAX_CAT_RETRIES:
        logging.warning(
            f"Categorization quality insufficient, retrying "
            f"({cat_retry_count + 1}/{Config.MAX_CAT_RETRIES})"
        )
        print(
            f"⚠️  Categorization quality insufficient. "
            f"Retrying ({cat_retry_count + 1}/{Config.MAX_CAT_RETRIES})..."
        )
        return "categorizer"

    logging.warning("Max cat retries reached, saving anyway")
    print("⚠️  Max categorization retries reached. Saving anyway.")
    return "save_outputs"


def save_outputs(state: dict) -> dict:
    """
    Save the final outputs (JSON and Excel) for all statement types.

    Uses categorized_data if available (with CoA mappings), otherwise falls back to extracted_data.

    Creates separate files for each statement type:
    - {pdf_name}_balance_sheet_{timestamp}.json/xlsx
    - {pdf_name}_income_statement_{timestamp}.json/xlsx
    - {pdf_name}_cash_flow_{timestamp}.json/xlsx
    """
    from utils.excel_writer import save_to_excel
    from utils.json_formatter import format_json_output
    from utils.observability import get_observability
    from datetime import datetime

    obs = get_observability()
    run_id = state.get("run_id")
    start_time = time.time()

    # Use categorized_data if available, otherwise use extracted_data
    data_to_save = state.get("categorized_data", {})
    if not data_to_save:
        data_to_save = state.get("extracted_data", {})

    guardrail_flags = state.get("guardrail_flags", [])

    if not data_to_save:
        error = state.get("error_message", "No data to save")
        logging.error(f"No data to save: {error}")
        return {
            "error_message": error,
            "guardrail_flags": guardrail_flags,
            "run_id": run_id
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    input_pdf = state.get("input_pdf", "")
    pdf_name = Path(input_pdf).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_files = []

    for statement_type, data in data_to_save.items():
        statement_name = statement_type.value

        # Save JSON
        json_path = str(OUTPUT_DIR / f"{pdf_name}_{statement_name}_{timestamp}.json")
        json_content = format_json_output(data)

        # Inject guardrail flags into JSON metadata if present
        if guardrail_flags:
            import json as _json
            parsed = _json.loads(json_content)
            if isinstance(parsed, dict):
                parsed["guardrail_flags"] = guardrail_flags
            json_content = _json.dumps(parsed, indent=2)

        with open(json_path, "w") as f:
            f.write(json_content)
        logging.info(f"JSON saved: {json_path}")
        print(f"💾 {statement_name.replace('_', ' ').title()} JSON: {json_path}")
        output_files.append(json_path)

        # Save Excel
        excel_path = str(OUTPUT_DIR / f"{pdf_name}_{statement_name}_{timestamp}.xlsx")
        save_to_excel(data, excel_path)
        logging.info(f"Excel saved: {excel_path}")
        print(f"💾 {statement_name.replace('_', ' ').title()} Excel: {excel_path}")
        output_files.append(excel_path)

    # Determine success based on guardrail flags
    failure_flags = {"quality_degraded", "hallucination_warning", "input_validation_failed"}
    has_failure_flag = bool(failure_flags.intersection(guardrail_flags))
    run_success = not has_failure_flag

    # Log node timing and end the run
    duration_ms = (time.time() - start_time) * 1000
    obs.log_node_timing("save_outputs", duration_ms, run_id)
    obs.end_run(
        run_id=run_id,
        success=run_success,
        retry_count=state.get("retry_count", 0),
        cat_retry_count=state.get("cat_retry_count", 0),
        review_queue_count=len(state.get("review_queue", [])),
    )

    return {
        "output_files": output_files,
        "run_id": run_id,
        "guardrail_flags": guardrail_flags
    }
