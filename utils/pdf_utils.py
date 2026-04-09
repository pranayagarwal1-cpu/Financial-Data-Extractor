import subprocess
import base64
from pathlib import Path


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
