# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.core.errors import ErrorCode, format_user_message
from app.core.logging import get_logger
from app.services.catalog_service import CatalogService
from app.services.embedding_service import EmbeddingConfig, EmbeddingError, EmbeddingService
from app.services.extraction_service import ExtractionService, SlideText
from app.services.image_embedder import ImageEmbeddingService
from app.services.project_store import ProjectStore
from app.services.render_service import RenderService
from app.utils.text import tokenize

log = get_logger(__name__)

TEXT_EMBED_BATCH_SIZE = int(os.getenv("TEXT_EMBED_BATCH_SIZE", "32") or 32)
TEXT_EMBED_PROGRESS_EVERY = 1
IMAGE_EMBED_BATCH_SIZE = int(os.getenv("IMAGE_EMBED_BATCH_SIZE", "16") or 16)
IMAGE_EMBED_PROGRESS_EVERY = 1


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
    reuse_text: bool = True
    reuse_image: bool = True


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
        self.image_embedder = ImageEmbeddingService(self.store.paths.cache_dir, version="1", autoload=False)
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
        files = [e for e in manifest.get("files", []) if isinstance(e, dict)]
        slide_pages = self.store.load_slide_pages()

        needed: List[Dict[str, Any]] = []
        for f in files:
            if f.get("missing"):
                continue
            file_id = f.get("file_id")
            if not file_id:
                needed.append(f)
                continue
            mtime = int(f.get("modified_time") or 0)
            indexed_at = int(f.get("indexed_at") or 0)
            if indexed_at <= 0 or mtime > indexed_at:
                needed.append(f)
                continue
            index_mode = f.get("index_mode") or "none"
            if index_mode in {"none", "text_only", "image_only"}:
                needed.append(f)
                continue
            slide_count = f.get("slide_count")
            try:
                slide_total = int(slide_count) if slide_count is not None else 0
            except Exception:
                slide_total = 0
            if slide_total <= 0:
                slide_total = int(f.get("slides_count") or 0)
            summary = f.get("last_index_summary") if isinstance(f.get("last_index_summary"), dict) else {}
            if slide_total > 0 and summary:
                text_ok = int(summary.get("slides_ok_text") or 0)
                image_ok = int(summary.get("slides_ok_image") or 0)
                bm25_ok = int(summary.get("slides_ok_bm25") or 0)
                if text_ok < slide_total or image_ok < slide_total or bm25_ok < slide_total:
                    needed.append(f)
                    continue
            if slide_count is not None:
                try:
                    slide_total = int(slide_count)
                except Exception:
                    slide_total = 0
                if slide_total > 0:
                    slide_in_pages = sum(1 for key in slide_pages.keys() if key.startswith(f"{file_id}#"))
                    if slide_in_pages < slide_total:
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
        log.info(
            "[INDEX_SCOPE][SVC] files_count=%d files=%s",
            len(files),
            [(f.get("file_id"), f.get("abs_path")) for f in files],
        )
        if len(files) == 0:
            return 1, "沒有可索引檔案"
        update_text_vectors = bool(update_text and self.embeddings.has_openai())
        update_image_vectors = bool(update_image and self.image_embedder.enabled_onnx())
        processed_pages = 0
        extract_pages = 0
        render_pages = 0
        extract_time = 0.0
        render_time = 0.0
        text_vectors_written = False
        image_vectors_written = False

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
                    save_slide_pages(force=True)
                    progress("pause", cur, total, "已暫停，等待續跑...")
                    paused_notified = True
                time.sleep(0.2)
            return True

        slide_pages = self.store.load_slide_pages() if update_text else {}
        slide_pages_updates = 0
        if update_text_vectors:
            try:
                self.embeddings.ensure_cache_file()
            except Exception as exc:
                log.warning("建立文字 embedding 快取檔失敗：%s", exc)

        def save_slide_pages(*, force: bool = False) -> None:
            nonlocal slide_pages_updates
            if not update_text:
                return
            if force or slide_pages_updates >= 100:
                try:
                    self.store.save_slide_pages(slide_pages)
                except Exception as exc:
                    log.warning("保存 slide_pages.json 失敗：%s", exc)
                slide_pages_updates = 0

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

            cached_texts_by_no: Dict[int, SlideText] = {}
            cached_text_valid = int(f.get("indexed_at") or 0) >= start_mtime
            if update_text and cached_text_valid:
                for slide_key, slide_text in slide_pages.items():
                    if not isinstance(slide_key, str):
                        continue
                    if not slide_key.startswith(f"{file_id}#"):
                        continue
                    try:
                        slide_no = int(slide_key.split("#", 1)[1])
                    except Exception:
                        continue
                    cached_texts_by_no[slide_no] = SlideText(
                        page=slide_no,
                        title="",
                        body="",
                        all_text=str(slide_text or ""),
                    )
                if slide_count <= 0 and cached_texts_by_no:
                    slide_count = max(cached_texts_by_no.keys())

            cached_thumbs_by_no: Dict[int, str] = {}
            if update_image and slide_count > 0:
                thumbs_dir = self.store.paths.thumbs_dir / file_id
                for slide_no in range(1, slide_count + 1):
                    thumb_path = thumbs_dir / f"{slide_no}.png"
                    if thumb_path.exists():
                        cached_thumbs_by_no[slide_no] = str(thumb_path)

            reuse_text = False
            slide_texts: List[SlideText] = []
            extract_elapsed = 0.0
            if update_text:
                missing_text = False
                if slide_count > 0:
                    missing_text = any(slide_no not in cached_texts_by_no for slide_no in range(1, slide_count + 1))
                else:
                    missing_text = not bool(cached_texts_by_no)
                need_extract = not cached_text_valid or missing_text
                if not need_extract:
                    reuse_text = True
                    progress("extract_cache", fi, overall_total, f"使用快取文字：{pptx.name}")
                    slide_texts = [cached_texts_by_no[n] for n in sorted(cached_texts_by_no.keys())]
                else:
                    progress("extract", fi, overall_total, f"抽取文字：{pptx.name}")
                    extract_start = time.perf_counter()
                    extracted = self.extractor.extract(pptx)
                    extract_elapsed = time.perf_counter() - extract_start
                    extracted_by_no = {st.page: st for st in extracted}
                    if slide_count <= 0:
                        slide_count = max(slide_count, len(extracted_by_no), len(cached_texts_by_no))
                    if slide_count > 0:
                        slide_texts = []
                        for slide_no in range(1, slide_count + 1):
                            if slide_no in cached_texts_by_no:
                                slide_texts.append(cached_texts_by_no[slide_no])
                            elif slide_no in extracted_by_no:
                                slide_texts.append(extracted_by_no[slide_no])
                        if not slide_texts:
                            slide_texts = extracted
                    else:
                        slide_texts = extracted or list(cached_texts_by_no.values())

            reuse_image = False
            if update_image:
                missing_image = False
                if slide_count > 0:
                    missing_image = any(slide_no not in cached_thumbs_by_no for slide_no in range(1, slide_count + 1))
                else:
                    missing_image = not bool(cached_thumbs_by_no)
                if not missing_image:
                    reuse_image = True

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

            if update_image and file_slides:
                for slide in file_slides:
                    thumb_path = cached_thumbs_by_no.get(slide.page)
                    if thumb_path:
                        slide.thumb_path = thumb_path

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
                    reuse_text=reuse_text,
                    reuse_image=reuse_image,
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

        bm25_indices: List[int] = []
        bm25_payload: List[str] = []
        embed_indices: List[int] = []
        embed_payload: List[str] = []

        if update_text:
            for slide in all_slides:
                slide_id = f"{slide.file_entry.get('file_id')}#{slide.page}"
                if slide.slide_text is None:
                    continue
                text_content = slide.slide_text.all_text or ""
                if text_content.strip():
                    bm25_indices.append(slide.index)
                    bm25_payload.append(text_content)
                if update_text_vectors:
                    embed_indices.append(slide.index)
                    embed_payload.append(text_content)

        bm25_future = None
        bm25_progress = total_files + 1 if update_text else None
        text_progress = total_files + 2 if update_text else None
        with ThreadPoolExecutor(max_workers=1) as executor:
            if update_text and bm25_payload:
                progress(
                    "bm25_start",
                    bm25_progress or total_files,
                    overall_total,
                    "建立 BM25 tokens 中...",
                )
                bm25_future = executor.submit(lambda: [tokenize(t) for t in bm25_payload])

            if update_text_vectors and embed_payload:
                total = len(embed_payload)
                vec_dtype = np.float16 if hasattr(np, "float16") else np.float32
                zero_vec = np.zeros(self.emb_cfg.text_dim, dtype=vec_dtype)
                for batch_start in range(0, total, TEXT_EMBED_BATCH_SIZE):
                    if cancel_flag and cancel_flag():
                        return 1, "已取消"
                    batch_texts = embed_payload[batch_start : batch_start + TEXT_EMBED_BATCH_SIZE]
                    batch_indices = embed_indices[batch_start : batch_start + TEXT_EMBED_BATCH_SIZE]
                    if TEXT_EMBED_PROGRESS_EVERY > 0:
                        progress(
                            "embed_text",
                            text_progress or total_files,
                            overall_total,
                            f"產生文字向量中... {batch_start}/{total}",
                        )
                    try:
                        batch_vecs = self.embeddings.embed_text_batch(batch_texts)
                    except EmbeddingError as exc:
                        msg = format_user_message(ErrorCode.OPENAI_ERROR, detail=str(exc))
                        for fd in staged_files:
                            self.catalog.mark_index_error(fd.abs_path, ErrorCode.OPENAI_ERROR.value, msg)
                        log.error("[OPENAI_ERROR] 文字向量建立失敗：%s", exc)
                        raise
                    batch_to_append: Dict[str, np.ndarray] = {}
                    for idx, slide_idx in enumerate(batch_indices):
                        slide = all_slides[slide_idx]
                        slide_id = f"{slide.file_entry.get('file_id')}#{slide.page}"
                        if idx < len(batch_vecs) and batch_vecs[idx] is not None:
                            vec = np.asarray(batch_vecs[idx], dtype=vec_dtype)
                        else:
                            vec = zero_vec
                        batch_to_append[slide_id] = vec
                    if batch_to_append:
                        self.store.append_text_vectors(batch_to_append)
                        text_vectors_written = True
                    if TEXT_EMBED_PROGRESS_EVERY > 0:
                        progress(
                            "embed_text_write",
                            text_progress or total_files,
                            overall_total,
                            f"已寫入文字向量... {min(batch_start + TEXT_EMBED_BATCH_SIZE, total)}/{total}",
                        )
            elif update_text:
                if not embed_payload:
                    progress(
                        "embed_text_skip",
                        text_progress or total_files,
                        overall_total,
                        "無需更新文字向量",
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
                    if fd.reuse_image:
                        progress("render_cache", total_files + fi, overall_total, f"使用快取縮圖：{fd.pptx.name}")
                    else:
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
                            if thumb_path:
                                slide.thumb_path = thumb_path

                    for slide in fd.slides:
                        if not slide.thumb_path:
                            continue
                        slide_id = f"{file_id}#{slide.page}"
                        if update_image_vectors:
                            image_paths.append(Path(slide.thumb_path))
                            image_slide_ids.append(slide_id)
            finally:
                self.renderer.end_batch()

            if update_image_vectors and image_paths:
                total = len(image_paths)
                image_vec_dtype = np.float16 if hasattr(np, "float16") else np.float32
                zero_vec = np.zeros(self.emb_cfg.image_dim, dtype=image_vec_dtype)
                for batch_start in range(0, total, IMAGE_EMBED_BATCH_SIZE):
                    if cancel_flag and cancel_flag():
                        return 1, "已取消"
                    batch_paths = image_paths[batch_start : batch_start + IMAGE_EMBED_BATCH_SIZE]
                    batch_ids = image_slide_ids[batch_start : batch_start + IMAGE_EMBED_BATCH_SIZE]
                    if IMAGE_EMBED_PROGRESS_EVERY > 0:
                        progress(
                            "embed_image",
                            total_files + stage2_units,
                            overall_total,
                            f"產生圖片向量中... {batch_start}/{total}",
                        )
                    try:
                        batch_vecs = self.image_embedder.embed_images(
                            batch_paths,
                            dim=self.emb_cfg.image_dim,
                            batch_size=IMAGE_EMBED_BATCH_SIZE,
                        )
                    except Exception as exc:
                        log.warning("圖片向量批次產生失敗：%s", exc)
                        batch_vecs = np.zeros((len(batch_paths), self.emb_cfg.image_dim), dtype=np.float32)
                    batch_vectors_to_append: Dict[str, np.ndarray] = {}
                    for idx, slide_id in enumerate(batch_ids):
                        if idx < len(batch_vecs) and batch_vecs[idx] is not None:
                            vec = np.asarray(batch_vecs[idx], dtype=image_vec_dtype)
                        else:
                            vec = zero_vec
                        batch_vectors_to_append[slide_id] = vec
                    if batch_vectors_to_append:
                        self.store.append_image_vectors(batch_vectors_to_append)
                        image_vectors_written = True
                    if IMAGE_EMBED_PROGRESS_EVERY > 0:
                        progress(
                            "embed_image_write",
                            total_files + stage2_units,
                            overall_total,
                            f"已寫入圖片向量... {min(batch_start + IMAGE_EMBED_BATCH_SIZE, total)}/{total}",
                        )

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

            text_indexed_count = 0
            image_indexed_count = 0
            bm25_indexed_count = 0

            for slide in fd.slides:
                slide_id = f"{file_id}#{slide.page}"
                if update_text:
                    text_value = ""
                    if slide.slide_text is not None:
                        text_value = slide.slide_text.all_text or ""
                    slide_pages[slide_id] = text_value
                    slide_pages_updates += 1
                    save_slide_pages(force=False)
                    if text_value.strip():
                        text_indexed_count += 1
                        bm25_indexed_count += 1

                if update_image and slide.thumb_path:
                    image_indexed_count += 1

            save_slide_pages(force=False)
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

        save_slide_pages(force=True)

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

        try:
            self.store.ensure_vector_files(text=update_text, image=update_image)
        except Exception as exc:
            log.warning("建立向量檔案失敗：%s", exc)

        if update_text:
            try:
                self._sync_legacy_index(manifest, slide_pages)
            except Exception as exc:
                log.warning("同步 index.json 失敗：%s", exc)

        if text_vectors_written:
            try:
                self.store.compact_text_vectors()
            except Exception as exc:
                log.warning("壓縮文字向量檔失敗：%s", exc)
        if image_vectors_written:
            try:
                self.store.compact_image_vectors()
            except Exception as exc:
                log.warning("壓縮圖片向量檔失敗：%s", exc)

        progress("done", overall_total, overall_total, "索引完成")
        return 0, f"已索引 {total_files} 個檔案"

    def _sync_legacy_index(self, manifest: Dict[str, Any], slide_pages: Dict[str, str]) -> None:
        files_map: Dict[str, Dict[str, Any]] = {}
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict):
                continue
            file_id = entry.get("file_id")
            if not file_id:
                continue
            files_map[file_id] = {
                "file_id": file_id,
                "abs_path": entry.get("abs_path", ""),
                "filename": entry.get("filename", ""),
                "slide_count": entry.get("slide_count") or entry.get("slides_count") or 0,
                "indexed": bool(entry.get("indexed")),
                "indexed_at": entry.get("indexed_at"),
                "index_mode": entry.get("index_mode"),
            }

        slides_map: Dict[str, Dict[str, Any]] = {}
        for slide_id, text in slide_pages.items():
            if not isinstance(slide_id, str) or "#" not in slide_id:
                continue
            file_id, page_raw = slide_id.split("#", 1)
            try:
                page = int(page_raw)
            except Exception:
                page = None
            file_entry = files_map.get(file_id, {})
            slides_map[slide_id] = {
                "slide_id": slide_id,
                "file_id": file_id,
                "file_path": file_entry.get("abs_path", ""),
                "filename": file_entry.get("filename", ""),
                "page": page,
                "text_for_bm25": "" if text is None else str(text),
            }

        self.store.save_index(
            {
                "files": files_map,
                "slides": slides_map,
            }
        )
