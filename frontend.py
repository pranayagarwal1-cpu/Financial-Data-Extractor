"""
Streamlit Frontend for Multi-Agent Financial Statement Extractor

Run with: streamlit run frontend.py

Freemium Model:
- Free Tier: 2 extractions per month
- Pro Tier: Unlimited extractions ($29/month)
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
from utils.freemium import UsageTracker, init_usage_session, check_extraction_limit, render_usage_indicator, FREE_TIER_LIMIT

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


def process_pdf(pdf_path: str, statement_types: list):
    """Process a PDF through the workflow and return results."""
    from utils.observability import get_observability
    obs = get_observability()

    workflow = create_workflow(statement_types)
    initial_state = {
        "input_pdf": pdf_path,
        "statement_types": statement_types,
        "retry_count": 0
    }
    try:
        final_state = workflow.invoke(initial_state)
        if final_state.get("error_message") and not final_state.get("output_files"):
            run_id = final_state.get("run_id")
            if run_id:
                obs.end_run(run_id=run_id, success=False, error_message=final_state["error_message"])
        return final_state
    except Exception as e:
        obs.end_run(run_id=initial_state.get("run_id", ""), success=False, error_message=str(e))
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
# Initialize Freemium Model
# -----------------------------------------------------------------------------

# User email input (in production, replace with real auth)
if "user_email" not in st.session_state:
    st.session_state["user_email"] = ""

if "usage_tracker" not in st.session_state:
    st.session_state["usage_tracker"] = UsageTracker()

# Login / User identification
if not st.session_state["user_email"]:
    st.title("📊 Financial Statement Extractor")
    st.markdown("### Welcome! Please enter your email to continue")

    email_input = st.text_input("Email address", placeholder="analyst@company.com")

    if st.button("Continue", type="primary"):
        if email_input:
            st.session_state["user_email"] = email_input
            init_usage_session(email_input)
            st.rerun()
    st.stop()

# Initialize session for logged-in user
init_usage_session(st.session_state["user_email"])

# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
with st.sidebar:
    # User info
    st.markdown(f"**👤 {st.session_state['user_email']}**")
    st.divider()

    # Usage indicator (Free vs Pro)
    render_usage_indicator()

    st.divider()

    # Upload section
    st.header("📁 Upload PDF")

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

    # Check if user is free tier - limit to 1 statement at a time
    stats = st.session_state.get("usage_stats", {})
    tier = stats.get("tier", "free")
    is_pro = tier == "pro"

    selected_statements = st.multiselect(
        "Select statements:",
        options=list(statement_options.keys()),
        default=[StatementType.BALANCE_SHEET],
        format_func=lambda x: statement_options[x],
        help="Free tier: 1 statement at a time | Pro: Extract all statements simultaneously"
    )

    # Free tier limitation
    if not is_pro and len(selected_statements) > 1:
        st.warning("⚠️ Free tier: Select only 1 statement at a time. Upgrade to Pro for multi-statement extraction.")
        selected_statements = selected_statements[:1]

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
# Upgrade Modal (shown when user clicks upgrade)
# -----------------------------------------------------------------------------
if st.session_state.get("show_upgrade_modal"):
    st.markdown("""
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 30px; border-radius: 15px; margin: 20px 0; color: white;">
        <h2 style="margin: 0;">⭐ Upgrade to Pro</h2>
        <p style="font-size: 1.2em; margin: 10px 0;">Unlock unlimited extractions</p>
        <ul style="font-size: 1em;">
            <li>✅ Unlimited extractions (no monthly cap)</li>
            <li>✅ Multi-statement extraction (all 3 at once)</li>
            <li>✅ Download Excel + JSON files</li>
            <li>✅ No watermark on results</li>
            <li>✅ Priority processing</li>
            <li>✅ Usage analytics dashboard</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        # Stripe Payment Link - REPLACE WITH YOUR ACTUAL LINK
        stripe_link = "https://buy.stripe.com/cNicN41Us9gW0QYex7cwg00"

        st.markdown(f"""
        <div style="text-align: center; padding: 20px;">
            <p style="font-size: 2em; font-weight: bold; margin: 10px 0;">$29<span style="font-size: 0.5em; font-weight: normal;">/month</span></p>
            <a href="{stripe_link}" target="_blank"
               style="background: #059669; color: white; padding: 15px 40px;
                      text-decoration: none; border-radius: 8px; font-weight: bold;
                      display: inline-block; margin: 10px 0;">
                🚀 Upgrade to Pro Now
            </a>
            <p style="font-size: 0.9em; color: #10b981; font-weight: bold; margin: 10px 0;">
                📊 Unlimited extractions/month
            </p>
            <p style="font-size: 0.8em; color: #666; margin-top: 15px;">
                Secure checkout powered by Stripe
            </p>
        </div>
        """, unsafe_allow_html=True)

        if st.button("Close", use_container_width=True):
            st.session_state["show_upgrade_modal"] = False
            st.rerun()

    st.divider()

