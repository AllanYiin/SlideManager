# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import hashlib
from typing import List

import numpy as np


def vec_to_b64_f32(vec: np.ndarray) -> str:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    return base64.b64encode(v.tobytes()).decode("ascii")


def b64_f32_to_vec(b64: str, dim: int) -> np.ndarray:
    raw = base64.b64decode(b64.encode("ascii"))
    v = np.frombuffer(raw, dtype=np.float32)
    if v.size != dim:
        # 容錯：若尺寸不符，以截斷/補 0 讓 UI 不壞
        if v.size > dim:
            v = v[:dim]
        else:
            pad = np.zeros((dim - v.size,), dtype=np.float32)
            v = np.concatenate([v, pad], axis=0)
    return v


def normalize_l2(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v) + 1e-12)
    return v / n


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = normalize_l2(a)
    b = normalize_l2(b)
    return float(np.dot(a, b))


def stable_hash_to_vec(text: str, dim: int) -> np.ndarray:
    """不依賴外部模型的退化向量：用 sha256 產生可重現的 float32 向量。"""
    h = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
    out = np.zeros((dim,), dtype=np.float32)
    # 以循環方式填充
    for i in range(dim):
        b = h[i % len(h)]
        out[i] = (b / 255.0) * 2.0 - 1.0
    return normalize_l2(out)


def chunked(seq: List, n: int) -> List[List]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]
