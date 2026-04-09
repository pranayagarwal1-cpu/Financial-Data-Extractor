"""
Streamlit Frontend for Multi-Agent Financial Statement Extractor

Run with: streamlit run frontend.py
"""

import streamlit as st
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
import pandas as pd

# Import workflow and statement types
from graph.workflow import create_workflow
from utils.vlm_utils import StatementType

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"

# Ensure directories exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def get_output_files_for_pdf(pdf_name: str):
    """Get all output files for a given PDF."""
    pattern = f"{pdf_name}_*"
    files = list(OUTPUT_DIR.glob(pattern))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def process_pdf(pdf_path: str, statement_types: list, log_callback=None):
    """Process a PDF through the workflow and return results with logs."""
    from utils.observability import get_observability
    obs = get_observability()

    # Log callback
    if log_callback:
        log_callback("🔍 Analyzing PDF structure...")

    workflow = create_workflow(statement_types)
    initial_state = {
        "input_pdf": pdf_path,
        "statement_types": statement_types,
        "retry_count": 0
    }

    if log_callback:
        log_callback("📄 Detecting financial statements...")

    try:
        final_state = workflow.invoke(initial_state)
        if final_state.get("error_message") and not final_state.get("output_files"):
            run_id = final_state.get("run_id")
            if run_id:
                obs.end_run(run_id=run_id, success=False, error_message=final_state["error_message"])
        if log_callback:
            log_callback("✅ Extraction complete!")
        return final_state
    except Exception as e:
        obs.end_run(run_id=initial_state.get("run_id", ""), success=False, error_message=str(e))
        if log_callback:
            log_callback(f"❌ Error: {str(e)}")
        return {"error_message": str(e)}


def load_excel(path: Path):
    """Load Excel file and return dict of sheets."""
    xl = pd.ExcelFile(path)
    return {sheet: xl.parse(sheet) for sheet in xl.sheet_names}


