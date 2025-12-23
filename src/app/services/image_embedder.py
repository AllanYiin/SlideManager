# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
from typing import List, Optional

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

FLOAT_DTYPE = np.float16 if hasattr(np, "float16") else np.float32

MODEL_INPUT_SIZE = 224


class ImageEmbedder:
    """圖片向量（512 維）。"""

    def __init__(self, model_path: Optional[Path] = None, dim: int = 512):
        self.dim = int(dim)
        self.model_path = model_path
        self._ort_session = None
        self.input_name = None
        self.output_name = None

        if not model_path:
            raise FileNotFoundError("找不到 ONNX 模型：未指定模型路徑")
        if not model_path.exists():
            raise FileNotFoundError(f"找不到 ONNX 模型: {model_path}")
        if importlib.util.find_spec("numpy.core") is None:
            raise RuntimeError("numpy 安裝不完整或版本過舊，請升級 numpy 至最新版後再啟動圖片模型")
        try:
            numpy_version = version("numpy")
            log.info("ImageEmbedder：偵測到 numpy 版本 %s", numpy_version)
        except PackageNotFoundError:
            numpy_version = "unknown"
            log.warning("ImageEmbedder：無法取得 numpy 版本資訊")
        if importlib.util.find_spec("onnxruntime") is None:
            raise RuntimeError("onnxruntime 未安裝，請安裝對應版本後再啟動圖片模型")

        import onnxruntime as ort

        try:
            self._ort_session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
            self.input_name = self._ort_session.get_inputs()[0].name
            self.output_name = self._ort_session.get_outputs()[0].name
            log.info("ImageEmbedder：已載入 ONNX 模型：%s", model_path)
        except Exception as exc:
            log.exception("[ONNX_ERROR] 載入 ONNX 模型失敗 (%s): %s", model_path, exc)
            raise RuntimeError("載入圖片模型失敗，檔案可能損毀，請重新下載 rerankTexure.onnx") from exc

    def enabled_onnx(self) -> bool:
        return self._ort_session is not None

    def embed_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        try:
            input_data = self._preprocess_image_bytes(image_bytes)
            embedding = self._ort_session.run([self.output_name], {self.input_name: input_data})[0]
            embedding = np.squeeze(embedding, axis=0).astype(FLOAT_DTYPE)
            return self._align_dim(embedding)
        except Exception as exc:
            log.exception("[ONNX_ERROR] 圖片嵌入失敗 (bytes): %s", exc)
            return np.zeros((self.dim,), dtype=FLOAT_DTYPE)

    def embed_images(self, image_paths: List[Path], *, batch_size: int = 16) -> np.ndarray:
        if not image_paths:
            return np.zeros((0, self.dim), dtype=FLOAT_DTYPE)

        outputs: List[np.ndarray] = []
        safe_batch_size = max(1, int(batch_size))
        for start in range(0, len(image_paths), safe_batch_size):
            batch_paths = image_paths[start : start + safe_batch_size]
            try:
                input_data = self._preprocess_images(batch_paths)
                embedding = self._ort_session.run([self.output_name], {self.input_name: input_data})[0]
                embedding = np.asarray(embedding).astype(FLOAT_DTYPE)
                if embedding.ndim == 1:
                    embedding = np.expand_dims(embedding, axis=0)
                outputs.append(self._align_batch_dim(embedding))
            except Exception as exc:
                log.exception("[ONNX_ERROR] 圖片嵌入失敗 (%s): %s", batch_paths, exc)
                outputs.append(np.zeros((len(batch_paths), self.dim), dtype=FLOAT_DTYPE))
        return np.vstack(outputs)

    @staticmethod
    def _preprocess_image_bytes(image_bytes: bytes) -> np.ndarray:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            if img.size != (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE):
                img = img.resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE))
            img_np = np.array(img).astype(np.float32)
        img_np = (img_np - 127.5) / 127.5
        img_np = img_np.transpose(2, 0, 1)
        return np.expand_dims(img_np, axis=0)

    def _align_dim(self, vec: np.ndarray) -> np.ndarray:
        v = np.asarray(vec, dtype=FLOAT_DTYPE).reshape(-1)
        if v.size == self.dim:
            return v
        if v.size > self.dim:
            return v[: self.dim]
        pad = np.zeros((self.dim - v.size,), dtype=FLOAT_DTYPE)
        return np.concatenate([v, pad], axis=0)

    def _align_batch_dim(self, batch: np.ndarray) -> np.ndarray:
        if batch.ndim != 2:
            return np.zeros((0, self.dim), dtype=FLOAT_DTYPE)
        if batch.shape[1] == self.dim:
            return batch
        if batch.shape[1] > self.dim:
            return batch[:, : self.dim]
        pad = np.zeros((batch.shape[0], self.dim - batch.shape[1]), dtype=FLOAT_DTYPE)
        return np.concatenate([batch, pad], axis=1)

    @staticmethod
    def _preprocess_image(img_path: Path) -> np.ndarray:
        from PIL import Image

        with Image.open(img_path) as img:
            img = img.convert("RGB")
            if img.size != (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE):
                img = img.resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE))
            img_np = np.array(img).astype(np.float32)
        img_np = (img_np - 127.5) / 127.5
        img_np = img_np.transpose(2, 0, 1)
        return np.expand_dims(img_np, axis=0)

    @staticmethod
    def _preprocess_images(img_paths: List[Path]) -> np.ndarray:
        from PIL import Image

        batch: List[np.ndarray] = []
        for img_path in img_paths:
            try:
                with Image.open(img_path) as img:
                    img = img.convert("RGB")
                    if img.size != (MODEL_INPUT_SIZE, MODEL_INPUT_SIZE):
                        img = img.resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE))
                    img_np = np.array(img).astype(np.float32)
            except Exception as exc:
                log.exception("[ONNX_ERROR] 讀取圖片失敗 (%s): %s", img_path, exc)
                img_np = np.zeros((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE, 3), dtype=np.float32)
            img_np = (img_np - 127.5) / 127.5
            img_np = img_np.transpose(2, 0, 1)
            batch.append(img_np)
        if not batch:
            return np.zeros((0, 3, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE), dtype=np.float32)
        return np.stack(batch, axis=0)


