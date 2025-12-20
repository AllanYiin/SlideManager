# -*- coding: utf-8 -*-

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

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


def _has_pdf2image() -> bool:
    return find_spec("pdf2image") is not None


def _pdf_to_pngs(pdf_path: Path, out_dir: Path) -> List[Path]:
    from pdf2image import convert_from_path

    paths = convert_from_path(
        str(pdf_path),
        output_folder=str(out_dir),
        fmt="png",
        paths_only=True,
    )
    return [Path(p) for p in paths]


class LibreOfficeListener:
    def __init__(self, soffice_path: str, host: str = "127.0.0.1", port: int = 2002) -> None:
        self._soffice_path = soffice_path
        self._host = host
        self._port = port
        self._process: Optional[subprocess.Popen[str]] = None
        atexit.register(self.stop)

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            return
        cmd = [
            self._soffice_path,
            "--headless",
            "--nologo",
            "--norestore",
            "--nofirststartwizard",
            f"--accept=socket,host={self._host},port={self._port},urp;",
        ]
        self._process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process = None

    def _connect(self):
        import uno

        local_ctx = uno.getComponentContext()
        resolver = local_ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver",
            local_ctx,
        )
        last_error: Optional[Exception] = None
        for _ in range(10):
            try:
                return resolver.resolve(
                    f"uno:socket,host={self._host},port={self._port};urp;StarOffice.ComponentContext"
                )
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError("LibreOffice UNO 連線失敗") from last_error

    def convert_to_pdf(self, pptx_path: Path, pdf_path: Path) -> None:
        from com.sun.star.beans import PropertyValue

        self.start()
        ctx = self._connect()
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(
            pptx_path.resolve().as_uri(),
            "_blank",
            0,
            (PropertyValue("Hidden", 0, True, 0),),
        )
        try:
            doc.storeToURL(
                pdf_path.resolve().as_uri(),
                (PropertyValue("FilterName", 0, "impress_pdf_Export", 0),),
            )
        finally:
            doc.close(True)


class LibreOfficeListenerRenderer:
    name = "libreoffice_listener"

    def __init__(self) -> None:
        self._soffice = shutil.which("soffice")
        self._listener: Optional[LibreOfficeListener] = None

    def available(self) -> bool:
        return bool(self._soffice) and find_spec("uno") is not None and _has_pdf2image()

    def status_message(self) -> str:
        if not self._soffice:
            return "未偵測到 LibreOffice"
        if find_spec("uno") is None:
            return "未安裝 UNO"
        if not _has_pdf2image():
            return "未安裝 pdf2image"
        return self._soffice

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        if not self._soffice:
            return []
        if not self._listener:
            self._listener = LibreOfficeListener(self._soffice)
        pdf_path = out_dir / f"{pptx_path.stem}.pdf"
        self._listener.convert_to_pdf(pptx_path, pdf_path)
        return sorted(_pdf_to_pngs(pdf_path, out_dir))


class LibreOfficeCliRenderer:
    name = "libreoffice_cli"

    def __init__(self) -> None:
        self._soffice = shutil.which("soffice")

    def available(self) -> bool:
        return bool(self._soffice) and _has_pdf2image()

    def status_message(self) -> str:
        if not self._soffice:
            return "未偵測到 LibreOffice"
        if not _has_pdf2image():
            return "未安裝 pdf2image"
        return self._soffice

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        if not self._soffice:
            return []
        pdf_path = out_dir / f"{pptx_path.stem}.pdf"
        cmd = [
            self._soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(pptx_path),
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        if p.returncode != 0:
            raise RuntimeError(p.stdout[-2000:])
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice 未產生 PDF")
        return sorted(_pdf_to_pngs(pdf_path, out_dir))


class WindowsComRenderer:
    name = "windows_com"

    def available(self) -> bool:
        return os.name == "nt" and find_spec("win32com") is not None and _has_pdf2image()

    def status_message(self) -> str:
        if os.name != "nt":
            return "非 Windows"
        if find_spec("win32com") is None:
            return "未安裝 pywin32"
        if not _has_pdf2image():
            return "未安裝 pdf2image"
        return "可用"

    def render(self, pptx_path: Path, out_dir: Path) -> List[Path]:
        if os.name != "nt":
            return []
        import win32com.client  # type: ignore
        from win32com.client import gencache  # type: ignore

        pdf_path = out_dir / f"{pptx_path.stem}.pdf"
        try:
            powerpoint = gencache.EnsureDispatch("PowerPoint.Application")
            use_constants = True
        except Exception:
            powerpoint = win32com.client.Dispatch("PowerPoint.Application")
            use_constants = False

        powerpoint.Visible = 1
        try:
            presentation = powerpoint.Presentations.Open(str(pptx_path), WithWindow=False)
            try:
                from win32com.client import constants  # type: ignore

                fixed_format_type = (
                    getattr(constants, "ppFixedFormatTypePDF", 2) if use_constants else 2
                )
                fixed_format_intent = (
                    getattr(constants, "ppFixedFormatIntentScreen", 1) if use_constants else 1
                )
                presentation.ExportAsFixedFormat(
                    str(pdf_path),
                    fixed_format_type,
                    Intent=fixed_format_intent,
                    PrintRange=None,
                )
            finally:
                presentation.Close()
        finally:
            powerpoint.Quit()
        return sorted(_pdf_to_pngs(pdf_path, out_dir))


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
        self._renderers: List[Renderer] = [
            WindowsComRenderer(),
            LibreOfficeListenerRenderer(),
            LibreOfficeCliRenderer(),
        ]

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
