# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core.logging import get_logger
from app.services.catalog_service import CatalogService
from app.services.embedding_service import EmbeddingConfig, EmbeddingService
from app.services.extraction_service import ExtractionService
from app.services.image_embedder import ImageEmbedder
from app.services.project_store import ProjectStore
from app.services.render_service import RenderService
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
            text_model=str(emb.get("text_model", "text-embedding-3-large")),
            text_dim=int(emb.get("text_dim", 3072)),
            image_dim=int(emb.get("image_dim", 2048)),
        )

        self.extractor = ExtractionService()
        self.renderer = RenderService(self.store.paths.thumbs_dir)
        self.image_embedder = ImageEmbedder(self.store.paths.cache_dir / "image_embedder.onnx", dim=self.emb_cfg.image_dim)
        self.embeddings = EmbeddingService(api_key, self.emb_cfg)

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
    ) -> Tuple[int, str]:
        """索引指定檔案（增量）：先移除舊 entries，再寫入新 entries。"""

        def progress(stage: str, cur: int, total: int, msg: str) -> None:
            if on_progress:
                on_progress(IndexProgress(stage=stage, current=cur, total=total, message=msg))

        index = self.store.load_index()
        slides = [s for s in index.get("slides", []) if isinstance(s, dict)]

        # 先移除要重建檔案的舊 slide
        target_paths = {f.get("abs_path") for f in files if f.get("abs_path")}
        slides = [s for s in slides if s.get("file_path") not in target_paths]

        total_files = len(files)
        for fi, f in enumerate(files, start=1):
            if cancel_flag and cancel_flag():
                return 1, "已取消"

            abs_path = f.get("abs_path")
            file_hash = f.get("file_hash")
            if not abs_path or not file_hash:
                continue

            pptx = Path(abs_path)
            progress("extract", fi, total_files, f"抽取文字：{pptx.name}")
            slide_texts = self.extractor.extract(pptx)

            if cancel_flag and cancel_flag():
                return 1, "已取消"

            progress("render", fi, total_files, f"產生縮圖：{pptx.name}")
            rr = self.renderer.render_pptx(pptx, file_hash, slides_count=len(slide_texts))
            thumbs = rr.thumbs

            if cancel_flag and cancel_flag():
                return 1, "已取消"

            progress("embed", fi, total_files, f"產生向量：{pptx.name}")

            # text embeddings（分批）
            all_texts = [st.all_text for st in slide_texts]
            text_vecs = self.embeddings.embed_text_batch(all_texts)

            new_entries = []
            for si, st in enumerate(slide_texts, start=1):
                if cancel_flag and cancel_flag():
                    return 1, "已取消"

                thumb_path = str(thumbs[si - 1]) if si - 1 < len(thumbs) else None
                img_vec_b64 = None

                # 若縮圖存在且非空，做 image embedding（可退化）
                try:
                    if thumb_path:
                        b = Path(thumb_path).read_bytes()
                        if b:
                            img_vec = self.image_embedder.embed_image_bytes(b)
                            img_vec_b64 = vec_to_b64_f32(img_vec)
                except Exception:
                    img_vec_b64 = None

                tv = text_vecs[si - 1] if si - 1 < len(text_vecs) else np.zeros((self.emb_cfg.text_dim,), dtype=np.float32)
                tv_b64 = vec_to_b64_f32(tv)

                # concat：text(3072)+image(2048)，若缺 image 就補 0
                if img_vec_b64:
                    try:
                        iv = np.frombuffer(base64.b64decode(img_vec_b64.encode("ascii")), dtype=np.float32)
                    except Exception:
                        iv = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
                else:
                    iv = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)

                concat = np.concatenate([tv.astype(np.float32), iv.astype(np.float32)], axis=0)
                concat_b64 = vec_to_b64_f32(concat)

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
                        "thumb_path": thumb_path,
                        "text_vec": tv_b64,
                        "image_vec": img_vec_b64,
                        "concat_vec": concat_b64,
                        "indexed_at": int(time.time()),
                    }
                )

            slides.extend(new_entries)
            self.catalog.mark_indexed(abs_path, slides_count=len(slide_texts))

        index["slides"] = slides
        index["embedding"] = {
            "text_model": self.emb_cfg.text_model,
            "text_dim": self.emb_cfg.text_dim,
            "image_dim": self.emb_cfg.image_dim,
            "concat_dim": self.emb_cfg.text_dim + self.emb_cfg.image_dim,
            "vector_encoding": "base64_f32",
            "text_source": "openai" if self.embeddings.has_openai() else "fallback_hash",
            "image_source": "onnx" if self.image_embedder.enabled_onnx() else "fallback_hash",
        }
        self.store.save_index(index)

        progress("done", total_files, total_files, "索引完成")
        return 0, f"已索引 {total_files} 個檔案"
