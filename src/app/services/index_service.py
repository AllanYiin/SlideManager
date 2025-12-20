# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core.errors import ErrorCode, format_user_message
from app.core.logging import get_logger
from app.services.catalog_service import CatalogService
from app.services.embedding_service import EmbeddingConfig, EmbeddingService
from app.services.extraction_service import ExtractionService
from app.services.image_embedder import ImageEmbeddingService
from app.services.project_store import ProjectStore
from app.services.render_service import RenderService
from app.utils.text import tokenize
from app.utils.vectors import vec_to_b64_f32

log = get_logger(__name__)


@dataclass
class IndexProgress:
    stage: str
    current: int
    total: int
    message: str


class IndexService:
    """建立/更新 index.json。"""

    def __init__(self, store: ProjectStore, catalog: CatalogService, api_key: Optional[str]):
        self.store = store
        self.catalog = catalog
        self.api_key = api_key

        idx = self.store.load_index()
        emb = idx.get("embedding", {})
        self.emb_cfg = EmbeddingConfig(
            text_model=str(emb.get("text_model", "text-embedding-3-small")),
            text_dim=int(emb.get("text_dim", 1536)),
            image_dim=int(emb.get("image_dim", 4096)),
        )

        self.extractor = ExtractionService()
        self.renderer = RenderService(self.store.paths.thumbs_dir)
        self.image_embedder = ImageEmbeddingService(self.store.paths.cache_dir, version="1")
        self.embeddings = EmbeddingService(api_key, self.emb_cfg, cache_dir=self.store.paths.cache_dir)

    def compute_needed_files(self) -> List[Dict[str, Any]]:
        cat = self.store.load_catalog()
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]

        needed = []
        for f in files:
            if f.get("missing"):
                continue
            status = f.get("index_status") if isinstance(f.get("index_status"), dict) else {}
            indexed = bool(status.get("indexed")) if status else bool(f.get("indexed"))
            if not indexed:
                needed.append(f)
                continue
            mtime = int(f.get("modified_time") or 0)
            index_mtime = int(status.get("index_mtime_epoch") or 0)
            if mtime > index_mtime:
                needed.append(f)
                continue
            slide_count = f.get("slide_count")
            index_slide_count = status.get("index_slide_count")
            if slide_count is not None and index_slide_count is not None:
                try:
                    if int(slide_count) != int(index_slide_count):
                        needed.append(f)
                        continue
                except Exception:
                    needed.append(f)
                    continue
        return needed

    def rebuild_for_files(
        self,
        files: List[Dict[str, Any]],
        *,
        on_progress: Optional[Callable[[IndexProgress], None]] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
        pause_flag: Optional[Callable[[], bool]] = None,
    ) -> Tuple[int, str]:
        """索引指定檔案（增量）：先移除舊 entries，再寫入新 entries。"""

        def progress(stage: str, cur: int, total: int, msg: str) -> None:
            if on_progress:
                on_progress(IndexProgress(stage=stage, current=cur, total=total, message=msg))

        def wait_if_paused(cur: int, total: int) -> bool:
            if not pause_flag:
                return True
            paused_notified = False
            while pause_flag():
                if cancel_flag and cancel_flag():
                    return False
                if not paused_notified:
                    progress("pause", cur, total, "已暫停，等待續跑...")
                    paused_notified = True
                time.sleep(0.2)
            return True

        index = self.store.load_index()
        slides = [s for s in index.get("slides", []) if isinstance(s, dict)]

        # 先移除要重建檔案的舊 slide
        target_paths = {f.get("abs_path") for f in files if f.get("abs_path")}
        slides = [s for s in slides if s.get("file_path") not in target_paths]

        total_files = len(files)
        render_message = ""
        for fi, f in enumerate(files, start=1):
            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(fi, total_files):
                return 1, "已取消"

            abs_path = f.get("abs_path")
            file_hash = f.get("file_hash")
            if not abs_path or not file_hash:
                continue

            pptx = Path(abs_path)
            if not pptx.exists():
                message = format_user_message(ErrorCode.PATH_NOT_FOUND, detail=str(pptx))
                log.warning("[PATH_NOT_FOUND] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.PATH_NOT_FOUND.value, message)
                progress("skip", fi, total_files, "檔案已移除，已略過")
                continue
            try:
                start_stat = pptx.stat()
                start_mtime = int(start_stat.st_mtime)
                start_size = int(start_stat.st_size)
            except PermissionError as exc:
                message = format_user_message(ErrorCode.PERMISSION_DENIED, detail=str(exc))
                log.warning("[PERMISSION_DENIED] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.PERMISSION_DENIED.value, message)
                progress("skip", fi, total_files, "檔案權限不足，已略過")
                continue
            except Exception as exc:
                log.warning("讀取檔案狀態失敗：%s (%s)", pptx, exc)
                continue

            if int(f.get("modified_time") or -1) != start_mtime or int(f.get("size") or -1) != start_size:
                message = format_user_message(ErrorCode.MTIME_CHANGED, detail=str(pptx))
                log.warning("[MTIME_CHANGED] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.MTIME_CHANGED.value, message)
                progress("skip", fi, total_files, "檔案已變更，請重新掃描")
                continue

            progress("extract", fi, total_files, f"抽取文字：{pptx.name}")
            slide_texts = self.extractor.extract(pptx)

            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(fi, total_files):
                return 1, "已取消"

            progress("render", fi, total_files, f"產生縮圖：{pptx.name}")
            rr = self.renderer.render_pptx(pptx, file_hash, slides_count=len(slide_texts))
            render_message = rr.message
            thumbs = rr.thumbs
            if not rr.ok:
                log.warning("[RENDERER_ERROR] %s (%s)", pptx, rr.message)
                progress("render", fi, total_files, rr.message)

            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(fi, total_files):
                return 1, "已取消"

            if self.embeddings.has_openai():
                progress("embed", fi, total_files, f"產生向量：{pptx.name}")
                # text embeddings（分批）
                all_texts = [st.all_text for st in slide_texts]
                text_vecs = self.embeddings.embed_text_batch(all_texts)
            else:
                progress("embed", fi, total_files, f"未設定 API Key，略過向量：{pptx.name}")
                text_vecs = []

            new_entries = []
            for si, st in enumerate(slide_texts, start=1):
                if cancel_flag and cancel_flag():
                    return 1, "已取消"
                if not wait_if_paused(fi, total_files):
                    return 1, "已取消"

                thumb_path = str(thumbs[si - 1]) if si - 1 < len(thumbs) else None
                img_vec_b64 = None

                # 若縮圖存在且非空，做 image embedding（可退化）
                if thumb_path:
                    try:
                        b = Path(thumb_path).read_bytes()
                        if b:
                            img_vec = self.image_embedder.embed_image_bytes(b, dim=self.emb_cfg.image_dim)
                            img_vec_b64 = vec_to_b64_f32(img_vec)
                    except Exception:
                        img_vec_b64 = None

                tv = text_vecs[si - 1] if si - 1 < len(text_vecs) else None
                tv_b64 = vec_to_b64_f32(tv) if tv is not None else None

                has_text_vec = tv is not None
                has_image_vec = img_vec_b64 is not None
                if has_text_vec or has_image_vec:
                    if has_text_vec:
                        tv_concat = tv.astype(np.float32)
                    else:
                        tv_concat = np.zeros((self.emb_cfg.text_dim,), dtype=np.float32)
                    if has_image_vec:
                        try:
                            iv = np.frombuffer(base64.b64decode(img_vec_b64.encode("ascii")), dtype=np.float32)
                        except Exception:
                            iv = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
                    else:
                        iv = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
                    concat = np.concatenate([tv_concat, iv.astype(np.float32)], axis=0)
                    concat_b64 = vec_to_b64_f32(concat)
                else:
                    concat_b64 = None

                slide_id = f"{f.get('file_id')}_{file_hash}_p{si:04d}"
                new_entries.append(
                    {
                        "slide_id": slide_id,
                        "file_id": f.get("file_id"),
                        "file_path": abs_path,
                        "file_hash": file_hash,
                        "filename": pptx.name,
                        "page": si,
                        "title": st.title,
                        "body": st.body,
                        "all_text": st.all_text,
                        "bm25_tokens": tokenize(st.all_text),
                        "thumb_path": thumb_path,
                        "text_vec": tv_b64,
                        "image_vec": img_vec_b64,
                        "concat_vec": concat_b64,
                        "indexed_at": int(time.time()),
                    }
                )

            try:
                end_stat = pptx.stat()
                end_mtime = int(end_stat.st_mtime)
                end_size = int(end_stat.st_size)
            except Exception:
                end_mtime = start_mtime
                end_size = start_size

            if end_mtime != start_mtime or end_size != start_size:
                message = format_user_message(ErrorCode.MTIME_CHANGED, detail=str(pptx))
                log.warning("[MTIME_CHANGED] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.MTIME_CHANGED.value, message)
                progress("skip", fi, total_files, "索引期間檔案有變更，已略過")
                continue

            slides.extend(new_entries)
            self.catalog.mark_indexed(abs_path, slides_count=len(slide_texts))

        index["slides"] = slides
        index["embedding"] = {
            "text_model": self.emb_cfg.text_model,
            "text_dim": self.emb_cfg.text_dim,
            "image_dim": self.emb_cfg.image_dim,
            "concat_dim": self.emb_cfg.text_dim + self.emb_cfg.image_dim,
            "vector_encoding": "base64_f32",
            "text_source": "openai" if self.embeddings.has_openai() else "none",
            "image_source": "onnx" if self.image_embedder.enabled_onnx() else "none",
            "image_model_version": self.image_embedder.status().version,
        }
        render_status = self.renderer.status()
        index["render"] = {
            "available": render_status.get("available"),
            "active": render_status.get("active"),
            "status": render_status.get("status"),
            "last_message": render_message,
        }
        self.store.save_index(index)

        progress("done", total_files, total_files, "索引完成")
        return 0, f"已索引 {total_files} 個檔案"
