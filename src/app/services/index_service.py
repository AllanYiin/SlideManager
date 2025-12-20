# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
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
from app.utils.vectors import vec_to_b64_f32

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
            if status:
                text_indexed = status.get("text_indexed")
                image_indexed = status.get("image_indexed")
                if text_indexed is False or image_indexed is False:
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
        update_text: bool = True,
        update_image: bool = True,
    ) -> Tuple[int, str]:
        """索引指定檔案（增量）：先移除舊 entries，再寫入新 entries。"""

        overall_start = time.perf_counter()
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

        index = self.store.load_index()
        slides = [s for s in index.get("slides", []) if isinstance(s, dict)]

        # 先移除要重建檔案的舊 slide
        target_paths = {f.get("abs_path") for f in files if f.get("abs_path")}
        prev_entries: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for s in slides:
            file_path = s.get("file_path")
            page = s.get("page")
            if file_path in target_paths and isinstance(page, int):
                prev_entries[(file_path, page)] = s
        slides = [s for s in slides if s.get("file_path") not in target_paths]

        total_files = len(files)
        stage2_units = 0
        if update_text:
            stage2_units += 2
        if update_image:
            stage2_units += 1
        overall_total = total_files * 2 + stage2_units
        render_message = ""
        staged_files: List[FileStageData] = []
        if update_image:
            self.renderer.begin_batch()
        try:
            for fi, f in enumerate(files, start=1):
                if cancel_flag and cancel_flag():
                    return 1, "已取消"
                if not wait_if_paused(fi, overall_total):
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

                if update_image:
                    progress("render", fi, overall_total, f"產生縮圖：{pptx.name}")
                    render_start = time.perf_counter()
                    rr = self.renderer.render_pptx(pptx, file_hash, slides_count=slide_count or len(slide_texts))
                    render_elapsed = time.perf_counter() - render_start
                    render_message = rr.message
                    thumbs = rr.thumbs
                    if not rr.ok:
                        log.warning("[RENDERER_ERROR] %s (%s)", pptx, rr.message)
                        progress("render", fi, overall_total, rr.message)
                else:
                    thumbs = []
                    render_elapsed = 0.0

                if cancel_flag and cancel_flag():
                    return 1, "已取消"
                if not wait_if_paused(fi, overall_total):
                    return 1, "已取消"

                source_slide_count = len(slide_texts) if slide_texts else slide_count
                slide_count = max(slide_count, source_slide_count)
                if update_text and extract_elapsed > 0 and slide_count > 0:
                    extract_time += extract_elapsed
                    extract_pages += slide_count
                if update_image and render_elapsed > 0 and slide_count > 0:
                    render_time += render_elapsed
                    render_pages += slide_count
                if slide_count > 0:
                    processed_pages += slide_count
                file_slides: List[SlideWork] = []
                if update_text and slide_texts:
                    slide_iter = enumerate(slide_texts, start=1)
                else:
                    slide_iter = ((si, None) for si in range(1, slide_count + 1))
                for si, st in slide_iter:
                    thumb_path = str(thumbs[si - 1]) if si - 1 < len(thumbs) else None
                    file_slides.append(
                        SlideWork(
                            file_entry=f,
                            abs_path=abs_path,
                            file_hash=file_hash,
                            filename=pptx.name,
                            page=si,
                            slide_text=st,
                            thumb_path=thumb_path,
                        )
                    )
                staged_files.append(
                    FileStageData(
                        file_entry=f,
                        abs_path=abs_path,
                        file_hash=file_hash,
                        pptx=pptx,
                        slide_texts=slide_texts,
                        thumbs=thumbs,
                        slide_count=slide_count,
                        start_mtime=start_mtime,
                        start_size=start_size,
                        slides=file_slides,
                    )
                )
        finally:
            if update_image:
                self.renderer.end_batch()

        if cancel_flag and cancel_flag():
            return 1, "已取消"

        all_slides: List[SlideWork] = []
        for fd in staged_files:
            for slide in fd.slides:
                slide.index = len(all_slides)
                all_slides.append(slide)

        text_vecs_by_index: List[Optional[np.ndarray]] = [None] * len(all_slides)
        bm25_tokens_by_index: List[Optional[List[str]]] = [None] * len(all_slides)
        image_vecs_by_index: List[Optional[np.ndarray]] = [None] * len(all_slides)

        text_indices: List[int] = []
        text_payload: List[str] = []
        for slide in all_slides:
            if update_text and slide.slide_text is not None:
                text_indices.append(slide.index)
                text_payload.append(slide.slide_text.all_text)

        image_indices: List[int] = []
        image_paths: List[Path] = []
        for slide in all_slides:
            if update_image and slide.thumb_path:
                image_indices.append(slide.index)
                image_paths.append(Path(slide.thumb_path))

        bm25_future = None
        image_future = None
        bm25_progress = total_files + 1 if update_text else None
        text_progress = total_files + 2 if update_text else None
        image_progress = total_files + stage2_units if update_image else None
        with ThreadPoolExecutor(max_workers=2) as executor:
            if update_text and text_payload:
                progress(
                    "bm25_start",
                    bm25_progress or total_files,
                    overall_total,
                    "建立 BM25 索引中...",
                )
                bm25_future = executor.submit(lambda: [tokenize(t) for t in text_payload])
            if update_image and image_paths:
                progress(
                    "embed_image_start",
                    image_progress or total_files,
                    overall_total,
                    "批次產生圖片向量中...",
                )
                image_future = executor.submit(
                    self.image_embedder.embed_images,
                    image_paths,
                    dim=self.emb_cfg.image_dim,
                    batch_size=16,
                )

            if update_text and self.embeddings.has_openai() and text_payload:
                progress(
                    "embed_text",
                    text_progress or total_files,
                    overall_total,
                    "產生文字向量中...",
                )
                text_vecs = self.embeddings.embed_text_batch(text_payload)
                for idx, vec in zip(text_indices, text_vecs):
                    text_vecs_by_index[idx] = vec
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

            if bm25_future:
                try:
                    tokens = bm25_future.result()
                    for idx, tok in zip(text_indices, tokens):
                        bm25_tokens_by_index[idx] = tok
                except Exception as exc:
                    log.warning("BM25 tokens 建立失敗：%s", exc)
            if image_future:
                try:
                    image_vecs = image_future.result()
                    for idx, vec in zip(image_indices, image_vecs):
                        image_vecs_by_index[idx] = vec
                except Exception as exc:
                    log.warning("圖片向量批次產生失敗：%s", exc)

        if cancel_flag and cancel_flag():
            return 1, "已取消"

        for fi, fd in enumerate(staged_files, start=1):
            if cancel_flag and cancel_flag():
                return 1, "已取消"
            if not wait_if_paused(total_files + stage2_units + fi, overall_total):
                return 1, "已取消"

            new_entries = []
            text_indexed_count = 0
            image_indexed_count = 0
            for slide in fd.slides:
                prev = prev_entries.get((slide.abs_path, slide.page))
                prev_title = prev.get("title") if isinstance(prev, dict) else None
                prev_body = prev.get("body") if isinstance(prev, dict) else None
                prev_all_text = prev.get("all_text") if isinstance(prev, dict) else ""
                prev_tokens = prev.get("bm25_tokens") if isinstance(prev, dict) else []
                prev_text_vec = prev.get("text_vec") if isinstance(prev, dict) else None
                prev_image_vec = prev.get("image_vec") if isinstance(prev, dict) else None
                prev_thumb_path = prev.get("thumb_path") if isinstance(prev, dict) else None

                thumb_path = slide.thumb_path
                img_vec_b64 = None

                if update_image and slide.index < len(image_vecs_by_index):
                    img_vec = image_vecs_by_index[slide.index]
                    if img_vec is not None:
                        img_vec_b64 = vec_to_b64_f32(img_vec)

                tv = None
                if update_text and slide.index < len(text_vecs_by_index):
                    tv = text_vecs_by_index[slide.index]
                tv_b64 = vec_to_b64_f32(tv) if tv is not None else None

                has_text_vec = tv is not None
                has_image_vec = img_vec_b64 is not None
                if update_text and slide.slide_text is not None:
                    text_indexed_count += 1
                if update_image and has_image_vec:
                    image_indexed_count += 1

                if not update_text and prev_text_vec:
                    tv_b64 = prev_text_vec
                if not update_image and prev_image_vec:
                    img_vec_b64 = prev_image_vec
                if not update_image and not thumb_path and prev_thumb_path:
                    thumb_path = prev_thumb_path

                if has_text_vec or has_image_vec or tv_b64 or img_vec_b64:
                    tv_concat = None
                    iv_concat = None
                    if tv is not None:
                        tv_concat = tv.astype(np.float32)
                    elif tv_b64:
                        try:
                            tv_concat = np.frombuffer(base64.b64decode(tv_b64.encode("ascii")), dtype=np.float32)
                        except Exception:
                            tv_concat = None
                    if img_vec_b64:
                        try:
                            iv_concat = np.frombuffer(base64.b64decode(img_vec_b64.encode("ascii")), dtype=np.float32)
                        except Exception:
                            iv_concat = None
                    if tv_concat is None:
                        tv_concat = np.zeros((self.emb_cfg.text_dim,), dtype=np.float32)
                    if iv_concat is None:
                        iv_concat = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
                    concat = np.concatenate([tv_concat, iv_concat.astype(np.float32)], axis=0)
                    concat_b64 = vec_to_b64_f32(concat)
                else:
                    concat_b64 = None

                slide_id = f"{slide.file_entry.get('file_id')}_{slide.file_hash}_p{slide.page:04d}"
                if slide.slide_text is None:
                    title = prev_title
                    body = prev_body
                    all_text = prev_all_text
                    bm25_tokens = prev_tokens
                else:
                    title = slide.slide_text.title
                    body = slide.slide_text.body
                    all_text = slide.slide_text.all_text
                    bm25_tokens = bm25_tokens_by_index[slide.index] or tokenize(slide.slide_text.all_text)
                new_entries.append(
                    {
                        "slide_id": slide_id,
                        "file_id": slide.file_entry.get("file_id"),
                        "file_path": slide.abs_path,
                        "file_hash": slide.file_hash,
                        "filename": slide.filename,
                        "page": slide.page,
                        "title": title,
                        "body": body,
                        "all_text": all_text,
                        "bm25_tokens": bm25_tokens,
                        "thumb_path": thumb_path,
                        "text_vec": tv_b64,
                        "image_vec": img_vec_b64,
                        "concat_vec": concat_b64,
                        "indexed_at": int(time.time()),
                    }
                )

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

            slides.extend(new_entries)
            self.catalog.mark_indexed(
                fd.abs_path,
                slides_count=fd.slide_count,
                text_indexed_count=text_indexed_count if update_text else None,
                image_indexed_count=image_indexed_count if update_image else None,
            )
            progress(
                "file_done",
                total_files + stage2_units + fi,
                overall_total,
                f"已更新索引：{fd.pptx.name}",
            )

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

        progress("done", overall_total, overall_total, "索引完成")
        return 0, f"已索引 {total_files} 個檔案"
