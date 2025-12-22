# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import time
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from app.core.logging import get_logger
from app.services.openai_client import OpenAIClient
from app.utils.vectors import normalize_l2

log = get_logger(__name__)

_TIKTOKEN_AVAILABLE = importlib.util.find_spec("tiktoken") is not None


@dataclass
class EmbeddingConfig:
    text_model: str = "text-embedding-3-small"
    text_dim: int = 1536
    image_dim: int = 512


class EmbeddingService:
    def __init__(self, api_key: Optional[str], cfg: EmbeddingConfig, *, cache_dir: Optional[Path] = None):
        self.cfg = cfg
        self.api_key = api_key
        self._client: Optional[OpenAIClient] = OpenAIClient(api_key) if api_key else None
        self.cache_dir = cache_dir
        self._cache: Dict[str, List[float]] = {}
        self._cache_path = None
        self._max_chars = int(os.getenv("OPENAI_EMBED_MAX_CHARS", "200000") or 200000)
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
        if not self._client:
            return [np.zeros((self.cfg.text_dim,), dtype=np.float32) for _ in texts]
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
            for t, pos in zip(missing_texts, missing_indices):
                vec = self._fetch_embedding_with_retry(t)
                if vec:
                    v = self._align_dim(np.asarray(vec, dtype=np.float32))
                    out[pos] = normalize_l2(v)
                    self._cache[self._cache_key(t)] = self._to_cache_list(v)

                else:
                    out[pos] = np.zeros((self.cfg.text_dim,), dtype=np.float32)
            self._save_cache()

        for i, t in enumerate(cleaned):
            if out[i] is None:
                out[i] = np.zeros((self.cfg.text_dim,), dtype=np.float32)

        return [o if o is not None else np.zeros((self.cfg.text_dim,), dtype=np.float32) for o in out]

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

    def _fetch_embedding_with_retry(self, text: str) -> List[float]:

        if not self._client:
            return []
        delays = [0.5, 1.0, 2.0]
        for attempt, delay in enumerate(delays, start=1):
            try:
                vecs = self._client.embed_texts([text], self.cfg.text_model)
                return vecs[0] if vecs else []
            except Exception as exc:
                log.warning(
                    "[OPENAI_ERROR] OpenAI embeddings 失敗（第 %s 次）：%s | text_id=%s chars=%s est_tokens=%s | text=%s",
                    attempt,
                    exc,
                    self._cache_key(text),
                    len(text),
                    self._estimate_tokens(text),
                    text,
                )
                if attempt < len(delays):
                    time.sleep(delay)
        log.error("[OPENAI_ERROR] OpenAI embeddings 最終失敗，改用零向量")
        return []

    def _split_text(self, text: str) -> List[str]:
        if not text:
            return []
        max_chars = max(int(self._max_chars), 0)
        if max_chars <= 0 or len(text) <= max_chars:
            return [text]
        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                split_at = text.rfind("\n", start, end)
                if split_at == -1:
                    split_at = text.rfind(" ", start, end)
                if split_at > start + int(max_chars * 0.5):
                    end = split_at
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
        return chunks

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        if not _TIKTOKEN_AVAILABLE:
            return max(1, int(len(text) / 4))
        import tiktoken
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            return max(1, int(len(text) / 4))

    def _align_dim(self, v: np.ndarray) -> np.ndarray:
        if v.size != self.cfg.text_dim:
            if v.size > self.cfg.text_dim:
                v = v[: self.cfg.text_dim]
            else:
                pad = np.zeros((self.cfg.text_dim - v.size,), dtype=np.float32)
                v = np.concatenate([v, pad], axis=0)
        return v

    def _to_cache_list(self, v: np.ndarray) -> List[float]:
        arr = np.asarray(v, dtype=np.float32).reshape(-1)
        return [float(x) for x in arr]
