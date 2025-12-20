# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from app.core.logging import get_logger

log = get_logger(__name__)


def read_pptx_metadata(pptx_path: Path) -> Dict[str, Any]:
    """讀取 PPTX metadata（core properties + slide_count）。"""
    from pptx import Presentation

    try:
        prs = Presentation(str(pptx_path))
        props = prs.core_properties
        core_properties = {
            "title": props.title,
            "author": props.author,
            "created_epoch": int(props.created.timestamp()) if props.created else None,
            "modified_epoch": int(props.modified.timestamp()) if props.modified else None,
        }
        return {
            "slide_count": len(prs.slides),
            "core_properties": core_properties,
        }
    except Exception as e:
        log.warning("讀取 PPTX metadata 失敗：%s (%s)", pptx_path, e)
        return {"slide_count": None, "core_properties": None}
