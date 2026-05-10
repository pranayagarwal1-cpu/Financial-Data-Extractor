"""Metrics dashboard component: aggregate stats and recent-runs table."""

import pandas as pd
import streamlit as st

from utils.observability import get_observability


def render_metrics_dashboard() -> None:
    """Render the metrics dashboard if the user has toggled it on."""
    if not st.session_state.get("show_metrics", False):
        return

    st.divider()
    st.header("📈 Metrics Dashboard")

    obs = get_observability()
    recent_runs = obs.get_recent_runs(limit=20)

    if not recent_runs:
        st.info("No metrics data yet. Run an extraction to see metrics.")
        return

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
    runs_df = pd.DataFrame(recent_runs)
    display_df = runs_df[[
        "timestamp", "pdf_file", "success", "total_duration_sec",
        "llm_calls", "retry_count",
    ]].copy()
    display_df.columns = ["Timestamp", "PDF", "Success", "Duration (s)", "LLM Calls", "Retries"]
    display_df["Timestamp"] = pd.to_datetime(display_df["Timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(display_df, width="stretch", hide_index=True)

    if len(recent_runs) > 1:
        st.divider()
        st.subheader("📊 Trends")
        duration_data = pd.DataFrame(recent_runs)[["timestamp", "total_duration_sec"]].copy()
        duration_data["timestamp"] = pd.to_datetime(duration_data["timestamp"])
        duration_data = duration_data.sort_values("timestamp")
        st.line_chart(duration_data.set_index("timestamp")["total_duration_sec"])
        st.caption("Extraction duration over time")
