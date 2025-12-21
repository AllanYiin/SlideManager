# -*- coding: utf-8 -*-

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core.errors import ErrorCode, format_user_message
from app.core.logging import get_logger
from app.services.catalog_service import CatalogService
from app.services.embedding_service import EmbeddingConfig, EmbeddingService
from app.services.extraction_service import ExtractionService, SlideText
from app.services.image_embedder import ImageEmbeddingService
from app.services.project_store import ProjectStore
from app.services.render_service import RenderService
from app.utils.text import tokenize

log = get_logger(__name__)


@dataclass
class IndexProgress:
    stage: str
    current: int
    total: int
    message: str
    avg_page_time: Optional[float] = None
    avg_extract_time: Optional[float] = None
    avg_render_time: Optional[float] = None
    pages_indexed: int = 0
    extract_pages: int = 0
    render_pages: int = 0


@dataclass
class SlideWork:
    file_entry: Dict[str, Any]
    abs_path: str
    file_hash: str
    filename: str
    page: int
    slide_text: Optional[SlideText]
    thumb_path: Optional[str]
    index: int = 0


@dataclass
class FileStageData:
    file_entry: Dict[str, Any]
    abs_path: str
    file_hash: str
    pptx: Path
    slide_texts: List[SlideText]
    thumbs: List[Path]
    slide_count: int
    start_mtime: int
    start_size: int
    slides: List[SlideWork]


