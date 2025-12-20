# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import numpy as np

from app.core.logging import get_logger
from app.utils.vectors import normalize_l2

log = get_logger(__name__)


class ImageEmbedder:
    """圖片向量（2048 維）。

    規格期待 onnxruntime + CNN ONNX；但為了確保「開箱即用」：
    - 若 cache 內有模型檔（image_embedder.onnx）且能 import onnxruntime：就使用。
    - 否則退化為 hash 向量（可重現、可用於相似度，但效果較差）。
    """

    def __init__(self, model_path: Optional[Path] = None, dim: int = 2048):
        self.dim = int(dim)
        self.model_path = model_path
        self._ort_session = None

        if model_path and model_path.exists():
            try:
                import onnxruntime as ort

                self._ort_session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
                log.info("ImageEmbedder：已載入 ONNX 模型：%s", model_path)
            except Exception as e:
                log.warning("ImageEmbedder：載入 ONNX 失敗，將使用退化 hash 向量：%s", e)
                self._ort_session = None

    def enabled_onnx(self) -> bool:
        return self._ort_session is not None

    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        if self._ort_session:
            try:
                return self._embed_with_onnx(image_bytes)
            except Exception as e:
                log.warning("ONNX 取向量失敗，退回 hash：%s", e)

        h = hashlib.sha256(image_bytes).digest()
        out = np.zeros((self.dim,), dtype=np.float32)
        for i in range(self.dim):
            b = h[i % len(h)]
            out[i] = (b / 255.0) * 2.0 - 1.0
        return normalize_l2(out)

    def _embed_with_onnx(self, image_bytes: bytes) -> np.ndarray:
        # 設計為「可插拔」：此處僅提供一個合理的預設前處理。
        # 真正的 CNN 模型需與其訓練時的前處理一致。
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((224, 224))
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = np.transpose(arr, (2, 0, 1))[None, ...]  # NCHW

        inp_name = self._ort_session.get_inputs()[0].name
        out_name = self._ort_session.get_outputs()[0].name
        vec = self._ort_session.run([out_name], {inp_name: arr})[0]
        vec = np.asarray(vec, dtype=np.float32).reshape(-1)
        if vec.size != self.dim:
            # 容錯：截斷/補 0
            if vec.size > self.dim:
                vec = vec[: self.dim]
            else:
                pad = np.zeros((self.dim - vec.size,), dtype=np.float32)
                vec = np.concatenate([vec, pad], axis=0)
        return normalize_l2(vec)
