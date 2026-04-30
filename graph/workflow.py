"""
LangGraph Workflow - Defines the multi-statement financial extraction pipeline.

Architecture:
    Orchestrator → Extractor → Evaluator → [Retry?] → Extractor
                                              ↓ (pass)
                                        Save Outputs

Supports:
- Balance Sheet (Statement of Financial Position)
- Income Statement (Statement of Earnings)
- Cash Flow Statement
"""

from langgraph.graph import StateGraph, END

from graph.state import AgentState
from agents.orchestrator import orchestrator_node, should_retry, should_retry_categorization, save_outputs
from agents.extractor import extractor_node
from agents.evaluator import evaluator_node
from agents.categorizer import categorizer_node
from agents.cat_evaluator import cat_evaluator_node


def check_detection_result(state: dict) -> str:
    """
    Check if any financial statement pages were found.

    Returns:
        'extractor' if pages found, 'save_outputs' if not (to handle error)
    """
    statement_pages = state.get("statement_pages", {})
    error_msg = state.get("error_message")

    if error_msg or not statement_pages:
        return "end"

    # Check if at least one statement has pages
    total_pages = sum(len(pages) for pages in statement_pages.values())
    if total_pages == 0:
        return "end"

    return "extractor"


def create_workflow(statement_types: list = None) -> StateGraph:
    """
    Create and configure the financial statement extraction workflow.

    Args:
        statement_types: Optional list of StatementType to extract.
                        If None, extracts all three statement types.

    Returns:
        Compiled StateGraph ready for execution
    """
    from utils.vlm_utils import StatementType

    if statement_types is None:
        statement_types = [
            StatementType.BALANCE_SHEET,
            StatementType.INCOME_STATEMENT,
            StatementType.CASH_FLOW
        ]

    # Initialize the graph with state
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("extractor", extractor_node)
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("categorizer", categorizer_node)
    workflow.add_node("cat_evaluator", cat_evaluator_node)
    workflow.add_node("save_outputs", save_outputs)

    # Set entry point
    workflow.set_entry_point("orchestrator")

    # Conditional edge after orchestrator: check if pages were found
    workflow.add_conditional_edges(
        "orchestrator",
        check_detection_result,
        {
            "extractor": "extractor",
            "end": "save_outputs"
        }
    )

    workflow.add_edge("extractor", "evaluator")

    # Conditional edge: retry or save
    workflow.add_conditional_edges(
        "evaluator",
        should_retry,
        {
            "extractor": "extractor",
            "categorizer": "categorizer"  # Pass to categorizer if evaluation passes
        }
    )

    # After categorizer, evaluate categorization quality
    workflow.add_edge("categorizer", "cat_evaluator")

    # Conditional edge: retry categorization or save
    workflow.add_conditional_edges(
        "cat_evaluator",
        should_retry_categorization,
        {
            "categorizer": "categorizer",
            "save_outputs": "save_outputs",
        }
    )

    # Final edge to END
    workflow.add_edge("save_outputs", END)

    # Compile the workflow
    # Note: Callbacks are handled manually via direct instrumentation
    # since we use raw ollama.chat calls (not LangChain)
    app = workflow.compile()

    return app
