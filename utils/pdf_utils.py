import logging
import subprocess
import base64
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple


def _validate_pdf_path(pdf_path: str) -> Path:
    """Validate and resolve a PDF path to prevent command-injection issues."""
    p = Path(pdf_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not p.is_file():
        raise ValueError(f"Not a regular file: {pdf_path}")
    return p


def get_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF using pdfinfo."""
    p = _validate_pdf_path(pdf_path)
    result = subprocess.run(
        ["pdfinfo", str(p)], capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    raise RuntimeError("Could not determine page count")


def _ocr_image(image_path: str) -> str:
    """Run OCR on an image and return the extracted text."""
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(image_path))
        return text or ""
    except Exception:
        logging.warning("OCR failed for %s", image_path, exc_info=True)
        return ""


def _detect_inverted(image) -> bool:
    """Detect if an image has inverted colors (white text on dark background)."""
    from PIL import ImageStat
    stat = ImageStat.Stat(image)
    # For grayscale/RGB, compute average brightness
    if image.mode in ("L", "1"):
        avg_brightness = stat.mean[0]
    else:
        avg_brightness = sum(stat.mean[:3]) / 3
    # If average brightness is very low, the page is mostly dark → likely inverted
    return avg_brightness < 80


def _detect_rotation_tesseract(image_path: str) -> tuple[int, float]:
    """
    Detect image rotation using Tesseract OSD.

    Returns:
        (angle, confidence) where angle is 0/90/180/270 and confidence is the
        OSD confidence score.  A confidence < 2.0 is considered unreliable.
    """
    try:
        import pytesseract
        from PIL import Image
        osd = pytesseract.image_to_osd(Image.open(image_path), output_type=pytesseract.Output.DICT)
        angle = osd.get("rotate", 0)
        conf = osd.get("orientation_conf", 0.0)
        return angle, conf
    except Exception:
        return 0, 0.0


def _auto_correct_orientation(image_path: str) -> Tuple[str, str]:
    """
    Auto-correct image orientation and inversion.

    Very conservative: only applies rotation when Tesseract OSD reports
    high confidence (>= 2.5).  Financial statements often confuse OSD, so
    low-confidence results are ignored and the original orientation is kept.

    Returns:
        (corrected_image_path, description_of_changes)
    """
    from PIL import Image, ImageOps

    img = Image.open(image_path)
    changes: list[str] = []

    # 1. Invert detection (brightness heuristic — only very dark pages)
    if _detect_inverted(img):
        img = ImageOps.invert(img.convert("RGB")).convert(img.mode)
        changes.append("inverted colors")

    # 2. Rotation detection — conservative, high-confidence only
    angle, confidence = _detect_rotation_tesseract(image_path)
    if angle != 0 and confidence >= 2.5:
        img = img.rotate(-angle, expand=True)
        changes.append(f"rotated {angle}° (conf={confidence:.1f})")
    elif angle != 0:
        logging.info(
            f"[orientation] Ignored rotation {angle}° (low confidence {confidence:.1f})"
        )

    if changes:
        corrected_path = str(Path(image_path).with_suffix("")) + "_corrected.jpg"
        img.save(corrected_path, "JPEG", quality=95)
        logging.info(f"[orientation] Corrected {image_path}: {', '.join(changes)}")
        return corrected_path, ", ".join(changes)

    return image_path, ""


def rasterize_page(pdf_path: str, page_num: int, out_prefix: str, dpi: int = 150, auto_rotate: bool = True) -> str:
    """
    Rasterize a single PDF page (1-indexed) and return the image path.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number to rasterize (1-indexed)
        out_prefix: Output file prefix (without extension)
        dpi: Resolution in dots per inch
        auto_rotate: Whether to auto-detect and correct orientation/inversion

    Returns:
        Path to the generated JPEG image (corrected if needed)
    """
    p = _validate_pdf_path(pdf_path)
    subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi),
         "-f", str(page_num), "-l", str(page_num),
         str(p), out_prefix],
        check=True, capture_output=True
    )
    # pdftoppm zero-pads based on total page count – find the file
    matches = sorted(Path(out_prefix).parent.glob(f"{Path(out_prefix).name}-*.jpg"))
    if not matches:
        raise FileNotFoundError(f"No rasterized image found for page {page_num}")
    img_path = str(matches[-1])

    if auto_rotate:
        corrected_path, changes = _auto_correct_orientation(img_path)
        if changes:
            print(f"🔄 Auto-corrected page {page_num}: {changes}")
        return corrected_path

    return img_path


def image_to_base64(image_path: str) -> str:
    """Read an image file and return its base64-encoded string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def rasterize_page_to_png(pdf_path: str, page_num: int, dpi: int = 150, auto_rotate: bool = True) -> Optional[bytes]:
    """
    Rasterize a PDF page to PNG bytes for display in Streamlit.

    Args:
        pdf_path: Path to the PDF file
        page_num: Page number (1-indexed)
        dpi: Resolution
        auto_rotate: Whether to auto-detect and correct orientation/inversion

    Returns:
        PNG bytes or None if failed
    """
    import tempfile

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
            if not matches:
                return None

            img_path = str(matches[0])
            if auto_rotate:
                corrected_path, changes = _auto_correct_orientation(img_path)
                if changes:
                    print(f"🔄 Auto-corrected page {page_num}: {changes}")
                img_path = corrected_path

            with open(img_path, "rb") as f:
                return f.read()
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
