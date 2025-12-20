# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from app.core.logging import get_logger
from app.services.openai_client import OpenAIClient
from app.utils.vectors import normalize_l2, stable_hash_to_vec

log = get_logger(__name__)


@dataclass
class EmbeddingConfig:
    text_model: str = "text-embedding-3-large"
    text_dim: int = 3072
    image_dim: int = 2048


class EmbeddingService:
    def __init__(self, api_key: Optional[str], cfg: EmbeddingConfig):
        self.cfg = cfg
        self.api_key = api_key
        self._client: Optional[OpenAIClient] = OpenAIClient(api_key) if api_key else None

    def has_openai(self) -> bool:
        return self._client is not None

    def embed_text(self, text: str) -> np.ndarray:
        text = (text or "").strip()
        if not text:
            return np.zeros((self.cfg.text_dim,), dtype=np.float32)

        if self._client:
            try:
                vecs = self._client.embed_texts([text], self.cfg.text_model)
                if vecs and len(vecs[0]) >= 1:
                    v = np.asarray(vecs[0], dtype=np.float32)
                    # 容錯：維度對齊
                    if v.size != self.cfg.text_dim:
                        if v.size > self.cfg.text_dim:
                            v = v[: self.cfg.text_dim]
                        else:
                            pad = np.zeros((self.cfg.text_dim - v.size,), dtype=np.float32)
                            v = np.concatenate([v, pad], axis=0)
                    return normalize_l2(v)
            except Exception as e:
                log.warning("OpenAI embeddings 失敗，退化 hash：%s", e)

        return stable_hash_to_vec(text, self.cfg.text_dim)

    def embed_text_batch(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        if self._client:
            try:
                vecs = self._client.embed_texts(texts, self.cfg.text_model)
                out: List[np.ndarray] = []
                for i, t in enumerate(texts):
                    if i < len(vecs):
                        v = np.asarray(vecs[i], dtype=np.float32)
                        if v.size != self.cfg.text_dim:
                            if v.size > self.cfg.text_dim:
                                v = v[: self.cfg.text_dim]
                            else:
                                pad = np.zeros((self.cfg.text_dim - v.size,), dtype=np.float32)
                                v = np.concatenate([v, pad], axis=0)
                        out.append(normalize_l2(v))
                    else:
                        out.append(stable_hash_to_vec(t, self.cfg.text_dim))
                return out
            except Exception as e:
                log.warning("OpenAI batch embeddings 失敗，改用 hash：%s", e)

        return [stable_hash_to_vec((t or "").strip(), self.cfg.text_dim) for t in texts]
