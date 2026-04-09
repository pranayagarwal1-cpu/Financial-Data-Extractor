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
        retry_count: Number of extraction attempts
        output_files: List of output file paths
        error_message: Error message if any step fails
        log_file: Path to the log file for this run
        run_id: Unique identifier for this extraction run (for observability)
    """
    input_pdf: str
    statement_types: List[StatementType]
    statement_pages: Dict[StatementType, List[int]]
    extracted_data: Dict[StatementType, dict]
    evaluation_result: Dict[StatementType, dict]
    retry_count: int
    output_files: List[str]
    error_message: Optional[str]
    log_file: Optional[str]
    run_id: Optional[str]
