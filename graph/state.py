from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from utils.vlm_utils import StatementType
from models.schemas import StatementData


class AgentState(BaseModel):
    """
    State for the multi-statement financial extraction workflow.

    Uses Pydantic BaseModel instead of TypedDict so LangGraph's
    ``get_type_hints`` introspection does not depend on the
    ``graph.state`` module remaining in ``sys.modules`` —
    a requirement that Streamlit's script re-loader breaks
    between re-runs.
    """

    input_pdf: str = ""
    statement_types: List[StatementType] = Field(default_factory=list)
    statement_pages: Dict[StatementType, List[int]] = Field(default_factory=dict)
    extracted_data: Dict[StatementType, StatementData] = Field(default_factory=dict)
    evaluation_result: Dict[StatementType, dict] = Field(default_factory=dict)
    categorized_data: Dict[StatementType, StatementData] = Field(default_factory=dict)
    categorization_summary: Dict[str, Any] = Field(default_factory=dict)
    review_queue: List[Dict[str, Any]] = Field(default_factory=list)
    retry_count: int = 0
    cat_retry_count: int = 0
    cat_evaluation_result: Dict[str, Any] = Field(default_factory=dict)
    cat_metrics: Dict[str, Any] = Field(default_factory=dict)
    output_files: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    log_file: Optional[str] = None
    run_id: Optional[str] = None
    enable_categorization: bool = True
    last_evaluation_feedback: Dict[StatementType, str] = Field(default_factory=dict)
    guardrail_flags: List[str] = Field(default_factory=list)
    page_texts: Dict[StatementType, List[str]] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Dict-like interface so existing nodes (typed as ``state: dict``)
    # can keep using ``.get()`` and ``[key]`` without changes.
    # ------------------------------------------------------------------

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)