# -----------------------------------------------------------------------------
# Page Config
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Financial Statement Extractor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
with st.sidebar:
    # Contact Button at top of sidebar
    st.markdown(
        """
        <style>
        .sidebar-contact a {
            background-color: #001f3f;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            display: block;
            text-align: center;
            margin-bottom: 20px;
        }
        .sidebar-contact a:hover {
            background-color: #003366;
        }
        </style>
        <div class="sidebar-contact">
            <a href="mailto:data.analytics.product@gmail.com?subject=Interested in Customizing Financial Statement Extractor&body=Hi, I found the Financial Statement Extractor valuable and I'm interested in customizing it for my use case.">
                Contact
            </a>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Upload section
    st.header("📁 Upload PDF")

    # Center align upload button and caption
    st.markdown(
        "<style>"
        "div[data-testid='stFileUploader'] {text-align: center;}"
        "div[data-testid='stFileUploader'] > div {margin: 0 auto;}"
        "</style>",
        unsafe_allow_html=True
    )

    uploaded_file = st.file_uploader(
        "Choose a PDF",
        type=["pdf"],
        help="Drag and drop or click to browse"
    )

    if uploaded_file:
        pdf_path = INPUT_DIR / uploaded_file.name
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        st.success(f"✅ {uploaded_file.name}")
        st.session_state["uploaded_pdf"] = str(pdf_path)
        if "last_uploaded_file" not in st.session_state or st.session_state.get("last_uploaded_file") != uploaded_file.name:
            st.session_state["pdfs_uploaded"] = st.session_state.get("pdfs_uploaded", 0) + 1
            st.session_state["last_uploaded_file"] = uploaded_file.name

    st.divider()

    # Statement type selection
    st.header("📊 Statements to Extract")
    statement_options = {
        StatementType.BALANCE_SHEET: "Balance Sheet",
        StatementType.INCOME_STATEMENT: "Income Statement",
        StatementType.CASH_FLOW: "Cash Flow Statement"
    }

    selected_statements = st.multiselect(
        "Select statements:",
        options=list(statement_options.keys()),
        default=[StatementType.BALANCE_SHEET],
        format_func=lambda x: statement_options[x]
    )

    st.session_state["selected_statements"] = selected_statements

    st.divider()

    # Clean all files button
    if st.button("🗑️ Clean All Files", use_container_width=True):
        files_deleted = 0

        def clean_directory(directory: Path) -> int:
            count = 0
            if not directory.exists():
                return 0
            for item in directory.rglob("*"):
                if item.is_file():
                    item.unlink()
                    count += 1
            for item in sorted(directory.rglob("*"), key=lambda p: len(str(p)), reverse=True):
                if item.is_dir():
                    item.rmdir()
            return count

        files_deleted += clean_directory(INPUT_DIR)
        files_deleted += clean_directory(OUTPUT_DIR)
        files_deleted += clean_directory(TMP_DIR)

        st.success(f"✅ {files_deleted} files deleted!")
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.markdown("""
    <div style="text-align: center; color: #28a745; font-weight: bold; margin-top: 15px;">
        🔒 Zero Data Retention
    </div>
    <p style="text-align: center; font-size: 0.85em; color: #666;">
        All files stored locally. Clean anytime.
    </p>
    """, unsafe_allow_html=True)

    st.divider()

    # Metrics Dashboard Link
    st.header("📈 Analytics")
    if st.button("📊 View Metrics Dashboard", use_container_width=True):
        st.session_state["show_metrics"] = not st.session_state.get("show_metrics", False)

# -----------------------------------------------------------------------------
# Main Content
# -----------------------------------------------------------------------------
st.title("📊 Financial Statement Extractor")

# Value Proposition
st.markdown(
    """
    <div style="font-size: 18px; color: #555; margin-bottom: 30px; line-height: 1.6;">
    For <strong>financial analysts</strong> and <strong>investment teams</strong>:
    Extract Balance Sheets, Income Statements & Cash Flow from PDFs in seconds.
    <strong>Reduce manual data entry by 90%</strong> — from 30+ minutes to under 1 minute per report.
    Get structured Excel & JSON files ready for your financial models and data warehouse.
    </div>
    """,
    unsafe_allow_html=True
)

# Initialize session counters
if "pdfs_uploaded" not in st.session_state:
    st.session_state["pdfs_uploaded"] = 0
if "extracted_count" not in st.session_state:
    st.session_state["extracted_count"] = 0
if "excel_count" not in st.session_state:
    st.session_state["excel_count"] = 0
if "json_count" not in st.session_state:
    st.session_state["json_count"] = 0
if "extraction_counted" not in st.session_state:
    st.session_state["extraction_counted"] = False

# Track successful extractions
if st.session_state.get("processing_complete") and not st.session_state.get("extraction_counted"):
    final_state = st.session_state.get("final_state", {})
    if final_state.get("output_files"):
        output_files = final_state["output_files"]
        excel_files = [f for f in output_files if f.endswith(".xlsx")]
        json_files = [f for f in output_files if f.endswith(".json")]
        st.session_state["extracted_count"] += len(excel_files) // max(1, len(st.session_state.get("selected_statements", [1])))
        st.session_state["excel_count"] += len(excel_files)
        st.session_state["json_count"] += len(json_files)
        st.session_state["extraction_counted"] = True
    elif final_state.get("error_message"):
        st.session_state["extraction_counted"] = True

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
if "uploaded_pdf" in st.session_state:
    st.divider()

    if selected_statements:
        st.caption(f"📊 Extracting: {', '.join([statement_options[s] for s in selected_statements])}")

    process_btn = st.button("🚀 Extract Statements", type="primary", use_container_width=True)

    if process_btn:
        if not selected_statements:
            st.warning("Please select at least one statement type to extract.")
        else:
            pdf_path = st.session_state["uploaded_pdf"]
            pdf_name = Path(pdf_path).stem

            st.divider()

            # Log messages
            log_messages = []

            def add_log(message):
                log_messages.append(message)

            with st.spinner("⏳ Processing your PDF..."):
                # Step 1: Analyze PDF
                add_log("🔍 Analyzing PDF structure...")

                # Step 2: Detect statements
                add_log("📄 Detecting financial statements...")

                # Step 3: Extract data
                add_log("🤖 Extracting data using AI...")

                final_state = process_pdf(pdf_path, selected_statements, log_callback=add_log)

                # Step 4: Validate
                add_log("⚖️ Validating extraction quality...")

                # Step 5: Generate outputs
                add_log("📥 Generating Excel and JSON files...")

            # Show logs after processing
            st.expander("📝 View Processing Log", expanded=False).markdown("\n\n".join(log_messages))

            st.session_state["processing_complete"] = True
            st.session_state["final_state"] = final_state
            st.session_state["pdf_name"] = pdf_name
            st.rerun()

# -----------------------------------------------------------------------------
# Results Section
# -----------------------------------------------------------------------------
if st.session_state.get("processing_complete"):
    st.divider()
    st.header("📋 Results")

    final_state = st.session_state.get("final_state", {})
    pdf_name = st.session_state.get("pdf_name", "")

    if final_state.get("error_message"):
        error_msg = final_state["error_message"]
        if "No financial" in error_msg or "No data" in error_msg:
            st.warning("⚠️ No financial statements detected in this PDF. Please upload a different document.")
        else:
            st.error(f"❌ {error_msg}")

    elif final_state.get("output_files"):
        st.success("✅ Extraction Complete!")

        log_file = final_state.get("log_file")
        if log_file and Path(log_file).exists():
            st.divider()
            st.header("📝 Processing Log")
            with open(log_file, "r") as f:
                log_content = f.read()
            st.code(log_content, language="text")

        output_files = final_state["output_files"]
        json_files = [f for f in output_files if f.endswith(".json")]
        excel_files = [f for f in output_files if f.endswith(".xlsx")]

        st.markdown("### 📥 Download Files")

        from pathlib import Path
        statement_files = {}
        for f in output_files:
            f_path = Path(f)
            for st_type in StatementType:
                if st_type.value in f_path.name:
                    if st_type not in statement_files:
                        statement_files[st_type] = {"json": None, "excel": None}
                    if f_path.suffix == ".json":
                        statement_files[st_type]["json"] = f_path
                    elif f_path.suffix == ".xlsx":
                        statement_files[st_type]["excel"] = f_path

        for st_type, files in statement_files.items():
            st.markdown(f"**{statement_options[st_type]}**")
            col1, col2 = st.columns(2)
            with col1:
                if files["json"] and files["json"].exists():
                    with open(files["json"], "rb") as f:
                        st.download_button(
                            "📥 JSON",
                            f.read(),
                            files["json"].name,
                            "application/json",
                            use_container_width=True
                        )
            with col2:
                if files["excel"] and files["excel"].exists():
                    with open(files["excel"], "rb") as f:
                        st.download_button(
                            "📥 Excel",
                            f.read(),
                            files["excel"].name,
                            "application/vnd.ms-excel",
                            use_container_width=True
                        )

        st.divider()
        st.header("⚖️ AI Evaluation (Reference)")
        st.caption("AI scores are for reference only. Always verify manually.")

        eval_result = final_state.get("evaluation_result", {})
        if eval_result:
            for st_type in StatementType:
                st_key = st_type.value
                if st_key in eval_result:
                    eval_data = eval_result[st_key]
                    st.markdown(f"#### {statement_options[st_type]}")

                    passed = eval_data.get("passed", False)
                    scores = eval_data.get("scores", {})

                    col1, col2 = st.columns([1, 2])
                    with col1:
                        st.metric("Status", "✅ Pass" if passed else "❌ Review")
                    with col2:
                        avg_score = sum(scores.values()) / len(scores) if scores else 0
                        st.metric("Avg Score", f"{avg_score:.1f}/10")

                    if scores:
                        score_df = pd.DataFrame([
                            {"Criterion": k.replace("_", " ").title(), "Score": v}
                            for k, v in scores.items()
                        ])
                        st.dataframe(score_df, use_container_width=True, hide_index=True)

                    st.divider()

        st.markdown("### 📥 Original Document")
        pdf_path = st.session_state.get("uploaded_pdf")
        if pdf_path and Path(pdf_path).exists():
            st.download_button(
                "📥 Download Original PDF",
                Path(pdf_path).read_bytes(),
                Path(pdf_path).name,
                "application/pdf",
                use_container_width=True
            )

# -----------------------------------------------------------------------------
# Metrics Dashboard
# -----------------------------------------------------------------------------
if st.session_state.get("show_metrics", False):
    st.divider()
    st.header("📈 Metrics Dashboard")

    from utils.observability import get_observability
    obs = get_observability()

    recent_runs = obs.get_recent_runs(limit=20)

    if recent_runs:
        stats = obs.get_stats(days=7)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Runs (7 days)", stats["total_runs"])
        with col2:
            st.metric("Success Rate", f"{stats['success_rate']}%")
        with col3:
            st.metric("Avg Duration", f"{stats['avg_duration_sec']:.1f}s")
        with col4:
            st.metric("Avg Retries", f"{stats['avg_retries_per_run']:.2f}")

        st.divider()

        st.subheader("📋 Recent Runs")
        if recent_runs:
            runs_df = pd.DataFrame(recent_runs)
            display_df = runs_df[[
                "timestamp", "pdf_file", "success", "total_duration_sec",
                "llm_calls", "retry_count"
            ]].copy()
            display_df.columns = ["Timestamp", "PDF", "Success", "Duration (s)", "LLM Calls", "Retries"]
            display_df["Timestamp"] = pd.to_datetime(display_df["Timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📊 Trends")

        if len(recent_runs) > 1:
            duration_data = pd.DataFrame(recent_runs)[["timestamp", "total_duration_sec"]].copy()
            duration_data["timestamp"] = pd.to_datetime(duration_data["timestamp"])
            duration_data = duration_data.sort_values("timestamp")
            st.line_chart(duration_data.set_index("timestamp")["total_duration_sec"])
            st.caption("Extraction duration over time")
    else:
        st.info("No metrics data yet. Run an extraction to see metrics.")
