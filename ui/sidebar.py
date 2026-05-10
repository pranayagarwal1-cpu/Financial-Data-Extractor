"""Sidebar component: contact, upload, statement selection, CoA toggle, cleanup."""

from pathlib import Path

import streamlit as st

from utils.vlm_utils import StatementType
from ui import INPUT_DIR, OUTPUT_DIR, TMP_DIR
from ui.session import is_new_upload_set, reset_results_state


STATEMENT_OPTIONS = {
    StatementType.BALANCE_SHEET: "Balance Sheet",
    StatementType.INCOME_STATEMENT: "Income Statement",
    StatementType.CASH_FLOW: "Cash Flow Statement",
}


def _render_contact_button() -> None:
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
        .sidebar-contact a:hover { background-color: #003366; }
        </style>
        <div class="sidebar-contact">
            <a href="mailto:data.analytics.product@gmail.com?subject=Interested in Customizing Financial Statement Extractor&body=Hi, I found the Financial Statement Extractor valuable and I'm interested in customizing it for my use case.">
                Contact
            </a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_upload() -> None:
    st.header("📁 Upload PDFs")
    st.markdown(
        "<style>"
        "div[data-testid='stFileUploader'] {text-align: center;}"
        "div[data-testid='stFileUploader'] > div {margin: 0 auto;}"
        "</style>",
        unsafe_allow_html=True,
    )

    uploaded_files = st.file_uploader(
        "Choose PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        help="Drag and drop or click to browse. Multiple files supported.",
    )

    if not uploaded_files:
        return

    filenames = [f.name for f in uploaded_files]

    # Drop stale results when a new set of files appears so the UI doesn't
    # show last run's tabs alongside a brand-new upload.
    if is_new_upload_set(filenames):
        reset_results_state()
        st.session_state["pdfs_uploaded"] = (
            st.session_state.get("pdfs_uploaded", 0) + len(filenames)
        )

    for uploaded_file in uploaded_files:
        pdf_path = INPUT_DIR / uploaded_file.name
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

    st.session_state["uploaded_pdfs"] = [str(INPUT_DIR / name) for name in filenames]
    st.session_state["last_uploaded_files"] = filenames
    st.success(f"✅ {len(uploaded_files)} file(s) uploaded!")


def _render_statement_selector() -> None:
    st.header("📊 Statements to Extract")
    selected = st.multiselect(
        "Select statements:",
        options=list(STATEMENT_OPTIONS.keys()),
        default=[StatementType.BALANCE_SHEET],
        format_func=lambda x: STATEMENT_OPTIONS[x],
    )
    st.session_state["selected_statements"] = selected


def _render_categorization_toggle() -> None:
    st.header("🏷️ CoA Categorization")
    enabled = st.toggle(
        "Enable Chart of Accounts categorization",
        value=True,
        help="Map extracted line items to veterinary practice CoA codes. Adds ~5–15 min per income statement.",
    )
    st.session_state["enable_categorization"] = enabled


def _clean_directory(directory: Path) -> int:
    if not directory.exists():
        return 0
    count = 0
    for item in directory.rglob("*"):
        if item.is_file():
            item.unlink()
            count += 1
    for item in sorted(directory.rglob("*"), key=lambda p: len(str(p)), reverse=True):
        if item.is_dir():
            item.rmdir()
    return count


def _render_clean_button() -> None:
    if st.button("🗑️ Clean All Files", width="stretch"):
        files_deleted = sum(_clean_directory(d) for d in (INPUT_DIR, OUTPUT_DIR, TMP_DIR))
        st.success(f"✅ {files_deleted} files deleted!")
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.markdown(
        """
        <div style="text-align: center; color: #28a745; font-weight: bold; margin-top: 15px;">
            🔒 Zero Data Retention
        </div>
        <p style="text-align: center; font-size: 0.85em; color: #666;">
            All files stored locally. Clean anytime.
        </p>
        """,
        unsafe_allow_html=True,
    )


def _render_metrics_link() -> None:
    st.header("📈 Analytics")
    if st.button("📊 View Metrics Dashboard", width="stretch"):
        st.session_state["show_metrics"] = not st.session_state.get("show_metrics", False)


def render_sidebar() -> None:
    """Render the entire sidebar. Reads/writes st.session_state."""
    with st.sidebar:
        _render_contact_button()
        _render_upload()
        st.divider()
        _render_statement_selector()
        _render_categorization_toggle()
        st.divider()
        _render_clean_button()
        st.divider()
        _render_metrics_link()