class IndexService:
    """建立/更新 index.json。"""

    def __init__(self, store: ProjectStore, catalog: CatalogService, api_key: Optional[str]):
        self.store = store
        self.catalog = catalog
        self.api_key = api_key

        manifest = self.store.load_manifest()
        emb = manifest.get("embedding", {})
        self.emb_cfg = EmbeddingConfig(
            text_model=str(emb.get("text_model", "text-embedding-3-small")),
            text_dim=int(emb.get("text_dim", 1536)),
            image_dim=int(emb.get("image_dim", 512)),
        )

        self.extractor = ExtractionService()
        self.renderer = RenderService(self.store.paths.thumbs_dir)
        self.image_embedder = ImageEmbeddingService(self.store.paths.cache_dir, version="1")
        self.embeddings = EmbeddingService(api_key, self.emb_cfg, cache_dir=self.store.paths.cache_dir)

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
        manifest = self.store.load_manifest()
        meta = self.store.load_meta()
        files = [e for e in manifest.get("files", []) if isinstance(e, dict)]
        meta_files = meta.get("files", {}) if isinstance(meta.get("files"), dict) else {}
        meta_slides = meta.get("slides", {}) if isinstance(meta.get("slides"), dict) else {}

        needed: List[Dict[str, Any]] = []
        for f in files:
            if f.get("missing"):
                continue
            file_id = f.get("file_id")
            if not file_id:
                needed.append(f)
                continue
            meta_entry = meta_files.get(file_id, {})
            if not isinstance(meta_entry, dict) or not meta_entry:
                needed.append(f)
                continue
            mtime = int(f.get("modified_time") or 0)
            last_text = int(meta_entry.get("last_text_extract_at") or 0)
            last_image = int(meta_entry.get("last_image_index_at") or 0)
            if mtime > last_text or mtime > last_image:
                needed.append(f)
                continue
            slide_count = f.get("slide_count")
            meta_slide_count = meta_entry.get("slide_count")
            if slide_count is not None and meta_slide_count is not None:
                try:
                    if int(slide_count) != int(meta_slide_count):
                        needed.append(f)
                        continue
                except Exception:
                    needed.append(f)
                    continue
            if slide_count is not None:
                try:
                    slide_total = int(slide_count)
                except Exception:
                    slide_total = 0
                if slide_total > 0:
                    slide_in_meta = sum(
                        1 for s in meta_slides.values() if isinstance(s, dict) and s.get("file_id") == file_id
                    )
                    if slide_in_meta < slide_total:
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
        update_text: bool = True,
        update_image: bool = True,
    ) -> Tuple[int, str]:
        """索引指定檔案（依新規格：先文字、後圖片）。"""

        overall_start = time.perf_counter()
        update_text_vectors = bool(update_text and self.embeddings.has_openai())
        update_image_vectors = bool(update_image and self.image_embedder.enabled_onnx())
        processed_pages = 0
        extract_pages = 0
        render_pages = 0
        extract_time = 0.0
        render_time = 0.0

        def build_metrics() -> Dict[str, Any]:
            elapsed = time.perf_counter() - overall_start
            avg_page_time = elapsed / processed_pages if processed_pages > 0 else None
            avg_extract_time = extract_time / extract_pages if extract_pages > 0 else None
            avg_render_time = render_time / render_pages if render_pages > 0 else None
            return {
                "avg_page_time": avg_page_time,
                "avg_extract_time": avg_extract_time,
                "avg_render_time": avg_render_time,
                "pages_indexed": processed_pages,
                "extract_pages": extract_pages,
                "render_pages": render_pages,
            }

        def progress(stage: str, cur: int, total: int, msg: str) -> None:
            if on_progress:
                metrics = build_metrics()
                on_progress(
                    IndexProgress(
                        stage=stage,
                        current=cur,
                        total=total,
                        message=msg,
                        avg_page_time=metrics["avg_page_time"],
                        avg_extract_time=metrics["avg_extract_time"],
                        avg_render_time=metrics["avg_render_time"],
                        pages_indexed=metrics["pages_indexed"],
                        extract_pages=metrics["extract_pages"],
                        render_pages=metrics["render_pages"],
                    )
                )

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

        meta = self.store.load_meta()
        meta_files = meta.get("files", {}) if isinstance(meta.get("files"), dict) else {}
        meta_slides = meta.get("slides", {}) if isinstance(meta.get("slides"), dict) else {}

        total_files = len(files)
        stage2_units = 0
        if update_text:
            stage2_units += 2
        if update_image:
            stage2_units += 1
        overall_total = total_files * 2 + stage2_units
        render_message = ""
        staged_files: List[FileStageData] = []

        for fi, f in enumerate(files, start=1):
            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(fi, overall_total):
                return 1, "已取消"

            abs_path = f.get("abs_path")
            file_hash = f.get("file_hash")
            file_id = f.get("file_id")
            if not abs_path or not file_hash or not file_id:
                continue

            pptx = Path(abs_path)
            if not pptx.exists():
                message = format_user_message(ErrorCode.PATH_NOT_FOUND, detail=str(pptx))
                log.warning("[PATH_NOT_FOUND] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.PATH_NOT_FOUND.value, message)
                progress("skip", fi, overall_total, "檔案已移除，已略過")
                continue
            try:
                start_stat = pptx.stat()
                start_mtime = int(start_stat.st_mtime)
                start_size = int(start_stat.st_size)
            except PermissionError as exc:
                message = format_user_message(ErrorCode.PERMISSION_DENIED, detail=str(exc))
                log.warning("[PERMISSION_DENIED] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.PERMISSION_DENIED.value, message)
                progress("skip", fi, overall_total, "檔案權限不足，已略過")
                continue
            except Exception as exc:
                log.warning("讀取檔案狀態失敗：%s (%s)", pptx, exc)
                continue

            if int(f.get("modified_time") or -1) != start_mtime or int(f.get("size") or -1) != start_size:
                message = format_user_message(ErrorCode.MTIME_CHANGED, detail=str(pptx))
                log.warning("[MTIME_CHANGED] %s", message)
                self.catalog.mark_index_error(abs_path, ErrorCode.MTIME_CHANGED.value, message)
                progress("skip", fi, overall_total, "檔案已變更，請重新掃描")
                continue

            slide_count = int(f.get("slide_count") or 0)

            if update_text:
                progress("extract", fi, overall_total, f"抽取文字：{pptx.name}")
                extract_start = time.perf_counter()
                slide_texts = self.extractor.extract(pptx)
                extract_elapsed = time.perf_counter() - extract_start
            else:
                slide_texts = []
                extract_elapsed = 0.0

            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(fi, overall_total):
                return 1, "已取消"

            source_slide_count = slide_count
            if slide_texts:
                source_slide_count = max(source_slide_count, len(slide_texts))
            slide_count = max(slide_count, source_slide_count)
            if update_text and extract_elapsed > 0 and slide_count > 0:
                extract_time += extract_elapsed
                extract_pages += slide_count
            if slide_count > 0:
                processed_pages += slide_count
            file_slides: List[SlideWork] = []
            if update_text and slide_texts:
                slide_iter = enumerate(slide_texts, start=1)
            else:
                slide_iter = ((si, None) for si in range(1, slide_count + 1))
            for si, st in slide_iter:
                file_slides.append(
                    SlideWork(
                        file_entry=f,
                        abs_path=abs_path,
                        file_hash=file_hash,
                        filename=pptx.name,
                        page=si,
                        slide_text=st,
                        thumb_path=None,
                    )
                )
            staged_files.append(
                FileStageData(
                    file_entry=f,
                    abs_path=abs_path,
                    file_hash=file_hash,
                    pptx=pptx,
                    slide_texts=slide_texts,
                    thumbs=[],
                    slide_count=slide_count,
                    start_mtime=start_mtime,
                    start_size=start_size,
                    slides=file_slides,
                )
            )
            self.catalog.mark_extracted(
                abs_path,
                slides_count=slide_count,
                index_mtime_epoch=start_mtime,
            )
            progress(
                "extracted",
                fi,
                overall_total,
                f"已擷取：{pptx.name}",
            )

        if cancel_flag and cancel_flag():
            return 1, "已取消"

        all_slides: List[SlideWork] = []
        for fd in staged_files:
            for slide in fd.slides:
                slide.index = len(all_slides)
                all_slides.append(slide)

        text_indices: List[int] = []
        text_payload: List[str] = []
        for slide in all_slides:
            if update_text and slide.slide_text is not None:
                if slide.slide_text.all_text.strip():
                    text_indices.append(slide.index)
                    text_payload.append(slide.slide_text.all_text)

        bm25_future = None
        bm25_progress = total_files + 1 if update_text else None
        text_progress = total_files + 2 if update_text else None
        with ThreadPoolExecutor(max_workers=1) as executor:
            if update_text and text_payload:
                progress(
                    "bm25_start",
                    bm25_progress or total_files,
                    overall_total,
                    "建立 BM25 tokens 中...",
                )
                bm25_future = executor.submit(lambda: [tokenize(t) for t in text_payload])

            text_vecs: List[np.ndarray] = []
            if update_text_vectors and text_payload:
                progress(
                    "embed_text",
                    text_progress or total_files,
                    overall_total,
                    "產生文字向量中...",
                )
                text_vecs = self.embeddings.embed_text_batch(text_payload)
            elif update_text:
                if not text_payload:
                    progress(
                        "embed_text_skip",
                        text_progress or total_files,
                        overall_total,
                        "無可用文字，略過文字向量",
                    )
                else:
                    progress(
                        "embed_text_skip",
                        text_progress or total_files,
                        overall_total,
                        "未設定 API Key，略過文字向量",
                    )

            bm25_tokens: List[List[str]] = []
            if bm25_future:
                try:
                    bm25_tokens = bm25_future.result()
                except Exception as exc:
                    log.warning("BM25 tokens 建立失敗：%s", exc)
                    bm25_tokens = []

        if cancel_flag and cancel_flag():
            return 1, "已取消"

        text_vectors_to_append: Dict[str, np.ndarray] = {}
        slide_tokens_by_id: Dict[str, List[str]] = {}
        slide_text_vec_by_id: Dict[str, Optional[np.ndarray]] = {}
        for idx, slide_idx in enumerate(text_indices):
            slide = all_slides[slide_idx]
            slide_id = f"{slide.file_entry.get('file_id')}#{slide.page}"
            tok = bm25_tokens[idx] if idx < len(bm25_tokens) else []
            slide_tokens_by_id[slide_id] = tok
            if idx < len(text_vecs):
                vec = np.asarray(text_vecs[idx], dtype=np.float16)
                text_vectors_to_append[slide_id] = vec
                slide_text_vec_by_id[slide_id] = vec
            else:
                slide_text_vec_by_id[slide_id] = None

        if update_text and text_vectors_to_append:
            self.store.append_text_vectors(text_vectors_to_append)

        if update_image:
            self.renderer.begin_batch()
            try:
                image_paths: List[Path] = []
                image_slide_ids: List[str] = []
                sorted_files = sorted(staged_files, key=lambda x: x.start_mtime, reverse=True)
                for fi, fd in enumerate(sorted_files, start=1):
                    if cancel_flag and cancel_flag():
                        return 1, "已取消"
                    if not wait_if_paused(total_files + fi, overall_total):
                        return 1, "已取消"
                    file_id = fd.file_entry.get("file_id")
                    if not file_id:
                        continue
                    progress("render", total_files + fi, overall_total, f"產生縮圖：{fd.pptx.name}")
                    render_start = time.perf_counter()
                    rr = self.renderer.render_pptx(fd.pptx, file_id, slides_count=fd.slide_count)
                    render_elapsed = time.perf_counter() - render_start
                    render_message = rr.message
                    thumbs = rr.thumbs
                    if not rr.ok:
                        log.warning("[RENDERER_ERROR] %s (%s)", fd.pptx, rr.message)
                        progress("render", total_files + fi, overall_total, rr.message)
                    if render_elapsed > 0 and fd.slide_count > 0:
                        render_time += render_elapsed
                        render_pages += fd.slide_count
                    for slide in fd.slides:
                        thumb_path = str(thumbs[slide.page - 1]) if slide.page - 1 < len(thumbs) else None
                        slide.thumb_path = thumb_path
                        if update_image_vectors and thumb_path:
                            image_paths.append(Path(thumb_path))
                            image_slide_ids.append(f"{file_id}#{slide.page}")
            finally:
                self.renderer.end_batch()

            if update_image_vectors and image_paths:
                progress(
                    "embed_image_start",
                    total_files + stage2_units,
                    overall_total,
                    "批次產生圖片向量中...",
                )
                try:
                    image_vecs = self.image_embedder.embed_images(
                        image_paths,
                        dim=self.emb_cfg.image_dim,
                        batch_size=16,
                    )
                except Exception as exc:
                    log.warning("圖片向量批次產生失敗：%s", exc)
                    image_vecs = np.zeros((len(image_paths), self.emb_cfg.image_dim), dtype=np.float32)
                image_vectors_to_append = {
                    slide_id: np.asarray(vec, dtype=np.float16)
                    for slide_id, vec in zip(image_slide_ids, image_vecs)
                }
                if image_vectors_to_append:
                    self.store.append_image_vectors(image_vectors_to_append)

        now = int(time.time())
        for fi, fd in enumerate(staged_files, start=1):
            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(total_files + stage2_units + fi, overall_total):
                return 1, "已取消"

            try:
                end_stat = fd.pptx.stat()
                end_mtime = int(end_stat.st_mtime)
                end_size = int(end_stat.st_size)
            except Exception:
                end_mtime = fd.start_mtime
                end_size = fd.start_size

            if end_mtime != fd.start_mtime or end_size != fd.start_size:
                message = format_user_message(ErrorCode.MTIME_CHANGED, detail=str(fd.pptx))
                log.warning("[MTIME_CHANGED] %s", message)
                self.catalog.mark_index_error(fd.abs_path, ErrorCode.MTIME_CHANGED.value, message)
                progress("skip", total_files + stage2_units + fi, overall_total, "索引期間檔案有變更，已略過")
                continue

            file_id = fd.file_entry.get("file_id")
            if not file_id:
                continue

            file_entry = meta_files.get(file_id, {})
            file_entry = dict(file_entry) if isinstance(file_entry, dict) else {}
            file_entry.update(
                {
                    "path": fd.abs_path,
                    "name": fd.pptx.name,
                    "mtime": fd.start_mtime,
                    "size": fd.start_size,
                    "slide_count": fd.slide_count,
                }
            )
            if update_text:
                file_entry["last_text_extract_at"] = now
            if update_image:
                file_entry["last_image_index_at"] = now
            meta_files[file_id] = file_entry

            for slide_key, slide_val in list(meta_slides.items()):
                if not isinstance(slide_val, dict):
                    continue
                if slide_val.get("file_id") != file_id:
                    continue
                if int(slide_val.get("slide_no") or 0) > fd.slide_count:
                    meta_slides.pop(slide_key, None)

            slides_patch: Dict[str, Any] = {}
            text_indexed_count = 0
            image_indexed_count = 0
            bm25_indexed_count = 0

            for slide in fd.slides:
                slide_id = f"{file_id}#{slide.page}"
                prev = meta_slides.get(slide_id, {})
                prev = dict(prev) if isinstance(prev, dict) else {}
                flags = prev.get("flags") if isinstance(prev.get("flags"), dict) else {}

                if update_text and slide.slide_text is not None:
                    all_text = slide.slide_text.all_text or ""
                    title = slide.slide_text.title or ""
                    body = slide.slide_text.body or ""
                    tokens = slide_tokens_by_id.get(slide_id, [])
                    has_text = bool(all_text.strip())
                    flags.update(
                        {
                            "has_text": has_text,
                            "has_text_vec": bool(slide_text_vec_by_id.get(slide_id)),
                            "has_bm25": True,
                        }
                    )
                    prev.update(
                        {
                            "title": title,
                            "body_text": body,
                            "text_for_bm25": all_text,
                            "bm25_tokens": tokens,
                        }
                    )
                    if has_text:
                        text_indexed_count += 1
                    if flags.get("has_bm25"):
                        bm25_indexed_count += 1

                if update_image:
                    thumb_path = slide.thumb_path or prev.get("thumbnail_path")
                    if thumb_path:
                        prev["thumbnail_path"] = thumb_path
                    flags["has_image"] = bool(thumb_path)
                    if update_image_vectors and thumb_path:
                        flags["has_image_vec"] = True
                    else:
                        flags["has_image_vec"] = bool(flags.get("has_image_vec"))
                    if flags.get("has_image"):
                        image_indexed_count += 1

                prev.update(
                    {
                        "file_id": file_id,
                        "slide_no": slide.page,
                        "flags": flags,
                        "updated_at": now,
                    }
                )
                meta_slides[slide_id] = prev
                slides_patch[slide_id] = prev

            self.store.append_meta_log({"files": {file_id: file_entry}, "slides": slides_patch})
            self.catalog.mark_indexed(
                fd.abs_path,
                slides_count=fd.slide_count,
                text_indexed_count=text_indexed_count if update_text else None,
                image_indexed_count=image_indexed_count if update_image else None,
                bm25_indexed_count=bm25_indexed_count if update_text else None,
                index_mode=self._resolve_index_mode(update_text, update_image),
            )
            progress(
                "file_done",
                total_files + stage2_units + fi,
                overall_total,
                f"已更新索引：{fd.pptx.name}",
            )

        meta["files"] = meta_files
        meta["slides"] = meta_slides
        self.store.save_meta(meta)

        manifest = self.store.load_manifest()
        manifest["embedding"] = {
            "text_model": self.emb_cfg.text_model,
            "text_dim": self.emb_cfg.text_dim,
            "image_dim": self.emb_cfg.image_dim,
            "vector_encoding": "npz_fp16",
            "text_source": "openai" if self.embeddings.has_openai() else "none",
            "image_source": "onnx" if self.image_embedder.enabled_onnx() else "none",
            "image_model_version": self.image_embedder.status().version,
        }
        render_status = self.renderer.status()
        manifest["render"] = {
            "available": render_status.get("available"),
            "active": render_status.get("active"),
            "status": render_status.get("status"),
            "last_message": render_message,
        }
        self.store.save_manifest(manifest)

        progress("done", overall_total, overall_total, "索引完成")
        return 0, f"已索引 {total_files} 個檔案"
