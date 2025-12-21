# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List


def classify_doc_status(
    entry: Dict[str, Any],
    *,
    slides: List[Dict[str, Any]],
    meta_file: Dict[str, Any] | None,
) -> str:
    if entry.get("missing"):
        return "missing"
    if entry.get("last_error"):
        return "error"
    if not meta_file:
        return "pending"
    mtime = int(entry.get("modified_time") or 0)
    last_text = int(meta_file.get("last_text_extract_at") or 0)
    last_image = int(meta_file.get("last_image_index_at") or 0)
    if mtime > last_text or mtime > last_image:
        return "stale"

    slide_count = entry.get("slide_count")
    try:
        slide_total = int(slide_count) if slide_count is not None else len(slides)
    except Exception:
        slide_total = len(slides)
    if slide_total <= 0:
        return "pending"

    flags = [s.get("flags", {}) for s in slides if isinstance(s.get("flags"), dict)]
    if not flags:
        return "pending"
    text_vec = sum(1 for f in flags if f.get("has_text_vec"))
    image_vec = sum(1 for f in flags if f.get("has_image_vec"))
    any_done = sum(1 for f in flags if f.get("has_text") or f.get("has_image") or f.get("has_bm25"))
    if text_vec >= slide_total and image_vec >= slide_total:
        return "indexed"
    if any_done > 0 or text_vec > 0 or image_vec > 0:
        return "partial"
    return "pending"


STATUS_LABELS = {
    "pending": "未處理",
    "stale": "已擷取",
    "partial": "部分索引",
    "indexed": "已索引",
    "error": "未處理",
    "missing": "未處理",
}
