# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict


def classify_doc_status(entry: Dict[str, Any]) -> str:
    if entry.get("missing"):
        return "missing"
    status = entry.get("index_status") if isinstance(entry.get("index_status"), dict) else {}
    if status.get("last_error"):
        return "error"
    indexed = bool(status.get("indexed")) if status else bool(entry.get("indexed"))
    if not indexed:
        return "pending"
    mtime = int(entry.get("modified_time") or 0)
    index_mtime = int(status.get("index_mtime_epoch") or 0)
    slide_count = entry.get("slide_count")
    index_slide_count = status.get("index_slide_count")
    if mtime > index_mtime:
        return "stale"
    if slide_count is not None and index_slide_count is not None:
        try:
            if int(slide_count) != int(index_slide_count):
                return "stale"
        except Exception:
            return "stale"
    if status:
        text_indexed = status.get("text_indexed")
        image_indexed = status.get("image_indexed")
        if text_indexed is True and image_indexed is True:
            return "indexed"
        if text_indexed is False and image_indexed is False:
            return "pending"
        if text_indexed is not None or image_indexed is not None:
            return "partial"
        index_mode = status.get("index_mode")
        if index_mode in ("text", "image"):
            return "partial"
        index_slide_count = status.get("index_slide_count")
        text_count = status.get("text_indexed_count")
        image_count = status.get("image_indexed_count")
        bm25_count = status.get("bm25_indexed_count")
        if (
            isinstance(index_slide_count, int)
            and index_slide_count > 0
            and isinstance(text_count, int)
            and isinstance(image_count, int)
            and text_count >= index_slide_count
            and image_count >= index_slide_count
        ):
            return "indexed"
        if any(
            isinstance(count, int) and count > 0
            for count in (text_count, image_count, bm25_count)
        ):
            return "partial"
    return "indexed"


STATUS_LABELS = {
    "pending": "未處理",
    "stale": "已擷取",
    "partial": "部分索引",
    "indexed": "已索引",
    "error": "未處理",
    "missing": "未處理",
}