# -----------------------------------------------------------------------------
# Main Content
# -----------------------------------------------------------------------------
st.title("📊 Financial Statement Extractor")

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
# Process Button with Freemium Check
# -----------------------------------------------------------------------------
if "uploaded_pdf" in st.session_state:
    st.divider()

    if selected_statements:
        st.caption(f"📊 Extracting: {', '.join([statement_options[s] for s in selected_statements])}")

    # Check extraction limit BEFORE processing
    stats = st.session_state.get("usage_stats", {})
    tier = stats.get("tier", "free")
    current = stats.get("extractions_this_month", 0)
    limit = stats.get("limit", FREE_TIER_LIMIT)

    if tier == "free" and current >= limit:
        st.error(f"⚠️ Free tier limit reached ({current}/{limit} extractions this month)")
        st.info("👉 Upgrade to Pro for unlimited extractions")
        if st.button("⬆️ Upgrade to Pro", type="primary", use_container_width=True):
            st.session_state["show_upgrade_modal"] = True
            st.rerun()
    else:
        process_btn = st.button("🚀 Extract Statements", type="primary", use_container_width=True)

        if process_btn:
            if not selected_statements:
                st.warning("Please select at least one statement type to extract.")
            else:
                pdf_path = st.session_state["uploaded_pdf"]
                pdf_name = Path(pdf_path).stem

                st.divider()
                st.header("⏳ Processing")

                progress_bar = st.progress(0)
                log_container = st.expander("📝 Processing Log", expanded=True)
                log_text = log_container.empty()

                try:
                    final_state = process_pdf(pdf_path, selected_statements)
                finally:
                    pass

                progress_bar.progress(100)
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
        # Check tier for watermark
        stats = st.session_state.get("usage_stats", {})
        tier = stats.get("tier", "free")
        is_pro = tier == "pro"

        st.success("✅ Extraction Complete!")

        # Free tier watermark
        if not is_pro:
            st.markdown(
                "<div style='background: #fff3cd; border-left: 4px solid #ffc107; "
                "padding: 15px; margin: 20px 0; border-radius: 4px;'>"
                "<strong>⚠️ Free Tier Preview</strong><br>"
                "Upgrade to Pro to download Excel/JSON files and remove watermark"
                "</div>",
                unsafe_allow_html=True
            )

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
                    if is_pro:
                        with open(files["json"], "rb") as f:
                            st.download_button(
                                "📥 JSON",
                                f.read(),
                                files["json"].name,
                                "application/json",
                                use_container_width=True
                            )
                    else:
                        st.button("📥 JSON", disabled=True, use_container_width=True, help="Pro feature")
            with col2:
                if files["excel"] and files["excel"].exists():
                    if is_pro:
                        with open(files["excel"], "rb") as f:
                            st.download_button(
                                "📥 Excel",
                                f.read(),
                                files["excel"].name,
                                "application/vnd.ms-excel",
                                use_container_width=True
                            )
                    else:
                        st.button("📥 Excel", disabled=True, use_container_width=True, help="Pro feature")

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
