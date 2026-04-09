import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl import utils


def save_to_excel(data: dict, output_path: str):
    """
    Save extracted balance sheet data to an Excel file.

    Args:
        data: Dict with keys: title, periods, sections
        output_path: Path to save the Excel file
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"

    # Styles
    header_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill("solid", start_color="1F4E79")
    section_font = Font(name="Arial", bold=True, size=10, color="1F4E79")
    section_fill = PatternFill("solid", start_color="D6E4F0")
    subtotal_font = Font(name="Arial", bold=True, size=10)
    subtotal_fill = PatternFill("solid", start_color="EBF5FB")
    normal_font = Font(name="Arial", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    right_align = Alignment(horizontal="right", vertical="center")
    thin_border = Border(
        bottom=Side(border_style="thin", color="AAAAAA")
    )

    periods = data.get("periods", [])
    num_cols = len(periods)

    # Row 1: title
    ws.merge_cells(start_row=1, start_column=1,
                   end_row=1, end_column=1 + num_cols)
    title_cell = ws.cell(row=1, column=1, value=data.get("title", "Balance Sheet"))
    title_cell.font = Font(name="Arial", bold=True, size=13, color="1F4E79")
    title_cell.alignment = center_align
    ws.row_dimensions[1].height = 22

    # Row 2: column headers
    ws.cell(row=2, column=1, value="").font = header_font
    ws.cell(row=2, column=1).fill = header_fill
    for i, period in enumerate(periods, start=2):
        cell = ws.cell(row=2, column=i, value=period)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
    ws.row_dimensions[2].height = 18

    current_row = 3

    for section in data.get("sections", []):
        # Section header
        ws.merge_cells(start_row=current_row, start_column=1,
                       end_row=current_row, end_column=1 + num_cols)
        sec_cell = ws.cell(row=current_row, column=1, value=section["name"])
        sec_cell.font = section_font
        sec_cell.fill = section_fill
        ws.row_dimensions[current_row].height = 16
        current_row += 1

        for row in section.get("rows", []):
            label_cell = ws.cell(row=current_row, column=1, value=row["label"])
            label_cell.font = subtotal_font if row.get("is_subtotal") else normal_font
            if row.get("is_subtotal"):
                label_cell.fill = subtotal_fill
                label_cell.border = thin_border

            for i, val in enumerate(row.get("values", []), start=2):
                val_cell = ws.cell(row=current_row, column=i, value=val)
                val_cell.font = subtotal_font if row.get("is_subtotal") else normal_font
                val_cell.alignment = right_align
                if row.get("is_subtotal"):
                    val_cell.fill = subtotal_fill
                    val_cell.border = thin_border

            current_row += 1

        current_row += 1  # blank row between sections

    # Column widths
    ws.column_dimensions["A"].width = 45
    for i in range(2, 2 + num_cols):
        col_letter = utils.get_column_letter(i)
        ws.column_dimensions[col_letter].width = 18

    wb.save(output_path)
