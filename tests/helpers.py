from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Iterable, Sequence


def ensure_src_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return root


def build_presentation_xml(aspect: str) -> str:
    if aspect == "4:3":
        cx, cy = "9144000", "6858000"
    elif aspect == "16:9":
        cx, cy = "12192000", "6858000"
    else:
        return """<?xml version='1.0' encoding='UTF-8'?>
<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"></p:presentation>"""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">"
        f"<p:sldSz cx=\"{cx}\" cy=\"{cy}\"/>"
        "</p:presentation>"
    )


def build_slide_xml(texts: Sequence[str]) -> str:
    items = "".join(f"<a:t>{t}</a:t>" for t in texts)
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" "
        "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\">"
        f"{items}"
        "</p:sld>"
    )


def build_pptx(
    path: Path,
    slides: Iterable[str],
    aspect: str = "4:3",
    include_presentation: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        if include_presentation:
            zf.writestr("ppt/presentation.xml", build_presentation_xml(aspect))
        for idx, slide_xml in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)


def build_pdf(path: Path, pages: int = 1, width: float = 320, height: float = 240) -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()


def load_schema_sql(root: Path) -> str:
    return (root / "src" / "app" / "backend_daemon" / "schema.sql").read_text(
        encoding="utf-8"
    )
