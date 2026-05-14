"""Results component: per-PDF extraction view with side-by-side preview, downloads, evaluation, and CoA review."""

import json
import logging
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.vlm_utils import StatementType
from ui.sidebar import STATEMENT_OPTIONS


def _group_output_files(output_files):
    """Group output files by statement type, with json/excel pointers."""
    statement_files = {}
    for f in output_files:
        f_path = Path(f)
        for st_type in StatementType:
            if st_type.value in f_path.name:
                if st_type not in statement_files:
                    statement_files[st_type] = {"json": None, "excel": None, "pdf_page": None}
                if f_path.suffix == ".json":
                    statement_files[st_type]["json"] = f_path
                elif f_path.suffix == ".xlsx":
                    statement_files[st_type]["excel"] = f_path
    return statement_files


def _render_pdf_preview(pdf_path, pdf_name, stmt_type, statement_pages):
    """Left column: paginated PDF preview with statement-page indicators."""
    from utils.pdf_utils import get_page_count, rasterize_page_to_png

    st.markdown("**📄 Original PDF**")

    pages_for_type = statement_pages.get(stmt_type, [])

    try:
        total_pages = get_page_count(pdf_path)
    except Exception as e:
        st.warning(f"Could not read page count for {Path(pdf_path).name}: {e}")
        total_pages = 1

    statement_page_nums = pages_for_type  # Already 1-indexed
    all_page_options = [
        f"Page {p} ✓" if p in statement_page_nums else f"Page {p}"
        for p in range(1, total_pages + 1)
    ]

    default_idx = (statement_page_nums[0] - 1) if statement_page_nums else 0

    selected = st.selectbox(
        "Jump to page:",
        options=all_page_options,
        index=default_idx,
        key=f"page_select_{pdf_name}_{stmt_type.value}",
    )
    page_num = int(selected.split()[1])

    png_bytes = rasterize_page_to_png(pdf_path, page_num, dpi=150)
    if png_bytes:
        st.image(png_bytes, caption=f"Page {page_num}", width="stretch")

    if pages_for_type:
        st.caption(f"✓ Statement detected on page(s): {', '.join(str(p) for p in statement_page_nums)}")
        st.caption(f"Total PDF pages: {total_pages}")

    st.download_button(
        "📥 Download Full PDF",
        Path(pdf_path).read_bytes(),
        Path(pdf_path).name,
        "application/pdf",
        key=f"view_pdf_{pdf_name}_{stmt_type.value}",
        width="stretch",
    )


def _render_extracted_table(extracted_data):
    """Right column: extracted-data table grouped by section."""
    st.markdown("**📊 Extracted Data**")
    st.caption("Read-only preview — use Review & Correct section below to edit CoA mappings")

    sections = extracted_data.get("sections") or []
    periods = extracted_data.get("periods", ["Period 1", "Period 2"])

    for section in sections:
        section_name = section.get("name", "")
        if section_name:
            st.markdown(f"**{section_name}**")

        rows = section.get("rows", [])
        if not rows:
            continue

        table_data = []
        for row in rows:
            row_data = {"Line Item": row.get("label", "")}
            values = row.get("values", [])
            for i, period in enumerate(periods):
                row_data[period] = values[i] if i < len(values) else ""
            table_data.append(row_data)

        if table_data:
            st.dataframe(pd.DataFrame(table_data), width="stretch", hide_index=True)


def _render_download_buttons(statement_files, pdf_name):
    st.markdown("##### 📥 Download Files")
    for st_type, files in statement_files.items():
        st.markdown(f"**{STATEMENT_OPTIONS[st_type]}**")
        col1, col2 = st.columns(2)
        with col1:
            if files["json"] and files["json"].exists():
                with open(files["json"], "rb") as f:
                    st.download_button(
                        "📥 JSON",
                        f.read(),
                        files["json"].name,
                        "application/json",
                        key=f"json_{pdf_name}_{st_type.value}",
                        width="stretch",
                    )
        with col2:
            if files["excel"] and files["excel"].exists():
                with open(files["excel"], "rb") as f:
                    st.download_button(
                        "📥 Excel",
                        f.read(),
                        files["excel"].name,
                        "application/vnd.ms-excel",
                        key=f"excel_{pdf_name}_{st_type.value}",
                        width="stretch",
                    )


