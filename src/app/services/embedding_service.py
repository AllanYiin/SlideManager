# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from app.core.logging import get_logger
from app.services.openai_client import OpenAIClient
from app.utils.vectors import normalize_l2, stable_hash_to_vec

log = get_logger(__name__)


@dataclass
class EmbeddingConfig:
    text_model: str = "text-embedding-3-small"
    text_dim: int = 1536
    image_dim: int = 4096


class EmbeddingService:
    def __init__(self, api_key: Optional[str], cfg: EmbeddingConfig, *, cache_dir: Optional[Path] = None):
        self.cfg = cfg
        self.api_key = api_key
        self._client: Optional[OpenAIClient] = OpenAIClient(api_key) if api_key else None
        self.cache_dir = cache_dir
        self._cache: Dict[str, List[float]] = {}
        self._cache_path = None
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path = cache_dir / "text_embedding_cache.json"
            self._cache = self._load_cache()

    def has_openai(self) -> bool:
        return self._client is not None

    def embed_text(self, text: str) -> np.ndarray:
        vecs = self.embed_text_batch([text])
        if not vecs:
            return np.zeros((self.cfg.text_dim,), dtype=np.float32)
        return vecs[0]

    def embed_text_batch(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        cleaned = [(t or "").strip() for t in texts]
        out: List[Optional[np.ndarray]] = [None] * len(cleaned)
        missing_texts: List[str] = []
        missing_indices: List[int] = []

        for i, t in enumerate(cleaned):
            if not t:
                out[i] = np.zeros((self.cfg.text_dim,), dtype=np.float32)
                continue
            key = self._cache_key(t)
            cached = self._cache.get(key)
            if cached:
                out[i] = normalize_l2(self._align_dim(np.asarray(cached, dtype=np.float32)))
                continue
            missing_texts.append(t)
            missing_indices.append(i)

        if missing_texts and self._client:
            vecs = self._fetch_embeddings_with_retry(missing_texts)
            for idx, t in enumerate(missing_texts):
                pos = missing_indices[idx]
                if idx < len(vecs):
                    v = self._align_dim(np.asarray(vecs[idx], dtype=np.float32))
                    out[pos] = normalize_l2(v)
                    self._cache[self._cache_key(t)] = v.tolist()
                else:
                    out[pos] = stable_hash_to_vec(t, self.cfg.text_dim)
            self._save_cache()

        for i, t in enumerate(cleaned):
            if out[i] is None:
                out[i] = stable_hash_to_vec(t, self.cfg.text_dim)

        return [o if o is not None else stable_hash_to_vec("", self.cfg.text_dim) for o in out]

    def _cache_key(self, text: str) -> str:
        h = hashlib.sha256()
        h.update(self.cfg.text_model.encode("utf-8"))
        h.update(b"|")
        h.update(text.encode("utf-8", errors="ignore"))
        return h.hexdigest()

    def _load_cache(self) -> Dict[str, List[float]]:
        if not self._cache_path or not self._cache_path.exists():
            return {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {k: v for k, v in raw.items() if isinstance(v, list)}
        except Exception as exc:
            log.warning("讀取 embedding cache 失敗：%s", exc)
        return {}

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            payload = json.dumps(self._cache, ensure_ascii=False)
            self._cache_path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            log.warning("寫入 embedding cache 失敗：%s", exc)

    def _fetch_embeddings_with_retry(self, texts: List[str]) -> List[List[float]]:
        if not self._client:
            return []
        delays = [0.5, 1.0, 2.0]
        for attempt, delay in enumerate(delays, start=1):
            try:
                return self._client.embed_texts(texts, self.cfg.text_model)
            except Exception as exc:
                log.warning("[OPENAI_ERROR] OpenAI embeddings 失敗（第 %s 次）：%s", attempt, exc)
                if attempt < len(delays):
                    time.sleep(delay)
        log.error("[OPENAI_ERROR] OpenAI embeddings 最終失敗，改用 fallback 向量")
        return []

    def _align_dim(self, v: np.ndarray) -> np.ndarray:
        if v.size != self.cfg.text_dim:
            if v.size > self.cfg.text_dim:
                v = v[: self.cfg.text_dim]
            else:
                pad = np.zeros((self.cfg.text_dim - v.size,), dtype=np.float32)
                v = np.concatenate([v, pad], axis=0)
        return v
