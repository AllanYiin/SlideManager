# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.utils.json_io import atomic_write_json, read_json

log = get_logger(__name__)


SCHEMA_VERSION = "1.0"


@dataclass
class ProjectPaths:
    root: Path

    @property
    def project_json(self) -> Path:
        return self.root / "project.json"

    @property
    def catalog_json(self) -> Path:
        return self.root / "catalog.json"

    @property
    def index_json(self) -> Path:
        return self.root / "index.json"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "thumbs"

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

    # ---------------- Project (global) ----------------
    def load_project(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "project_name": "Local Slide Manager",
            "whitelist_dirs": [],
        }
        data = read_json(self.paths.project_json, default)
        return self._migrate_project(data)

    def save_project(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.project_json, data)

    def _migrate_project(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {
                "schema_version": SCHEMA_VERSION,
                "project_name": "Local Slide Manager",
                "whitelist_dirs": [],
            }
        # v1.0 無遷移
        if "whitelist_dirs" not in data or not isinstance(data["whitelist_dirs"], list):
            data["whitelist_dirs"] = []
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data

    # ---------------- Catalog ----------------
    def load_catalog(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "files": [],
        }
        data = read_json(self.paths.catalog_json, default)
        return self._migrate_catalog(data)

    def save_catalog(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.catalog_json, data)

    def _migrate_catalog(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"schema_version": SCHEMA_VERSION, "files": []}
        if "files" not in data or not isinstance(data["files"], list):
            data["files"] = []
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data

    # ---------------- Index ----------------
    def load_index(self) -> Dict[str, Any]:
        default = {
            "schema_version": SCHEMA_VERSION,
            "embedding": {
                "text_model": "text-embedding-3-large",
                "text_dim": 3072,
                "image_dim": 2048,
                "concat_dim": 5120,
                "vector_encoding": "base64_f32",
            },
            "slides": [],
        }
        data = read_json(self.paths.index_json, default)
        return self._migrate_index(data)

    def save_index(self, data: Dict[str, Any]) -> None:
        data = dict(data)
        data["schema_version"] = SCHEMA_VERSION
        atomic_write_json(self.paths.index_json, data)

    def _migrate_index(self, data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return self.load_index()
        if "slides" not in data or not isinstance(data["slides"], list):
            data["slides"] = []
        if "embedding" not in data or not isinstance(data["embedding"], dict):
            data["embedding"] = self.load_index()["embedding"]
        data["schema_version"] = str(data.get("schema_version", SCHEMA_VERSION))
        return data
