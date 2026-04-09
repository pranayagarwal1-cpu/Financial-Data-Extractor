#!/usr/bin/env python3
"""
Multi-Agent Financial Statement Extraction System

Folder Structure:
    input/   - Place PDF files here for processing
    output/  - Extracted JSON and Excel files saved here
    tmp/     - Temporary files (auto-cleaned)

Usage:
    Single PDF:  python main.py --pdf input/report.pdf
    Batch mode:  python main.py --folder input/
    All in input: python main.py

Statement Types:
    - balance_sheet: Balance Sheet / Statement of Financial Position
    - income_statement: Income Statement / Statement of Earnings
    - cash_flow: Cash Flow Statement
"""

import argparse
import os
import sys
from pathlib import Path

from graph.workflow import create_workflow
from utils.vlm_utils import StatementType

# Base directories
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"


def ensure_directories():
    """Ensure input, output, and tmp directories exist."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def parse_statement_types(statement_str: str) -> list:
    """
    Parse comma-separated statement types string into list of StatementType.

    Args:
        statement_str: Comma-separated string like "balance_sheet,income_statement"

    Returns:
        List of StatementType enums
    """
    if not statement_str:
        return list(StatementType)  # Default to all

    statement_map = {
        "balance_sheet": StatementType.BALANCE_SHEET,
        "income_statement": StatementType.INCOME_STATEMENT,
        "cash_flow": StatementType.CASH_FLOW,
        "all": list(StatementType)
    }

    types = []
    for s in statement_str.lower().split(","):
        s = s.strip()
        if s in statement_map:
            val = statement_map[s]
            if isinstance(val, list):
                types.extend(val)
            else:
                types.append(val)

    return types if types else list(StatementType)


def process_single_pdf(pdf_path: str, statement_types: list = None) -> dict:
    """
    Process a single PDF file through the multi-agent workflow.

    Args:
        pdf_path: Path to the PDF file
        statement_types: Optional list of StatementType to extract

    Returns:
        Final state from the workflow
    """
    from utils.observability import get_observability
    obs = get_observability()

    if not os.path.exists(pdf_path):
        print(f"❌ File not found: {pdf_path}")
        return {"error_message": "File not found"}

    # Create and run workflow with specified statement types
    workflow = create_workflow(statement_types)

    initial_state = {
        "input_pdf": pdf_path,
        "statement_types": statement_types or list(StatementType),
        "retry_count": 0
    }

    try:
        final_state = workflow.invoke(initial_state)
        # Ensure run is ended on error
        if final_state.get("error_message") and not final_state.get("output_files"):
            run_id = final_state.get("run_id")
            if run_id:
                obs.end_run(run_id=run_id, success=False, error_message=final_state["error_message"])
        return final_state
    except Exception as e:
        print(f"❌ Workflow error: {e}")
        # End run on exception
        obs.end_run(run_id=initial_state.get("run_id", ""), success=False, error_message=str(e))
        return {"error_message": str(e)}


def process_folder(folder_path: str, statement_types: list = None, pattern: str = "*.pdf") -> dict:
    """
    Process all PDFs in a folder.

    Args:
        folder_path: Path to folder containing PDFs
        statement_types: Optional list of StatementType to extract
        pattern: Glob pattern for matching files

    Returns:
        Dict with summary of processed files
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        print(f"❌ Not a directory: {folder_path}")
        return {"error_message": "Not a directory"}

    pdf_files = list(folder.glob(pattern))
    # Exclude already generated statement files
    pdf_files = [f for f in pdf_files if
                 not any(stmt.value in f.stem for stmt in StatementType)]

    if not pdf_files:
        print(f"❌ No PDF files found in: {folder_path}")
        return {"error_message": "No PDF files found"}

    statements_desc = ", ".join([st.value for st in statement_types]) if statement_types else "all"
    print(f"📁 Found {len(pdf_files)} PDF(s) to process")
    print(f"📊 Extracting: {statements_desc}\n")
    print("=" * 60)

    results = {
        "processed": [],
        "failed": [],
        "total": len(pdf_files)
    }

    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] Processing: {pdf_file.name}")
        print("-" * 40)

        final_state = process_single_pdf(str(pdf_file), statement_types)

        if final_state.get("output_files"):
            results["processed"].append({
                "file": str(pdf_file),
                "output_files": final_state["output_files"]
            })
            print(f"✅ Completed: {pdf_file.name}")
        else:
            results["failed"].append({
                "file": str(pdf_file),
                "error": final_state.get("error_message", "Unknown error")
            })
            print(f"❌ Failed: {pdf_file.name}")

    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"Total files: {results['total']}")
    print(f"Successful:  {len(results['processed'])}")
    print(f"Failed:      {len(results['failed'])}")

    if results["failed"]:
        print("\nFailed files:")
        for item in results["failed"]:
            print(f"  - {item['file']}: {item['error']}")

    return results