@dataclass
class ImageModelStatus:
    model_path: Path
    version: str
    available: bool
    detail: str


class ImageEmbeddingService:
    """圖片向量服務（支援模型快取/版本與退化）。"""

    def __init__(
        self,
        cache_dir: Path,
        *,
        model_name: str = "rerankTexure.onnx",
        version: str = "1",
        autoload: bool = True,
    ):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.cache_dir / model_name
        self.version = version
        self._embedder: Optional[ImageEmbedder] = None
        if autoload:
            self._status = self._load_embedder()
        else:
            self._status = ImageModelStatus(self.model_path, self.version, False, "尚未載入圖片模型")
        self._write_metadata()

    def _load_embedder(self) -> ImageModelStatus:
        if not self.model_path.exists():
            log.warning("找不到圖片模型：%s", self.model_path)
            return ImageModelStatus(self.model_path, self.version, False, "找不到模型檔案")
        try:
            self._embedder = ImageEmbedder(self.model_path)
            return ImageModelStatus(self.model_path, self.version, True, "已載入模型")
        except Exception as exc:
            log.error("[ONNX_ERROR] 圖片模型載入失敗：%s", exc)
            return ImageModelStatus(self.model_path, self.version, False, "模型載入失敗")

    def _write_metadata(self) -> None:
        meta = {
            "model_path": str(self.model_path),
            "version": self.version,
            "available": self._status.available,
            "detail": self._status.detail,
        }
        meta_path = self.cache_dir / "image_model.json"
        try:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("寫入模型狀態失敗：%s", exc)

    def status(self) -> ImageModelStatus:
        return self._status

    def reload(self) -> ImageModelStatus:
        """重新載入模型（下載完成後可呼叫）。"""

        self._status = self._load_embedder()
        self._write_metadata()
        return self._status

    def enabled_onnx(self) -> bool:
        return bool(self._embedder and self._embedder.enabled_onnx())

    def embed_image_bytes(self, image_bytes: bytes, *, dim: int) -> np.ndarray:
        if not self._embedder:
            return np.zeros((dim,), dtype=np.float32)
        return self._align_dim(self._embedder.embed_image_bytes(image_bytes), dim).astype(np.float32)

    def embed_images(self, image_paths: List[Path], *, dim: int, batch_size: int = 16) -> np.ndarray:
        if not self._embedder:
            return np.zeros((len(image_paths), dim), dtype=np.float32)
        batch = self._embedder.embed_images(image_paths, batch_size=batch_size).astype(np.float32)
        return self._align_batch_dim(batch, dim)

    @staticmethod
    def _align_dim(vec: np.ndarray, dim: int) -> np.ndarray:
        v = np.asarray(vec, dtype=np.float32).reshape(-1)
        if v.size == dim:
            return v
        if v.size > dim:
            return v[:dim]
        pad = np.zeros((dim - v.size,), dtype=np.float32)
        return np.concatenate([v, pad], axis=0)

    @staticmethod
    def _align_batch_dim(batch: np.ndarray, dim: int) -> np.ndarray:
        if batch.ndim != 2:
            return np.zeros((0, dim), dtype=np.float32)
        if batch.shape[1] == dim:
            return batch
        if batch.shape[1] > dim:
            return batch[:, :dim]
        pad = np.zeros((batch.shape[0], dim - batch.shape[1]), dtype=np.float32)
        return np.concatenate([batch, pad], axis=1)
