"""OCR for scanned BRD pages: recognise the text and lay it back onto the page as invisible text, so the
ordinary PDF parser (headings by size, lists, tables) handles a scan like any other document.

Runs on CPU with RapidOCR (ONNX); nothing leaves the machine. Recognition is imperfect, so the parse
carries a warning and every low-confidence line is counted."""

import logging
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

DPI = 300
MIN_CONFIDENCE = 0.7
_engine: Any = None


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def page_has_text(page: Any) -> bool:
    return len(page.get_text("text").strip()) >= 20


def ocr_into_page(page: Any) -> tuple[int, int]:
    """Add the recognised text of a page as invisible text. Returns (lines kept, lines dropped)."""
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    result = _get_engine()(img)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or scores is None:
        return 0, 0
    scale = 72 / DPI
    kept = dropped = 0
    for box, text, score in zip(boxes, texts, scores, strict=False):
        text = str(text).strip()
        if not text:
            continue
        if float(score) < MIN_CONFIDENCE:
            dropped += 1
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x0, x1, y0, y1 = (
            min(xs) * scale,
            max(xs) * scale,
            min(ys) * scale,
            max(ys) * scale,
        )
        height = max(4.0, y1 - y0)
        size = height * 0.78  # glyph height is a little under the box height
        # fit the text to the recognised width so line bboxes (and block merging) behave
        width_at_1 = pymupdf.get_text_length(text, fontname="helv", fontsize=1)
        if width_at_1 > 0:
            size = min(size, (x1 - x0) / width_at_1 * 1.0) if (x1 - x0) > 0 else size
        page.insert_text(
            (x0, y1 - height * 0.2),
            text,
            fontsize=max(3.0, size),
            fontname="helv",
            render_mode=3,
        )
        kept += 1
    return kept, dropped


def image_lines(data: bytes) -> list[str]:
    """The text of an image (a diagram, a screenshot) in reading order, confident lines only."""
    import numpy as np
    from PIL import Image

    img = np.array(Image.open(BytesIO(data)).convert("RGB"))
    result = _get_engine()(img)
    boxes, texts, scores = (
        getattr(result, "boxes", None),
        getattr(result, "txts", None),
        getattr(result, "scores", None),
    )
    if boxes is None or texts is None or scores is None:
        return []
    items = []
    for box, text, score in zip(boxes, texts, scores, strict=False):
        text = str(text).strip()
        if len(text) < 2 or float(score) < MIN_CONFIDENCE:
            continue
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        items.append((sum(ys) / 4, min(xs), text, max(ys) - min(ys)))
    # rows: boxes whose vertical centres are within half a line height belong together
    items.sort()
    rows: list[list[tuple[float, float, str, float]]] = []
    for it in items:
        if rows and abs(it[0] - rows[-1][-1][0]) < max(8.0, it[3] * 0.6):
            rows[-1].append(it)
        else:
            rows.append([it])
    return [
        " ".join(t for _y, _x, t, _h in sorted(r, key=lambda i: i[1])) for r in rows
    ]
