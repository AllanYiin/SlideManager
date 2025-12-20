# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import List, Protocol, Tuple

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class RenderResult:
    ok: bool
    thumbs: List[Path]
    message: str


class Renderer(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        ...

    def status_message(self) -> str:
        ...


class LibreOfficeRenderer:
    name = "libreoffice"

    def __init__(self) -> None:
        self._soffice = shutil.which("soffice")

    def available(self) -> bool:
        return bool(self._soffice)

    def status_message(self) -> str:
        return self._soffice or "未偵測"

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        if not self._soffice:
            return []
        cmd = [
            self._soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            "--convert-to",
            "png",
            "--outdir",
            str(out_dir),
            str(pptx_path),
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        if p.returncode != 0:
            raise RuntimeError(p.stdout[-2000:])
        return sorted(out_dir.glob("*.png"))


class WindowsComRenderer:
    name = "windows_com"

    def available(self) -> bool:
        return os.name == "nt" and find_spec("win32com") is not None

    def status_message(self) -> str:
        if os.name != "nt":
            return "非 Windows"
        return "可用" if find_spec("win32com") is not None else "未安裝 pywin32"

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        if os.name != "nt":
            return []
        import win32com.client  # type: ignore

        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        powerpoint.Visible = 1
        try:
            presentation = powerpoint.Presentations.Open(str(pptx_path), WithWindow=False)
            try:
                presentation.Export(str(out_dir), "PNG")
            finally:
                presentation.Close()
        finally:
            powerpoint.Quit()
        return sorted(out_dir.glob("*.png"))


class RenderService:
    """縮圖渲染服務（可插拔）。

    規格允許「找不到 renderer 仍可索引文字」。
    這裡採取策略：
    - 優先使用可用的 Renderer（LibreOffice / Windows COM）。
    - 若找不到 renderer，回傳空縮圖但保留狀態。
    """

    def __init__(self, thumbs_dir: Path):
        self.thumbs_dir = thumbs_dir
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)
        self._renderers: List[Renderer] = [LibreOfficeRenderer(), WindowsComRenderer()]

    def status(self) -> dict:
        available = [r for r in self._renderers if r.available()]
        active = available[0] if available else None
        return {
            "available": bool(active),
            "active": active.name if active else "none",
            "available_renderers": [r.name for r in available],
            "status": {r.name: r.status_message() for r in self._renderers},
        }

    def render_pptx(self, pptx_path: Path, file_hash: str, slides_count: int) -> RenderResult:
        available = [r for r in self._renderers if r.available()]
        if not available:
            return RenderResult(ok=False, thumbs=[], message="未偵測到可用的 renderer，已改為純文字索引")

        renderer = available[0]
        try:
            thumbs = self._render_with_renderer(renderer, pptx_path, file_hash)
            if thumbs:
                return RenderResult(ok=True, thumbs=thumbs, message=f"已使用 {renderer.name} 渲染縮圖")
            return RenderResult(ok=False, thumbs=[], message=f"{renderer.name} 未產生縮圖，已改為純文字索引")
        except Exception as e:
            log.warning("[RENDERER_ERROR] Renderer 失敗（%s）：%s", renderer.name, e)
            return RenderResult(ok=False, thumbs=[], message=f"{renderer.name} 渲染失敗，已改為純文字索引")

    def _thumb_path(self, file_hash: str, page: int) -> Path:
        return self.thumbs_dir / f"{file_hash}_p{page:04d}.png"

    @staticmethod
    def _target_thumb_size(size: Tuple[int, int]) -> Tuple[int, int]:
        width, height = size
        if height <= 0 or width <= 0:
            return (320, 240)
        ratio = width / height
        ratio_4_3 = 4 / 3
        ratio_16_9 = 16 / 9
        if abs(ratio - ratio_4_3) <= abs(ratio - ratio_16_9):
            return (320, 240)
        return (320, 180)

    def _resize_thumb(self, src: Path, dst: Path) -> bool:
        from PIL import Image

        with Image.open(src) as img:
            img = img.convert("RGB")
            target = self._target_thumb_size(img.size)
            if img.size != target:
                img.thumbnail(target, Image.LANCZOS)
            img.save(dst, format="PNG", optimize=True)
        return True

    def _render_with_renderer(self, renderer: Renderer, pptx_path: Path, file_hash: str) -> List[Path]:
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            pngs = renderer.render(pptx_path, outdir)
            thumbs: List[Path] = []
            for idx, src in enumerate(pngs, start=1):
                dst = self._thumb_path(file_hash, idx)
                try:
                    if not self._resize_thumb(src, dst):
                        continue
                    thumbs.append(dst)
                except Exception as exc:
                    log.warning("[THUMB_RESIZE_ERROR] 縮圖處理失敗 (%s): %s", src, exc)
                    continue
            return thumbs
