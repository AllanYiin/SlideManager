# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.logging import get_logger
from app.services.embedding_service import EmbeddingConfig, EmbeddingService
from app.services.project_store import ProjectStore
from app.utils.text import tokenize
from app.utils.vectors import cosine_similarity, normalize_l2

log = get_logger(__name__)


@dataclass
class SearchQuery:
    text: str
    mode: str = "hybrid"  # text | image | overall | hybrid | bm25
    weight_text: float = 0.5
    weight_vector: float = 0.5
    top_k: int = 50


@dataclass
class SearchResult:
    slide: Dict[str, Any]
    score: float
    bm25: float
    vec: float


class SearchService:
    def __init__(self, store: ProjectStore, api_key: Optional[str]):
        self.store = store
        manifest = self.store.load_manifest()
        emb = manifest.get("embedding", {})
        self.emb_cfg = EmbeddingConfig(
            text_model=str(emb.get("text_model", "text-embedding-3-small")),
            text_dim=int(emb.get("text_dim", 1536)),
            image_dim=int(emb.get("image_dim", 512)),
        )
        self.embeddings = EmbeddingService(api_key, self.emb_cfg, cache_dir=self.store.paths.cache_dir)

    def search(self, q: SearchQuery, image_vec: Optional[np.ndarray] = None) -> List[SearchResult]:
        manifest = self.store.load_manifest()
        files = {
            f.get("file_id"): f
            for f in manifest.get("files", [])
            if isinstance(f, dict) and f.get("file_id")
        }
        slide_pages = self.store.load_slide_pages()
        slides = self._build_slide_views(slide_pages, files, self.store.paths.thumbs_dir)
        if not slides:
            return []

        text_vectors = self.store.load_text_vectors()
        image_vectors = self.store.load_image_vectors()

        bm25_scores = self._bm25_scores(slides, q.text)

        vec_scores = [0.0] * len(slides)
        qv_text: Optional[np.ndarray] = None
        if q.mode in {"text", "hybrid", "overall"}:
            qv_text = self.embeddings.embed_text(q.text)

        if q.mode == "text" and qv_text is not None:
            vec_scores = self._vector_scores(slides, qv_text, text_vectors, image_vectors, use_concat=False)
        elif q.mode in {"overall", "hybrid"}:
            qv_concat = self._build_concat_query(qv_text, image_vec)
            vec_scores = self._vector_scores(slides, qv_concat, text_vectors, image_vectors, use_concat=True)
        elif q.mode == "image" and image_vec is not None:
            vec_scores = self._vector_scores(
                slides,
                image_vec,
                text_vectors,
                image_vectors,
                use_concat=False,
                use_image=True,
            )

        bm_n = self._minmax_norm(bm25_scores)
        vec_n = self._cosine_norm(vec_scores)

        results: List[SearchResult] = []
        for i, s in enumerate(slides):
            bm = bm_n[i]
            vc = vec_n[i]
            if q.mode == "bm25":
                score = bm
            elif q.mode in {"image", "overall"}:
                score = vc
            else:
                score = q.weight_text * bm + q.weight_vector * vc
            results.append(SearchResult(slide=s, score=score, bm25=bm, vec=vc))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: max(1, int(q.top_k))]

    def _bm25_scores(self, slides: List[Dict[str, Any]], query_text: str) -> List[float]:
        try:
            from rank_bm25 import BM25Okapi
        except Exception:
            qset = set(tokenize(query_text))
            out = []
            for s in slides:
                toks = s.get("bm25_tokens") or tokenize(s.get("text_for_bm25", ""))
                tset = set(toks)
                out.append(float(len(qset & tset)))
            return out

        corpus = [s.get("bm25_tokens") or tokenize(s.get("text_for_bm25", "")) for s in slides]
        bm25 = BM25Okapi(corpus)
        qtok = tokenize(query_text)
        scores = bm25.get_scores(qtok)
        return [float(x) for x in scores]

    def _vector_scores(
        self,
        slides: List[Dict[str, Any]],
        query_vec: np.ndarray,
        text_vectors: Dict[str, np.ndarray],
        image_vectors: Dict[str, np.ndarray],
        *,
        use_concat: bool = False,
        use_image: bool = False,
    ) -> List[float]:
        qv = normalize_l2(query_vec)
        out: List[float] = []
        for s in slides:
            try:
                slide_id = s.get("slide_id")
                if use_image:
                    v = image_vectors.get(slide_id)
                    if v is None:
                        out.append(0.0)
                        continue
                elif use_concat:
                    tv = text_vectors.get(slide_id)
                    iv = image_vectors.get(slide_id)
                    if tv is None and iv is None:
                        out.append(0.0)
                        continue
                    if tv is None:
                        tv = np.zeros((self.emb_cfg.text_dim,), dtype=np.float32)
                    if iv is None:
                        iv = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
                    v = np.concatenate([tv.astype(np.float32), iv.astype(np.float32)], axis=0)
                else:
                    v = text_vectors.get(slide_id)
                    if v is None:
                        out.append(0.0)
                        continue
                out.append(cosine_similarity(qv, v))
            except Exception:
                out.append(0.0)
        return out

    def _minmax_norm(self, xs: List[float]) -> List[float]:
        if not xs:
            return []
        mn = min(xs)
        mx = max(xs)
        if abs(mx - mn) < 1e-9:
            return [0.0 for _ in xs]
        return [(x - mn) / (mx - mn) for x in xs]

    def _cosine_norm(self, xs: List[float]) -> List[float]:
        if not xs:
            return []
        return [max(0.0, min(1.0, (x + 1.0) / 2.0)) for x in xs]

    def _build_concat_query(
        self,
        text_vec: Optional[np.ndarray],
        image_vec: Optional[np.ndarray],
    ) -> np.ndarray:
        if text_vec is None:
            text_vec = np.zeros((self.emb_cfg.text_dim,), dtype=np.float32)
        if image_vec is None:
            image_vec = np.zeros((self.emb_cfg.image_dim,), dtype=np.float32)
        return np.concatenate([text_vec, image_vec], axis=0)

    @staticmethod
    def _build_slide_views(
        slide_pages: Dict[str, str],
        files: Dict[str, Any],
        thumbs_dir: Optional[str | Path] = None,
    ) -> List[Dict[str, Any]]:
        slides: List[Dict[str, Any]] = []
        for slide_id, text in slide_pages.items():
            if not isinstance(slide_id, str):
                continue
            if "#" not in slide_id:
                continue
            file_id, page_raw = slide_id.split("#", 1)
            try:
                page = int(page_raw)
            except Exception:
                page = None
            file_entry = files.get(file_id, {}) if isinstance(files.get(file_id), dict) else {}
            thumb_path = None
            if thumbs_dir and page:
                thumb_candidate = Path(thumbs_dir) / file_id / f"{page}.png"
                if thumb_candidate.exists():
                    thumb_path = str(thumb_candidate)
            text_value = "" if text is None else str(text)
            slides.append(
                {
                    "slide_id": slide_id,
                    "file_id": file_id,
                    "file_path": file_entry.get("abs_path", ""),
                    "filename": file_entry.get("filename", ""),
                    "page": page,
                    "title": "",
                    "text_for_bm25": text_value,
                    "all_text": text_value,
                    "bm25_tokens": tokenize(text_value),
                    "thumb_path": thumb_path,
                    "flags": {},
                }
            )
        return slides
