# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.core.logging import get_logger
from app.services.embedding_service import EmbeddingConfig, EmbeddingService
from app.services.project_store import ProjectStore
from app.utils.text import tokenize
from app.utils.vectors import b64_f32_to_vec, cosine_similarity, normalize_l2

log = get_logger(__name__)


@dataclass
class SearchQuery:
    text: str
    mode: str = "hybrid"  # hybrid | bm25 | vector_text | vector_image | vector_concat
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
        idx = self.store.load_index()
        emb = idx.get("embedding", {})
        self.emb_cfg = EmbeddingConfig(
            text_model=str(emb.get("text_model", "text-embedding-3-small")),
            text_dim=int(emb.get("text_dim", 1536)),
            image_dim=int(emb.get("image_dim", 4096)),
        )
        self.embeddings = EmbeddingService(api_key, self.emb_cfg, cache_dir=self.store.paths.cache_dir)

    def search(self, q: SearchQuery, image_vec: Optional[np.ndarray] = None) -> List[SearchResult]:
        index = self.store.load_index()
        slides = [s for s in index.get("slides", []) if isinstance(s, dict)]
        if not slides:
            return []

        # BM25
        bm25_scores = self._bm25_scores(slides, q.text)

        # Vector
        vec_scores = [0.0] * len(slides)
        if q.mode in {"hybrid", "vector_text", "vector_concat"}:
            qv = self.embeddings.embed_text(q.text)
            vec_scores = self._vector_scores(slides, qv, use_concat=(q.mode == "vector_concat"))
        elif q.mode == "vector_image" and image_vec is not None:
            vec_scores = self._vector_scores(slides, image_vec, use_concat=False, use_image=True)

        # Normalize
        bm_n = self._minmax_norm(bm25_scores)
        vec_n = self._minmax_norm(vec_scores)

        results: List[SearchResult] = []
        for i, s in enumerate(slides):
            bm = bm_n[i]
            vc = vec_n[i]
            if q.mode == "bm25":
                score = bm
            elif q.mode.startswith("vector"):
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
            # 退化：純 token overlap
            qset = set(tokenize(query_text))
            out = []
            for s in slides:
                toks = s.get("bm25_tokens") or tokenize(s.get("all_text", ""))
                tset = set(toks)
                out.append(float(len(qset & tset)))
            return out

        corpus = [s.get("bm25_tokens") or tokenize(s.get("all_text", "")) for s in slides]
        bm25 = BM25Okapi(corpus)
        qtok = tokenize(query_text)
        scores = bm25.get_scores(qtok)
        return [float(x) for x in scores]

    def _vector_scores(
        self,
        slides: List[Dict[str, Any]],
        query_vec: np.ndarray,
        *,
        use_concat: bool = False,
        use_image: bool = False,
    ) -> List[float]:
        qv = normalize_l2(query_vec)
        out: List[float] = []
        for s in slides:
            try:
                if use_image:
                    b = s.get("image_vec")
                    if not b:
                        out.append(0.0)
                        continue
                    v = b64_f32_to_vec(b, self.emb_cfg.image_dim)
                elif use_concat:
                    b = s.get("concat_vec")
                    if not b:
                        out.append(0.0)
                        continue
                    v = b64_f32_to_vec(b, self.emb_cfg.text_dim + self.emb_cfg.image_dim)
                else:
                    b = s.get("text_vec")
                    if not b:
                        out.append(0.0)
                        continue
                    v = b64_f32_to_vec(b, self.emb_cfg.text_dim)
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
