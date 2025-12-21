# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from app.core.logging import get_logger
from app.services.project_store import ProjectStore
from app.services.metadata_service import read_pptx_metadata

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

    def _normalize_dir(self, dir_path: str) -> str:
        return str(Path(dir_path).resolve())

    def _load_whitelist(self) -> List[Dict[str, Any]]:
        proj = self.store.load_app_state()
        dirs = proj.get("whitelist_dirs", [])
        normalized: List[Dict[str, Any]] = []
        for entry in dirs:
            if isinstance(entry, str):
                path = entry.strip()
                if not path:
                    continue
                normalized.append({"path": path, "enabled": True, "recursive": True})
            elif isinstance(entry, dict) and entry.get("path"):
                normalized.append(entry)
        return normalized

    def _save_whitelist(self, dirs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        proj = self.store.load_app_state()
        proj["whitelist_dirs"] = dirs
        self.store.save_app_state(proj)
        return dirs

    def get_whitelist_dirs(self) -> List[str]:
        return [str(d.get("path")) for d in self._load_whitelist()]

    def get_whitelist_entries(self) -> List[Dict[str, Any]]:
        return list(self._load_whitelist())

    def add_whitelist_dir(self, dir_path: str) -> List[str]:
        p = self._normalize_dir(dir_path)
        dirs = self._load_whitelist()
        if not any(d.get("path") == p for d in dirs):
            dirs.append({"path": p, "enabled": True, "recursive": True})
        self._save_whitelist(dirs)
        return [d["path"] for d in dirs]

    def remove_whitelist_dir(self, dir_path: str) -> List[str]:
        p = self._normalize_dir(dir_path)
        dirs = [d for d in self._load_whitelist() if d.get("path") != p]
        self._save_whitelist(dirs)
        return [d["path"] for d in dirs]

    def update_whitelist_dir(
        self,
        dir_path: str,
        *,
        enabled: Optional[bool] = None,
        recursive: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        p = self._normalize_dir(dir_path)
        dirs = self._load_whitelist()
        for entry in dirs:
            if entry.get("path") == p:
                if enabled is not None:
                    entry["enabled"] = bool(enabled)
                if recursive is not None:
                    entry["recursive"] = bool(recursive)
        return self._save_whitelist(dirs)

    def set_whitelist_recursive(self, dir_path: str, recursive: bool) -> List[Dict[str, Any]]:
        return self.update_whitelist_dir(dir_path, recursive=recursive)

    def set_whitelist_enabled(self, dir_path: str, enabled: bool) -> List[Dict[str, Any]]:
        return self.update_whitelist_dir(dir_path, enabled=enabled)

    def scan(
        self,
        *,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_every: int = 10,
    ) -> Dict[str, Any]:
        """掃描白名單目錄，更新 catalog.json。"""
        whitelist = self._load_whitelist()
        existing = self.store.load_manifest().get("files", [])
        by_path = {e.get("abs_path"): e for e in existing if isinstance(e, dict) and e.get("abs_path")}
        touched_paths = set()
        scan_errors: List[Dict[str, Any]] = []

        files: List[Dict[str, Any]] = []
        batch: List[Dict[str, Any]] = []
        scanned_count = 0
        for entry in whitelist:
            if not entry.get("enabled", True):
                continue
            root = Path(str(entry.get("path")))
            if not root.exists():
                scan_errors.append(
                    {
                        "code": "PATH_NOT_FOUND",
                        "path": str(root),
                        "message": "白名單路徑不存在，已略過",
                    }
                )
                log.warning("[PATH_NOT_FOUND] 白名單路徑不存在：%s", root)
                continue
            if not os.access(root, os.R_OK):
                scan_errors.append(
                    {
                        "code": "PERMISSION_DENIED",
                        "path": str(root),
                        "message": "白名單路徑權限不足，已略過",
                    }
                )
                log.warning("[PERMISSION_DENIED] 白名單路徑權限不足：%s", root)
                continue
            walker = root.rglob("*.pptx") if entry.get("recursive", True) else root.glob("*.pptx")
            try:
                for path in walker:
                    if path.name.startswith("~$"):
                        continue
                    try:
                        st = path.stat()
                        abs_path = str(path.resolve())
                        prev = by_path.get(abs_path)
                        touched_paths.add(abs_path)

                        # 快速判斷是否需要重算 hash
                        mtime = int(st.st_mtime)
                        size = int(st.st_size)
                        file_hash = None
                        if prev and int(prev.get("size", -1)) == size and int(prev.get("modified_time", -1)) == mtime:
                            file_hash = prev.get("file_hash")
                        if not file_hash:
                            file_hash = _sha256_file(path)

                        file_id = hashlib.sha256(abs_path.encode("utf-8", errors="ignore")).hexdigest()
                        core_props = prev.get("core_properties") if prev else None
                        slide_count = prev.get("slide_count") if prev else None
                        if prev and (prev.get("metadata_mtime") != mtime or prev.get("metadata_size") != size):
                            core_props = None
                            slide_count = None
                        if core_props is None or slide_count is None:
                            try:
                                meta = read_pptx_metadata(path)
                                core_props = meta.get("core_properties")
                                slide_count = meta.get("slide_count")
                            except Exception as e:
                                log.warning("讀取 metadata 失敗：%s (%s)", path, e)

                        entry = {
                            "file_id": file_id,
                            "abs_path": abs_path,
                            "filename": path.name,
                            "size": size,
                            "modified_time": mtime,
                            "file_hash": file_hash,
                            "metadata_size": size,
                            "metadata_mtime": mtime,
                            "core_properties": core_props,
                            "slide_count": slide_count,
                            "indexed": bool(prev.get("indexed")) if prev else False,
                            "indexed_at": prev.get("indexed_at") if prev else None,
                            "slides_count": int(prev.get("slides_count", 0)) if prev else 0,
                            "last_error": prev.get("last_error") if prev else None,
                            "last_index_summary": prev.get("last_index_summary") if prev else None,
                            "index_mode": prev.get("index_mode") if prev else None,
                            "missing": False,
                        }
                        files.append(entry)
                        if on_progress:
                            scanned_count += 1
                            batch.append(entry)
                            if progress_every > 0 and len(batch) >= progress_every:
                                on_progress(
                                    {
                                        "count": scanned_count,
                                        "batch": list(batch),
                                    }
                                )
                                batch.clear()
                    except PermissionError as e:
                        scan_errors.append(
                            {
                                "code": "PERMISSION_DENIED",
                                "path": str(path),
                                "message": "掃描檔案權限不足，已略過",
                            }
                        )
                        log.warning("[PERMISSION_DENIED] 掃描檔案失敗：%s (%s)", path, e)
                    except Exception as e:
                        log.warning("掃描檔案失敗：%s (%s)", path, e)
            except PermissionError as e:
                scan_errors.append(
                    {
                        "code": "PERMISSION_DENIED",
                        "path": str(root),
                        "message": "白名單路徑權限不足，已略過",
                    }
                )
                log.warning("[PERMISSION_DENIED] 讀取目錄失敗：%s (%s)", root, e)

        if on_progress and batch:
            on_progress(
                {
                    "count": scanned_count,
                    "batch": list(batch),
                }
            )

        # 標記 missing
        for prev in existing:
            if not isinstance(prev, dict):
                continue
            abs_path = prev.get("abs_path")
            if abs_path and abs_path not in touched_paths:
                prev_entry = dict(prev)
                prev_entry["missing"] = True
                files.append(prev_entry)

        def _sort_key(item: Dict[str, Any]) -> tuple:
            mtime = item.get("modified_time")
            safe_mtime = int(mtime) if isinstance(mtime, (int, float, str)) and str(mtime).isdigit() else 0
            return (-safe_mtime, item.get("filename", ""), item.get("abs_path", ""))

        out = {
            "schema_version": self.store.load_manifest().get("schema_version", "1.0"),
            "files": sorted(files, key=_sort_key),
            "scanned_at": int(time.time()),
            "whitelist_dirs": whitelist,
            "scan_errors": scan_errors,
            "stats": {},
        }
        self.store.save_manifest(out)
        return out

    def mark_indexed(
        self,
        abs_path: str,
        slides_count: int,
        *,
        text_indexed_count: Optional[int] = None,
        image_indexed_count: Optional[int] = None,
        bm25_indexed_count: Optional[int] = None,
        index_mode: Optional[str] = None,
    ) -> None:
        cat = self.store.load_manifest()
        files = cat.get("files", [])
        now = int(time.time())
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = True
                e["indexed_at"] = now
                e["slides_count"] = int(slides_count)
                e["last_index_summary"] = {
                    "slides_ok_text": int(text_indexed_count or 0),
                    "slides_ok_image": int(image_indexed_count or 0),
                    "slides_ok_bm25": int(bm25_indexed_count or 0),
                }
                e["index_mode"] = index_mode
                e["last_error"] = None
        cat["files"] = files
        self.store.save_manifest(cat)

    def mark_extracted(
        self,
        abs_path: str,
        slides_count: int,
        *,
        index_mtime_epoch: Optional[int] = None,
    ) -> None:
        cat = self.store.load_manifest()
        files = cat.get("files", [])
        now = int(time.time())
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = True
                e["indexed_at"] = now
                e["slides_count"] = int(slides_count)
                e["index_mode"] = "none"
                e["last_error"] = None
        cat["files"] = files
        self.store.save_manifest(cat)

    def mark_unindexed(self, abs_path: str) -> None:
        cat = self.store.load_manifest()
        files = cat.get("files", [])
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = False
                e["indexed_at"] = None
                e["slides_count"] = 0
                e["last_index_summary"] = {
                    "slides_ok_text": 0,
                    "slides_ok_image": 0,
                    "slides_ok_bm25": 0,
                }
                e["index_mode"] = "none"
                e["last_error"] = None
        cat["files"] = files
        self.store.save_manifest(cat)

    def mark_index_error(self, abs_path: str, code: str, message: str) -> None:
        cat = self.store.load_manifest()
        files = cat.get("files", [])
        now = int(time.time())
        for e in files:
            if isinstance(e, dict) and e.get("abs_path") == abs_path:
                e["indexed"] = False
                e["indexed_at"] = None
                e["slides_count"] = 0
                e["last_index_summary"] = {
                    "slides_ok_text": 0,
                    "slides_ok_image": 0,
                    "slides_ok_bm25": 0,
                }
                e["index_mode"] = "none"
                e["last_error"] = {"code": code, "message": message, "time": now}
        cat["files"] = files
        self.store.save_manifest(cat)

    def clear_missing_files(self) -> int:
        cat = self.store.load_manifest()
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]
        kept = [f for f in files if not f.get("missing")]
        removed = len(files) - len(kept)
        cat["files"] = kept
        self.store.save_manifest(cat)
        return removed
