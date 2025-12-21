# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from app.core.logging import get_logger
from app.utils.json_io import atomic_write_json, read_json

log = get_logger(__name__)

FLOAT_DTYPE = np.float16 if hasattr(np, "float16") else np.float32


SCHEMA_VERSION = "2.0"


@dataclass
class ProjectPaths:
    root: Path

    @property
    def app_state_json(self) -> Path:
        return self.root / "app_state.json"

    @property
    def manifest_json(self) -> Path:
        return self.root / "manifest.json"

    @property
    def meta_json(self) -> Path:
        return self.root / "meta.json"

    @property
    def meta_log(self) -> Path:
        return self.root / "meta.log"

    @property
    def vec_text_npz(self) -> Path:
        return self.root / "vec_text_fp16.npz"

    @property
    def vec_text_delta_npz(self) -> Path:
        return self.root / "vec_text_delta_fp16.npz"

    @property
    def vec_image_npz(self) -> Path:
        return self.root / "vec_image_fp16.npz"

    @property
    def vec_image_delta_npz(self) -> Path:
        return self.root / "vec_image_delta_fp16.npz"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "cache" / "thumbs"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"


class ProjectStore:
    """專案持久化（JSON）。

    原則：
    - 版本欄位 schema_version
    - 原子寫入 + .bak
    """

    def __init__(self, project_root: Path):
        self.paths = ProjectPaths(project_root)
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- Legacy aliases ----------------
    def load_project(self) -> Dict[str, Any]:
        return self.load_app_state()

    def save_project(self, data: Dict[str, Any]) -> None:
        self.save_app_state(data)

    def load_catalog(self) -> Dict[str, Any]:
        return self.load_manifest()

    def save_catalog(self, data: Dict[str, Any]) -> None:
        self.save_manifest(data)

    def load_index(self) -> Dict[str, Any]:
        return self.load_meta()

    def save_index(self, data: Dict[str, Any]) -> None:
        self.save_meta(data)

    # ---------------- App State ----------------
    def load_app_state(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "project_name": "Local Slide Manager",
            "whitelist_dirs": [],
            "recent_queries": [],
        }
        data = read_json(self.paths.app_state_json, default)
        return self._migrate_app_state(data)

    def save_app_state(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.app_state_json, data)

    def _migrate_app_state(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {
                "schema_version": SCHEMA_VERSION,
                "project_name": "Local Slide Manager",
                "whitelist_dirs": [],
                "recent_queries": [],
            }
        if "whitelist_dirs" not in data or not isinstance(data["whitelist_dirs"], list):
            data["whitelist_dirs"] = []
        if "recent_queries" not in data or not isinstance(data["recent_queries"], list):
            data["recent_queries"] = []
        migrated: List[Any] = []
        for entry in data.get("whitelist_dirs", []):
            if isinstance(entry, str):
                path = entry.strip()
                if not path:
                    continue
                migrated.append(path)
            elif isinstance(entry, dict):
                path = str(entry.get("path", "")).strip()
                if not path:
                    continue
                migrated.append(
                    {
                        "path": path,
                        "enabled": bool(entry.get("enabled", True)),
                        "recursive": bool(entry.get("recursive", True)),
                    }
                )
        data["whitelist_dirs"] = migrated
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data

    # ---------------- Manifest ----------------
    def load_manifest(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "files": [],
            "stats": {},
        }
        data = read_json(self.paths.manifest_json, default)
        return self._migrate_manifest(data)

    def save_manifest(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.manifest_json, data)

    def _migrate_manifest(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"schema_version": SCHEMA_VERSION, "files": [], "stats": {}}
        if "files" not in data or not isinstance(data["files"], list):
            data["files"] = []
        if "stats" not in data or not isinstance(data["stats"], dict):
            data["stats"] = {}
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data

    # ---------------- Meta ----------------
    def load_meta(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "files": {},
            "slides": {},
        }
        data = read_json(self.paths.meta_json, default)
        data = self._migrate_meta(data)
        return self._apply_meta_log(data)

    def save_meta(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.meta_json, data)
        if self.paths.meta_log.exists():
            try:
                self.paths.meta_log.unlink()
            except Exception as exc:
                log.warning("清除 meta.log 失敗：%s", exc)

    def append_meta_log(self, patch: Dict[str, Any]) -> None:
        payload = {
            "ts": int(time.time()),
            "files": patch.get("files", {}),
            "slides": patch.get("slides", {}),
        }
        try:
            self.paths.meta_log.parent.mkdir(parents=True, exist_ok=True)
            with self.paths.meta_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("寫入 meta.log 失敗：%s", exc)

    def _migrate_meta(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"schema_version": SCHEMA_VERSION, "files": {}, "slides": {}}
        if "files" not in data or not isinstance(data["files"], dict):
            data["files"] = {}
        if "slides" not in data or not isinstance(data["slides"], dict):
            data["slides"] = {}
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data

    def _apply_meta_log(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.paths.meta_log.exists():
            return data
        try:
            for line in self.paths.meta_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    patch = json.loads(line)
                except Exception:
                    continue
                files_patch = patch.get("files") if isinstance(patch.get("files"), dict) else {}
                slides_patch = patch.get("slides") if isinstance(patch.get("slides"), dict) else {}
                data["files"].update(files_patch)
                data["slides"].update(slides_patch)
        except Exception as exc:
            log.warning("讀取 meta.log 失敗：%s", exc)
        return data

    # ---------------- Vectors ----------------
    def load_text_vectors(self) -> Dict[str, np.ndarray]:
        return self._load_vectors(self.paths.vec_text_npz, self.paths.vec_text_delta_npz)

    def load_image_vectors(self) -> Dict[str, np.ndarray]:
        return self._load_vectors(self.paths.vec_image_npz, self.paths.vec_image_delta_npz)

    def append_text_vectors(self, vectors: Dict[str, np.ndarray]) -> None:
        self._append_vectors(self.paths.vec_text_delta_npz, vectors)

    def append_image_vectors(self, vectors: Dict[str, np.ndarray]) -> None:
        self._append_vectors(self.paths.vec_image_delta_npz, vectors)

    def compact_text_vectors(self) -> None:
        self._compact_vectors(self.paths.vec_text_npz, self.paths.vec_text_delta_npz)

    def compact_image_vectors(self) -> None:
        self._compact_vectors(self.paths.vec_image_npz, self.paths.vec_image_delta_npz)

    def _load_vectors(self, snapshot_path: Path, delta_path: Path) -> Dict[str, np.ndarray]:
        vectors = self._load_npz_map(snapshot_path)
        delta = self._load_npz_map(delta_path)
        if delta:
            vectors.update(delta)
        return vectors

    def _append_vectors(self, delta_path: Path, vectors: Dict[str, np.ndarray]) -> None:
        if not vectors:
            return
        existing = self._load_npz_map(delta_path)
        existing.update({k: np.asarray(v, dtype=FLOAT_DTYPE) for k, v in vectors.items()})
        self._save_npz_map(delta_path, existing)

    def _compact_vectors(self, snapshot_path: Path, delta_path: Path) -> None:
        snapshot = self._load_npz_map(snapshot_path)
        delta = self._load_npz_map(delta_path)
        if not delta:
            return
        snapshot.update(delta)
        self._save_npz_map(snapshot_path, snapshot)
        try:
            delta_path.unlink()
        except Exception as exc:
            log.warning("清除向量 delta 失敗：%s", exc)

    def _load_npz_map(self, path: Path) -> Dict[str, np.ndarray]:
        if not path.exists():
            return {}
        try:
            with np.load(path, allow_pickle=False) as data:
                return {k: data[k] for k in data.files}
        except Exception as exc:
            log.warning("讀取向量檔失敗：%s (%s)", path, exc)
            return {}

    def _save_npz_map(self, path: Path, data: Dict[str, np.ndarray]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp.npz")
            payload = {k: np.asarray(v, dtype=FLOAT_DTYPE) for k, v in data.items()}
            saver = getattr(np, "savez_compressed", None)
            if saver is None:
                log.warning("numpy 缺少 savez_compressed，改用未壓縮 npz：%s", path)
                np.savez(tmp, **payload)
            else:
                saver(tmp, **payload)
            tmp.replace(path)
        except Exception as exc:
            log.warning("寫入向量檔失敗：%s (%s)", path, exc)