def _render_evaluation(eval_result):
    if not eval_result:
        return
    st.markdown("##### ⚖️ AI Evaluation")
    for st_type in StatementType:
        eval_data = eval_result.get(st_type) or eval_result.get(st_type.value)
        if not eval_data:
            continue
        st.markdown(f"**{STATEMENT_OPTIONS[st_type]}**")

        passed = eval_data.get("passed", False)
        scores = eval_data.get("scores", {})

        col1, col2 = st.columns([1, 2])
        with col1:
            st.metric("Status", "✅ Pass" if passed else "❌ Review")
        with col2:
            avg_score = sum(scores.values()) / len(scores) if scores else 0
            st.metric("Avg Score", f"{avg_score:.1f}/10")

        if scores:
            score_df = pd.DataFrame(
                [{"Criterion": k.replace("_", " ").title(), "Score": v} for k, v in scores.items()]
            )
            st.dataframe(score_df, width="stretch", hide_index=True)

            feedback = eval_data.get("feedback", "")
            if feedback:
                if not passed or avg_score < 7:
                    st.warning(f"⚠️ {feedback}")
                else:
                    st.info(f"✅ {feedback}")


def _render_categorization_metrics(cat_metrics):
    if not cat_metrics:
        return
    st.markdown("##### 📊 Categorization Metrics")
    for st_name, metrics in cat_metrics.items():
        if st_name != "income_statement":
            continue
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Coverage", f"{metrics.get('coverage_rate', 0):.1%}")
        with col2:
            st.metric("High Conf", f"{metrics.get('high_conf_rate', 0):.1%}")
        with col3:
            st.metric("Review Rate", f"{metrics.get('review_rate', 0):.1%}")
        with col4:
            st.metric("Sanity", f"{metrics.get('overall_sanity', 0):.1%}")

        with st.expander("Detailed Metrics", expanded=False):
            detail_items = []
            for k, v in metrics.items():
                if isinstance(v, float):
                    detail_items.append({"Metric": k.replace("_", " ").title(), "Value": f"{v:.3f}"})
                elif isinstance(v, int):
                    detail_items.append({"Metric": k.replace("_", " ").title(), "Value": v})
            if detail_items:
                st.dataframe(pd.DataFrame(detail_items), width="stretch", hide_index=True)
            violations = metrics.get("section_violations", [])
            if violations:
                st.warning(f"⚠️ {len(violations)} section sanity violation(s)")
                viol_df = pd.DataFrame(violations)
                st.dataframe(viol_df, width="stretch", hide_index=True)
            bs_codes = metrics.get("balance_sheet_codes_used", [])
            if bs_codes:
                st.error(f"❌ Balance-sheet codes used: {set(bs_codes)}")


def _render_categorization_evaluation(cat_eval):
    if not cat_eval:
        return
    st.markdown("##### ⚖️ CoA Categorization Evaluation")
    for st_key, cat_data_eval in cat_eval.items():
        st_name = st_key.value if hasattr(st_key, "value") else str(st_key)
        if st_name != "income_statement":
            continue
        cat_passed = cat_data_eval.get("passed", False)
        cat_scores = cat_data_eval.get("scores", {})
        cat_feedback = cat_data_eval.get("feedback", "")
        col1, col2 = st.columns([1, 2])
        with col1:
            st.metric("Status", "✅ Pass" if cat_passed else "❌ Review")
        with col2:
            avg_cat = sum(cat_scores.values()) / len(cat_scores) if cat_scores else 0
            st.metric("Avg Score", f"{avg_cat:.1f}/10")
        if cat_feedback:
            if not cat_passed:
                st.warning(f"⚠️ {cat_feedback}")
            else:
                st.info(f"✅ {cat_feedback}")
        if cat_scores:
            cat_score_df = pd.DataFrame(
                [{"Criterion": k.replace("_", " ").title(), "Score": v} for k, v in cat_scores.items()]
            )
            st.dataframe(cat_score_df, width="stretch", hide_index=True)


