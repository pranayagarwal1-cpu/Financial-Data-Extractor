from typing import TypedDict, Optional, List, Dict, Any
from utils.vlm_utils import StatementType


class AgentState(TypedDict, total=False):
    """
    State for the multi-statement financial extraction workflow.

    Attributes:
        input_pdf: Path to the input PDF file
        statement_types: List of statement types to extract
        statement_pages: Dict mapping StatementType to list of page numbers
        extracted_data: Dict mapping StatementType to extracted data
        evaluation_result: Dict mapping StatementType to evaluation results
        categorized_data: Dict mapping StatementType to categorized data (with CoA mappings)
        categorization_summary: Summary statistics for categorization
        review_queue: List of line items needing human review
        retry_count: Number of extraction attempts
        cat_retry_count: Number of categorization attempts
        cat_evaluation_result: Categorization evaluation scores per statement type
        output_files: List of output file paths
        error_message: Error message if any step fails
        log_file: Path to the log file for this run
        run_id: Unique identifier for this extraction run (for observability)
        enable_categorization: Whether to run CoA categorization after extraction
    """
    input_pdf: str
    statement_types: List[StatementType]
    statement_pages: Dict[StatementType, List[int]]
    extracted_data: Dict[StatementType, dict]
    evaluation_result: Dict[StatementType, dict]
    categorized_data: Dict[StatementType, dict]
    categorization_summary: Dict[str, Any]
    review_queue: List[Dict[str, Any]]
    retry_count: int
    cat_retry_count: int
    cat_evaluation_result: Dict[str, Any]
    output_files: List[str]
    error_message: Optional[str]
    log_file: Optional[str]
    run_id: Optional[str]
    enable_categorization: bool