def clean_tmp():
    """Clean up temporary files."""
    import shutil
    if TMP_DIR.exists():
        for item in TMP_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    print("🧹 Temp files cleaned")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Financial Statement Extraction System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Folder Structure:
  input/   - Place PDF files here
  output/  - Results saved here
  tmp/     - Temporary files

Statement Types:
  balance_sheet    - Balance Sheet / Statement of Financial Position
  income_statement - Income Statement / Statement of Earnings
  cash_flow        - Cash Flow Statement
  all              - Extract all three (default)

Examples:
  python main.py                                              # Process all PDFs in input/
  python main.py --pdf input/file.pdf                         # Process single file
  python main.py --folder input/                              # Process folder
  python main.py --pdf input/file.pdf --statements all        # Extract all statements
  python main.py --pdf input/file.pdf --statements balance_sheet  # Extract only BS
  python main.py --clean                                      # Clean temp files
  python main.py --model qwen3.5:9b                           # Use specific model
        """
    )

    parser.add_argument(
        "--pdf",
        type=str,
        help="Path to a single PDF file to process"
    )
    parser.add_argument(
        "--folder",
        type=str,
        help="Path to folder containing PDFs (default: input/)"
    )
    parser.add_argument(
        "--statements",
        type=str,
        default="all",
        help="Comma-separated statement types: balance_sheet,income_statement,cash_flow (default: all)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Ollama model to use for all tasks (overrides config)"
    )
    parser.add_argument(
        "--extraction-model",
        type=str,
        default=None,
        help="Model for page detection and data extraction"
    )
    parser.add_argument(
        "--eval-model",
        type=str,
        default=None,
        help="Model for evaluation (LLM-as-Judge)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean up temporary files"
    )

    args = parser.parse_args()

    # Ensure directories exist
    ensure_directories()

    # Clean tmp if requested
    if args.clean:
        clean_tmp()
        return

    # Parse statement types
    statement_types = parse_statement_types(args.statements)
    statement_names = [st.value for st in statement_types]
    print(f"📊 Extracting: {', '.join(statement_names)}")
    print()

    # Override models if specified
    if args.model:
        # Single model for all tasks
        os.environ["DEFAULT_MODEL"] = args.model
    if args.extraction_model:
        os.environ["EXTRACTION_MODEL"] = args.extraction_model
    if args.eval_model:
        os.environ["EVALUATION_MODEL"] = args.eval_model

    print("🤖 Multi-Agent Financial Statement Extractor")
    print("=" * 60)
    print(f"📂 Input:  {INPUT_DIR}")
    print(f"📂 Output: {OUTPUT_DIR}")
    print(f"📂 Temp:   {TMP_DIR}")
    print()

    # Determine what to process
    if args.pdf:
        final_state = process_single_pdf(args.pdf, statement_types)
        if final_state.get("error_message"):
            sys.exit(1)
    elif args.folder:
        results = process_folder(args.folder, statement_types)
        if results.get("error_message") or results["failed"]:
            sys.exit(1)
    else:
        # Default: process all PDFs in input/
        if not INPUT_DIR.exists():
            print(f"❌ Input directory not found: {INPUT_DIR}")
            sys.exit(1)

        results = process_folder(str(INPUT_DIR), statement_types)
        if results.get("error_message") or results["failed"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