def _render_review_and_correct(statement_files, pdf_name):
    st.markdown("##### 📝 Review & Correct CoA Mappings")
    st.caption("Click the **Corrected Code** cell to open the dropdown and select a new account. Then click **💾 Save Corrections**.")

    cat_json = statement_files.get(StatementType.INCOME_STATEMENT, {}).get("json")
    if not (cat_json and cat_json.exists()):
        return

    with open(cat_json, "r") as f:
        cat_data = json.load(f)

    review_items = []
    for section in cat_data.get("sections", []):
        section_name = section.get("name", "")
        for row in section.get("rows", []):
            cat = row.get("categorization", {})
            if not cat:
                continue
            is_review = cat.get("needs_review", False)
            is_low = cat.get("confidence") in ("low", "unmatched")
            if is_review or is_low:
                review_items.append({
                    "label": row.get("label", ""),
                    "section": section_name,
                    "current_code": str(cat.get("coa_code", "")),
                    "current_name": str(cat.get("coa_name", "")),
                    "confidence": str(cat.get("confidence", "")),
                    "reasoning": str(cat.get("reasoning", "")),
                })

    if not review_items:
        st.success("✅ No items flagged for review — all mappings look good!")
        return

    st.info(f"{len(review_items)} item(s) flagged for review")

    from coa.chart_of_accounts import COA_ACCOUNTS, get_account_by_code

    coa_display_options = [f"{code} - {acc.name}" for code, acc in COA_ACCOUNTS.items()]
    for item in review_items:
        display = f"{item['current_code']} - {item['current_name']}"
        if display not in coa_display_options:
            coa_display_options.insert(0, display)

    review_df = pd.DataFrame(review_items)
    review_df["corrected_code"] = review_df.apply(
        lambda r: f"{r['current_code']} - {r['current_name']}", axis=1
    )

    edited_df = st.data_editor(
        review_df,
        column_config={
            "corrected_code": st.column_config.SelectboxColumn(
                "Corrected Code",
                options=coa_display_options,
            ),
            "label": st.column_config.TextColumn("Line Item", disabled=True),
            "section": st.column_config.TextColumn("Section", disabled=True),
            "current_code": st.column_config.TextColumn("Current Code", disabled=True),
            "current_name": st.column_config.TextColumn("Current Name", disabled=True),
            "confidence": st.column_config.TextColumn("Confidence", disabled=True),
            "reasoning": st.column_config.TextColumn("Reasoning", disabled=True),
        },
        width="stretch",
        hide_index=True,
        key=f"review_editor_{pdf_name}",
    )

    with st.expander("🔍 CoA Account Lookup (reference)", expanded=False):
        ref_search = st.text_input("Search account name or code", key=f"ref_search_{pdf_name}")
        if ref_search:
            ref_matches = [opt for opt in coa_display_options if ref_search.lower() in opt.lower()]
            st.write(f"{len(ref_matches)} match(es)")
            st.write("  ".join([f"`{opt}`" for opt in ref_matches[:20]]))
        else:
            st.caption("Type above to search all 196 accounts")

    if st.button("💾 Save Corrections", key=f"save_corr_{pdf_name}", type="primary"):
        corrections = []
        for _, row in edited_df.iterrows():
            orig_code = str(row["current_code"])
            new_display = str(row["corrected_code"])
            new_code = new_display.split(" - ")[0] if " - " in new_display else new_display
            if new_code != orig_code:
                acc = get_account_by_code(new_code)
                corrections.append({
                    "label": row["label"],
                    "section": row["section"],
                    "wrong_code": orig_code,
                    "correct_code": new_code,
                    "correct_name": acc.name if acc else "",
                })

        if corrections:
            from utils.memory_manager import append_corrections
            saved = append_corrections(pdf_name, corrections)
            st.success(f"✅ {saved} new correction(s) saved to memory/{pdf_name}.md")
        else:
            st.info("No changes to save.")


