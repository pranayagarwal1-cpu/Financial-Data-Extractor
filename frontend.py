"""
Streamlit Frontend for Multi-Agent Financial Statement Extractor

Run with: streamlit run frontend.py

UI is split into components under ui/ — see ui/sidebar.py, ui/results.py,
ui/metrics_dashboard.py. Session state defaults live in ui/session.py.
"""

import sys
import warnings
from pathlib import Path

# Suppress LangChainPendingDeprecationWarning from langgraph internals
# (triggered by JsonPlusSerializer import before allowed_objects default changes)
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="langgraph.checkpoint.serde.jsonplus",
)

# Ensure project root is on path before any project imports
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force-load graph.state so typing.get_type_hints() can resolve it later
# when LangGraph introspects AgentState inside StateGraph().
import graph.state  # noqa: F401

import streamlit as st

from graph.workflow import create_workflow
from ui import INPUT_DIR, OUTPUT_DIR, TMP_DIR
from ui.session import init_session_state
from ui.sidebar import render_sidebar
from ui.results import render_results
from ui.metrics_dashboard import render_metrics_dashboard


# Ensure directories exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)


def process_pdf(pdf_path: str, statement_types: list, log_callback=None, enable_categorization: bool = True):
    """Process a PDF through the workflow and return final state."""
    from utils.observability import get_observability
    obs = get_observability()

    if log_callback:
        log_callback("🔍 Analyzing PDF structure...")

    workflow = create_workflow(statement_types)
    # Start the run up front so the run_id is always known to error handlers,
    # even if a node raises before orchestrator can call obs.start_run().
    run_id = obs.start_run(pdf_path, statement_types)
    initial_state = {
        "input_pdf": pdf_path,
        "statement_types": statement_types,
        "retry_count": 0,
        "enable_categorization": enable_categorization,
        "run_id": run_id,
    }

    if log_callback:
        log_callback("📄 Detecting financial statements...")

    try:
        final_state = workflow.invoke(initial_state)
        if final_state.get("error_message") and not final_state.get("output_files"):
            obs.end_run(run_id=run_id, success=False, error_message=final_state["error_message"])
        if log_callback:
            log_callback("✅ Extraction complete!")
        return final_state
    except Exception as e:
        obs.end_run(run_id=run_id, success=False, error_message=str(e))
        if log_callback:
            log_callback(f"❌ Error: {str(e)}")
        return {"error_message": str(e), "run_id": run_id}


# -----------------------------------------------------------------------------
# Page config + state
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Financial Statement Extractor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_session_state()

# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
render_sidebar()

# -----------------------------------------------------------------------------
# Main Content
# -----------------------------------------------------------------------------
st.title("📊 Financial Statement Extractor")

st.markdown(
    """
    <div style="font-size: 18px; color: #555; margin-bottom: 30px; line-height: 1.6;">
    For <strong>financial analysts</strong> and <strong>investment teams</strong>:
    Extract Balance Sheets, Income Statements & Cash Flow from PDFs in seconds.
    <strong>Reduce manual data entry by 90%</strong> — from 30+ minutes to under 1 minute per report.
    Get structured Excel & JSON files ready for your financial models and data warehouse.
    </div>
    """,
    unsafe_allow_html=True,
)

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("📄 PDFs Uploaded", st.session_state["pdfs_uploaded"])
with col2:
    st.metric("✅ Extracted", st.session_state["extracted_count"])
with col3:
    st.metric("📥 Excel", st.session_state["excel_count"])
with col4:
    st.metric("📄 JSON", st.session_state["json_count"])

# -----------------------------------------------------------------------------
# Process Button
# -----------------------------------------------------------------------------
selected_statements = st.session_state.get("selected_statements", [])
uploaded_pdfs = st.session_state.get("uploaded_pdfs", [])

if uploaded_pdfs:
    from ui.sidebar import STATEMENT_OPTIONS

    st.divider()
    pdf_count = len(uploaded_pdfs)
    if selected_statements:
        st.caption(f"📊 Extracting: {', '.join([STATEMENT_OPTIONS[s] for s in selected_statements])}")
        st.caption(f"📁 Files to process: {pdf_count}")

    if st.button("🚀 Extract Statements", type="primary", width="stretch"):
        if not selected_statements:
            st.warning("Please select at least one statement type to extract.")
        else:
            log_messages = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            all_results = []

            for idx, pdf_path in enumerate(uploaded_pdfs):
                pdf_name = Path(pdf_path).stem
                status_text.text(f"⏳ Processing {idx + 1}/{pdf_count}: {pdf_name}...")
                log_messages.append(f"🔍 Analyzing {pdf_name}...")
                log_messages.append(f"📄 Detecting financial statements in {pdf_name}...")
                log_messages.append(f"🤖 Extracting data from {pdf_name}...")

                final_state = process_pdf(
                    pdf_path,
                    selected_statements,
                    log_callback=log_messages.append,
                    enable_categorization=st.session_state.get("enable_categorization", True),
                )

                log_messages.append(f"⚖️ Validating extraction quality for {pdf_name}...")
                log_messages.append(f"📥 Generating Excel and JSON files for {pdf_name}...")

                all_results.append({
                    "pdf_name": pdf_name,
                    "pdf_path": pdf_path,
                    "final_state": final_state,
                })
                progress_bar.progress((idx + 1) / pdf_count)

            status_text.text("✅ All files processed!")
            st.session_state["all_results"] = all_results
            st.session_state["processing_complete"] = True
            st.session_state["log_messages"] = log_messages
            st.session_state["extraction_counted"] = False
            st.rerun()

# -----------------------------------------------------------------------------
# Results + Metrics
# -----------------------------------------------------------------------------
render_results()
render_metrics_dashboard()
