"""Session-state lifecycle for the Streamlit frontend.

Streamlit re-runs the script top-to-bottom on every interaction, so all
cross-run state lives in st.session_state. This module centralizes the
defaults and the "new upload arrived, drop stale results" reset path so
behavior is predictable across reruns.
"""

from typing import List

import streamlit as st


_DEFAULTS = {
    # Per-session counters surfaced in the metrics row at the top
    "pdfs_uploaded": 0,
    "extracted_count": 0,
    "excel_count": 0,
    "json_count": 0,
    "extraction_counted": False,
    # Upload tracking
    "uploaded_pdfs": [],
    "last_uploaded_files": [],
    # Sidebar selections
    "selected_statements": [],
    "enable_categorization": True,
    # Processing results
    "all_results": [],
    "processing_complete": False,
    "log_messages": [],
    # Metrics dashboard toggle
    "show_metrics": False,
}


def init_session_state() -> None:
    """Set defaults for any session_state keys that haven't been initialized.

    Idempotent — re-running it preserves existing user state. Call this
    once at the top of frontend.py before any component reads state.
    """
    for key, default in _DEFAULTS.items():
        if key not in st.session_state:
            # Lists/dicts are copied so each session gets its own instance.
            st.session_state[key] = default.copy() if isinstance(default, (list, dict)) else default


def reset_results_state() -> None:
    """Clear results from a previous extraction run.

    Call this when the user uploads a fresh set of PDFs so stale
    `all_results` / `log_messages` don't leak into the new render.
    """
    st.session_state["all_results"] = []
    st.session_state["processing_complete"] = False
    st.session_state["log_messages"] = []
    st.session_state["extraction_counted"] = False


def is_new_upload_set(filenames: List[str]) -> bool:
    """Return True if the uploader is showing a different set of files than last render."""
    previous = st.session_state.get("last_uploaded_files", [])
    return sorted(filenames) != sorted(previous)