def _render_single_result(result):
    final_state = result["final_state"]
    pdf_name = result["pdf_name"]
    pdf_path = result["pdf_path"]

    output_files = final_state.get("output_files") or []
    if not output_files:
        return

    st.success("✅ Extraction Complete!")
    st.markdown("##### 🔍 Review Extraction")

    statement_files = _group_output_files(output_files)
    statement_tabs = {
        StatementType.BALANCE_SHEET: "Balance Sheet",
        StatementType.INCOME_STATEMENT: "Income Statement",
        StatementType.CASH_FLOW: "Cash Flow",
    }
    tab_keys = [k for k in statement_tabs if k in statement_files and statement_files[k]["json"]]

    if tab_keys:
        tabs = st.tabs([statement_tabs[k] for k in tab_keys])
        for tab, stmt_type in zip(tabs, tab_keys):
            with tab:
                json_file = statement_files[stmt_type]["json"]
                if json_file and json_file.exists():
                    with open(json_file, "r") as f:
                        extracted_data = json.load(f)
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        _render_pdf_preview(
                            pdf_path, pdf_name, stmt_type,
                            final_state.get("statement_pages", {}),
                        )
                    with col2:
                        _render_extracted_table(extracted_data)
                    st.divider()

    _render_download_buttons(statement_files, pdf_name)
    _render_evaluation(final_state.get("evaluation_result", {}))

    st.markdown("##### 📥 Original Document")
    if pdf_path and Path(pdf_path).exists():
        st.download_button(
            "📥 Download Original PDF",
            Path(pdf_path).read_bytes(),
            Path(pdf_path).name,
            "application/pdf",
            key=f"pdf_{pdf_name}",
            width="stretch",
        )

    _render_categorization_evaluation(final_state.get("cat_evaluation_result", {}))
    _render_categorization_metrics(final_state.get("cat_metrics", {}))
    _render_review_and_correct(statement_files, pdf_name)


def _track_extraction_counts():
    """Bump the per-session success/excel/json counters once per processing batch."""
    if not st.session_state.get("processing_complete"):
        return
    if st.session_state.get("extraction_counted"):
        return
    for result in st.session_state.get("all_results", []):
        final_state = result["final_state"]
        if not final_state.get("output_files"):
            continue
        excel_files = [f for f in final_state["output_files"] if f.endswith(".xlsx")]
        json_files = [f for f in final_state["output_files"] if f.endswith(".json")]
        st.session_state["extracted_count"] += 1
        st.session_state["excel_count"] += len(excel_files)
        st.session_state["json_count"] += len(json_files)
    st.session_state["extraction_counted"] = True


def render_results() -> None:
    """Render the results section if a processing batch has completed."""
    _track_extraction_counts()

    if not st.session_state.get("processing_complete"):
        return

    st.divider()
    st.header("📋 Results")

    log_messages = st.session_state.get("log_messages", [])
    if log_messages:
        with st.expander("📝 View Processing Summary", expanded=False):
            st.markdown("\n\n".join(log_messages))

    all_results = st.session_state.get("all_results", [])
    successful = [r for r in all_results if not r["final_state"].get("error_message")]
    failed = [r for r in all_results if r["final_state"].get("error_message")]

    if failed:
        st.markdown("### ❌ Failed Extractions")
        for result in failed:
            with st.expander(f"📄 {result['pdf_name']}"):
                error_msg = result["final_state"].get("error_message", "Unknown error")
                if "No financial" in error_msg or "No data" in error_msg:
                    st.warning("⚠️ No financial statements detected in this PDF.")
                else:
                    st.error(f"❌ {error_msg}")

    if successful:
        st.markdown(f"### ✅ Successful Extractions ({len(successful)} file(s))")
        for result in successful:
            with st.expander(f"📄 {result['pdf_name']}", expanded=len(successful) == 1):
                _render_single_result(result)
