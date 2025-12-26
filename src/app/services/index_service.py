# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.services.backend_client import BackendApiClient, BackendConfig, SseWorker
from app.services.backend_daemon_manager import BackendDaemonManager
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
            "available": True,
            "active": True,
            "status": {"daemon": "已接線"},
            "last_message": "後台 daemon 已接線。",
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
        cfg = BackendConfig()
        self._client = BackendApiClient(cfg)
        self._daemon = BackendDaemonManager(cfg, root_dir=store.root)

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
                "enable_text": update_text,
                "enable_thumb": update_image,
                "enable_text_vec": update_text,
                "enable_img_vec": update_image,
                "enable_bm25": update_text,
            }
            job_id = self._client.start_index_job(str(self.store.root), "missing_or_changed", payload)
            if not job_id:
                raise RuntimeError("daemon 未回傳 job_id")
            if on_progress:
                on_progress(IndexProgress(stage="daemon", current=0, total=1, message="已送出後台任務"))
            return 0, "已送出後台任務"
        except Exception as exc:
            logger.exception("啟動後台索引失敗: %s", exc)
            return 2, "啟動後台索引失敗，請確認後台 daemon 是否啟動"

    def start_index_job(
        self,
        library_root: str,
        *,
        plan_mode: str,
        options: Dict[str, Any],
    ) -> Optional[str]:
        return self._client.start_index_job(library_root, plan_mode, options)

    def health(self) -> bool:
        return self._client.health()

    def ensure_backend_ready(self, *, timeout_sec: float = 6.0) -> bool:
        return self._daemon.ensure_running(timeout_sec=timeout_sec)

    def pause_job(self, job_id: str) -> bool:
        return self._client.pause_job(job_id)

    def resume_job(self, job_id: str) -> bool:
        return self._client.resume_job(job_id)

    def cancel_job(self, job_id: str) -> bool:
        return self._client.cancel_job(job_id)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._client.get_job(job_id)

    def sse_worker_for_job(self, job_id: str) -> SseWorker:
        return self._client.sse_worker_for_job(job_id)
