import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl import utils


def save_to_excel(data: dict, output_path: str, include_coa_columns: bool = True):
    """
    Save extracted financial statement data to an Excel file.

    Includes CoA columns (Code, Name, Category, Match Type, Confidence,
    Reasoning, Needs Review, Citation) if categorization metadata is present.

    Args:
        data: Dict with keys: title, periods, sections (rows may have 'categorization')
        output_path: Path to save the Excel file
        include_coa_columns: Whether to add CoA columns for categorized data
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = data.get("statement_type", "Statement")

    # Styles
    header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill("solid", start_color="1F4E79")
    coa_header_font = Font(name="Arial", bold=True, size=9, color="FFFFFF")
    coa_header_fill = PatternFill("solid", start_color="5B9BD5")
    section_font = Font(name="Arial", bold=True, size=10, color="1F4E79")
    section_fill = PatternFill("solid", start_color="D6E4F0")
    subtotal_font = Font(name="Arial", bold=True, size=10)
    subtotal_fill = PatternFill("solid", start_color="EBF5FB")
    normal_font = Font(name="Arial", size=10)
    coa_font = Font(name="Arial", size=9, color="006600")
    coa_fill = PatternFill("solid", start_color="E6F7FF")
    review_font = Font(name="Arial", size=9, color="CC0000")
    review_fill = PatternFill("solid", start_color="FFF0F0")
    warn_font = Font(name="Arial", size=9, color="CC6600")
    warn_fill = PatternFill("solid", start_color="FFF8E1")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_align = Alignment(horizontal="right", vertical="center")
    thin_border = Border(
        bottom=Side(border_style="thin", color="AAAAAA")
    )

    periods = data.get("periods", [])
    num_periods = len(periods)

    # Check if any rows have categorization
    has_categorization = any(
        "categorization" in row
        for section in data.get("sections", [])
        for row in section.get("rows", [])
        if not row.get("is_subtotal")
    )

    # CoA columns (placed to the RIGHT of period columns)
    coa_cols = ["CoA Code", "CoA Name", "CoA Category", "Match Type",
                "Confidence", "Reasoning", "Needs Review", "Citation"]
    show_coa = include_coa_columns and has_categorization
    num_coa_cols = len(coa_cols) if show_coa else 0

    # Layout: Line Item | Period 1 | Period 2 | ... | CoA Code | CoA Name | ...
    # Period columns start at col 2, CoA columns start after periods
    period_start_col = 2
    coa_start_col = period_start_col + num_periods
    total_cols = 1 + num_periods + num_coa_cols

    # Row 1: title
    ws.merge_cells(start_row=1, start_column=1,
                   end_row=1, end_column=total_cols)
    title_cell = ws.cell(row=1, column=1, value=data.get("title", "Financial Statement"))
    title_cell.font = Font(name="Arial", bold=True, size=13, color="1F4E79")
    title_cell.alignment = center_align
    ws.row_dimensions[1].height = 22

    # Row 2: column headers
    ws.cell(row=2, column=1, value="Line Item").font = header_font
    ws.cell(row=2, column=1).fill = header_fill

    # Period headers (columns 2 onward)
    for i, period in enumerate(periods, start=period_start_col):
        cell = ws.cell(row=2, column=i, value=period)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    # CoA column headers (after period columns)
    for offset, header in enumerate(coa_cols):
        if show_coa:
            col = coa_start_col + offset
            cell = ws.cell(row=2, column=col, value=header)
            cell.font = coa_header_font
            cell.fill = coa_header_fill
            cell.alignment = center_align
    ws.row_dimensions[2].height = 18

    current_row = 3

    for section in data.get("sections", []):
        # Section header
        ws.merge_cells(start_row=current_row, start_column=1,
                       end_row=current_row, end_column=total_cols)
        sec_cell = ws.cell(row=current_row, column=1, value=section["name"])
        sec_cell.font = section_font
        sec_cell.fill = section_fill
        ws.row_dimensions[current_row].height = 16
        current_row += 1

        for row in section.get("rows", []):
            # Label column
            label_cell = ws.cell(row=current_row, column=1, value=row["label"])
            label_cell.font = subtotal_font if row.get("is_subtotal") else normal_font
            if row.get("is_subtotal"):
                label_cell.fill = subtotal_fill
                label_cell.border = thin_border

            # Value columns (period values)
            for i, val in enumerate(row.get("values", []), start=period_start_col):
                val_cell = ws.cell(row=current_row, column=i, value=val)
                val_cell.font = subtotal_font if row.get("is_subtotal") else normal_font
                val_cell.alignment = right_align
                if row.get("is_subtotal"):
                    val_cell.fill = subtotal_fill
                    val_cell.border = thin_border

            # CoA columns (to the right of period values)
            if show_coa and not row.get("is_subtotal"):
                cat = row.get("categorization", {})
                is_unmatched = cat.get("match_type") == "unmatched"
                is_needs_review = cat.get("needs_review", False)
                is_low_conf = cat.get("confidence") in ("low", "unmatched")
                is_section_hdr = cat.get("match_type") == "section_header"

                # Determine row style based on status
                if is_unmatched or is_needs_review:
                    row_font = review_font
                    row_fill = review_fill
                elif is_low_conf and not is_section_hdr:
                    row_font = warn_font
                    row_fill = warn_fill
                else:
                    row_font = coa_font
                    row_fill = coa_fill

                coa_values = [
                    cat.get("coa_code", ""),
                    cat.get("coa_name", ""),
                    cat.get("coa_category", ""),
                    cat.get("match_type", ""),
                    cat.get("confidence", ""),
                    cat.get("reasoning", ""),
                    "YES" if is_needs_review else "No",
                    cat.get("citation", ""),
                ]
                for offset, val in enumerate(coa_values):
                    col = coa_start_col + offset
                    cell = ws.cell(row=current_row, column=col, value=val)
                    cell.font = row_font
                    cell.fill = row_fill
                    cell.alignment = left_align if offset >= 5 else center_align

            current_row += 1

        current_row += 1  # blank row between sections

    # Column widths
    ws.column_dimensions["A"].width = 40
    for i in range(period_start_col, period_start_col + num_periods):
        col_letter = utils.get_column_letter(i)
        ws.column_dimensions[col_letter].width = 18
    if show_coa:
        for offset in range(num_coa_cols):
            col_letter = utils.get_column_letter(coa_start_col + offset)
            widths = [12, 35, 18, 14, 12, 40, 14, 28]
            ws.column_dimensions[col_letter].width = widths[offset]

    wb.save(output_path)
