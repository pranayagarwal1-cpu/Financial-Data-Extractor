import subprocess
import base64
from pathlib import Path
from typing import Optional, List


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF using pdfinfo."""
    result = subprocess.run(
        ["pdfinfo", pdf_path], capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    raise RuntimeError("Could not determine page count")


def rasterize_page(pdf_path: str, page_num: int, out_prefix: str, dpi: int = 150) -> str:
    """
    Rasterize a single PDF page (1-indexed) and return the image path.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number to rasterize (1-indexed)
        out_prefix: Output file prefix (without extension)
        dpi: Resolution in dots per inch

    Returns:
        Path to the generated JPEG image
    """
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi),
         "-f", str(page_num), "-l", str(page_num),
         pdf_path, out_prefix],
        check=True, capture_output=True
    )
    # pdftoppm zero-pads based on total page count – find the file
    matches = sorted(Path(out_prefix).parent.glob(f"{Path(out_prefix).name}-*.jpg"))
    if not matches:
        raise FileNotFoundError(f"No rasterized image found for page {page_num}")
    return str(matches[-1])


def image_to_base64(image_path: str) -> str:
    """Read an image file and return its base64-encoded string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def rasterize_page_to_png(pdf_path: str, page_num: int, dpi: int = 150) -> Optional[bytes]:
    """
    Rasterize a PDF page to PNG bytes for display in Streamlit.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number (1-indexed)
        dpi: Resolution

    Returns:
        PNG bytes or None if failed
    """
    import tempfile
    import os

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_prefix = Path(tmpdir) / "page"
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi),
                 "-f", str(page_num), "-l", str(page_num),
                 pdf_path, out_prefix],
                check=True, capture_output=True
            )

            # Find the output file
            matches = sorted(Path(tmpdir).glob("page-*.png"))
            if matches:
                with open(matches[0], "rb") as f:
                    return f.read()
            return None
    except Exception as e:
        print(f"Error rasterizing page: {e}")
        return None


def find_statement_pages(pdf_path: str, statement_pages: dict) -> dict:
    """
    Map statement types to their actual page numbers in the PDF.

    Args:
        pdf_path: Path to the PDF
        statement_pages: Dict mapping StatementType to list of page indices (0-based)

    Returns:
        Dict mapping StatementType to list of 1-indexed page numbers
    """
    result = {}
    for stmt_type, pages in statement_pages.items():
        # Convert to 1-indexed for display
        result[stmt_type] = [p + 1 for p in pages]
    return result
