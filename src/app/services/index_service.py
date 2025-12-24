# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

from app.services.catalog_service import CatalogService
from app.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


@dataclass
class IndexProgress:
    stage: str
    current: int
    total: int
    message: str


class _DaemonStatus:
    def status(self) -> Dict[str, Any]:
        return {
            "available": False,
            "active": False,
            "status": {"daemon": "未接線"},
            "last_message": "後台已改為 daemon，UI 尚未接線。",
        }


class _DaemonImageEmbedder(_DaemonStatus):
    def enabled_onnx(self) -> bool:
        return False

    def embed_image_bytes(self, _data: bytes, *, dim: int = 0) -> None:
        return None


class IndexService:
    def __init__(self, store: ProjectStore, catalog: CatalogService, api_key: Optional[str]):
        self.store = store
        self.catalog = catalog
        self.api_key = api_key
        self.renderer = _DaemonStatus()
        self.image_embedder = _DaemonImageEmbedder()
        self._daemon_base = "http://127.0.0.1:5123"

    @staticmethod
    def _resolve_index_mode(update_text: bool, update_image: bool) -> str:
        if update_text and update_image:
            return "both"
        if update_text:
            return "text_only"
        if update_image:
            return "image_only"
        return "none"

    def compute_needed_files(self) -> List[Dict[str, Any]]:
        try:
            manifest = self.store.load_manifest()
            files = [e for e in manifest.get("files", []) if isinstance(e, dict)]
            return [f for f in files if not f.get("missing")]
        except Exception as exc:
            logger.exception("讀取需要索引的檔案失敗: %s", exc)
            return []

    def rebuild_for_files(
        self,
        files: List[Dict[str, Any]],
        *,
        on_progress: Optional[Callable[[IndexProgress], None]] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
        pause_flag: Optional[Callable[[], bool]] = None,
        update_text: bool = True,
        update_image: bool = True,
    ) -> tuple[int, str]:
        if cancel_flag and cancel_flag():
            return 1, "已取消"
        try:
            payload = {
                "library_root": str(self.store.root),
                "options": {
                    "enable_text": update_text,
                    "enable_thumb": update_image,
                    "enable_text_vec": update_text,
                    "enable_img_vec": update_image,
                    "enable_bm25": update_text,
                },
            }
            resp = requests.post(
                f"{self._daemon_base}/jobs/index",
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            if on_progress:
                on_progress(IndexProgress(stage="daemon", current=0, total=1, message="已送出後台任務"))
            return 0, "已送出後台任務"
        except Exception as exc:
            logger.exception("啟動後台索引失敗: %s", exc)
            return 2, "啟動後台索引失敗，請確認後台 daemon 是否啟動"
