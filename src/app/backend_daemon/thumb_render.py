from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Literal, Tuple

from PIL import Image

Aspect = Literal["4:3", "16:9", "unknown"]


def render_pdf_page_to_thumb(
    pdf_path: Path, page_index0: int, out_path: Path, width: int, height: int
) -> None:
    if importlib.util.find_spec("fitz") is None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), color="white").save(out_path)
        return

    fitz = importlib.import_module("fitz")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_index0)
        rect = page.rect
        sx = width / rect.width
        sy = height / rect.height
        mat = fitz.Matrix(sx, sy)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(out_path))
    finally:
        doc.close()


def thumb_size(
    aspect: Aspect, width: int, h_4_3: int, h_16_9: int
) -> Tuple[int, int]:
    if aspect == "4:3":
        return width, h_4_3
    if aspect == "16:9":
        return width, h_16_9
    return width, h_16_9
