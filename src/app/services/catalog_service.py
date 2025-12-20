# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.services.project_store import ProjectStore

log = get_logger(__name__)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CatalogService:
    """檔案目錄白名單與掃描。

    規格：只處理白名單目錄底下的 .pptx。
    """

    def __init__(self, store: ProjectStore):
        self.store = store

    def get_whitelist_dirs(self) -> List[str]:
        proj = self.store.load_project()
        return list(proj.get("whitelist_dirs", []))

    def add_whitelist_dir(self, dir_path: str) -> List[str]:
        proj = self.store.load_project()
        dirs = list(proj.get("whitelist_dirs", []))
        p = str(Path(dir_path).resolve())
        if p not in dirs:
            dirs.append(p)
        proj["whitelist_dirs"] = dirs
        self.store.save_project(proj)
        return dirs

    def remove_whitelist_dir(self, dir_path: str) -> List[str]:
        proj = self.store.load_project()
        dirs = [d for d in list(proj.get("whitelist_dirs", [])) if d != dir_path]
        proj["whitelist_dirs"] = dirs
        self.store.save_project(proj)
        return dirs

    def scan(self) -> Dict[str, Any]:
        """掃描白名單目錄，更新 catalog.json。"""
        whitelist = self.get_whitelist_dirs()
        existing = self.store.load_catalog().get("files", [])
        by_path = {e.get("abs_path"): e for e in existing if isinstance(e, dict) and e.get("abs_path")}

        files: List[Dict[str, Any]] = []
        for d in whitelist:
            root = Path(d)
            if not root.exists():
                continue
            for path in root.rglob("*.pptx"):
                try:
                    st = path.stat()
                    abs_path = str(path.resolve())
                    prev = by_path.get(abs_path)

                    # 快速判斷是否需要重算 hash
                    mtime = int(st.st_mtime)
                    size = int(st.st_size)
                    file_hash = None
                    if prev and int(prev.get("size", -1)) == size and int(prev.get("modified_time", -1)) == mtime:
                        file_hash = prev.get("file_hash")
                    if not file_hash:
                        file_hash = _sha256_file(path)

                    file_id = hashlib.sha256(abs_path.encode("utf-8", errors="ignore")).hexdigest()
                    entry = {
                        "file_id": file_id,
                        "abs_path": abs_path,
                        "filename": path.name,
                        "size": size,
                        "modified_time": mtime,
                        "file_hash": file_hash,
                        "indexed": bool(prev.get("indexed")) if prev else False,
                        "indexed_at": prev.get("indexed_at") if prev else None,
                        "slides_count": int(prev.get("slides_count", 0)) if prev else 0,
                    }
                    files.append(entry)
                except Exception as e:
                    log.warning("掃描檔案失敗：%s (%s)", path, e)

        out = {
            "schema_version": self.store.load_catalog().get("schema_version", "1.0"),
            "files": sorted(files, key=lambda x: (x.get("filename", ""), x.get("abs_path", ""))),
            "scanned_at": int(time.time()),
        }
        self.store.save_catalog(out)
        return out

    def mark_indexed(self, abs_path: str, slides_count: int) -> None:
        cat = self.store.load_catalog()
        files = cat.get("files", [])
        now = int(time.time())
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = True
                e["indexed_at"] = now
                e["slides_count"] = int(slides_count)
        cat["files"] = files
        self.store.save_catalog(cat)

    def mark_unindexed(self, abs_path: str) -> None:
        cat = self.store.load_catalog()
        files = cat.get("files", [])
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = False
                e["indexed_at"] = None
                e["slides_count"] = 0
        cat["files"] = files
        self.store.save_catalog(cat)
