# -*- coding: utf-8 -*-

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class RenderResult:
    ok: bool
    thumbs: List[Path]
    message: str


class RenderService:
    """縮圖渲染服務（可插拔）。

    規格允許「找不到 renderer 仍可索引文字」。
    這裡採取策略：
    - 若偵測到 LibreOffice soffice：嘗試將 pptx 轉成 PNG（每張投影片一張）。
    - 否則：以 placeholder PNG 產生縮圖（含檔名/頁碼），讓 UI 有可用預覽。
    """

    def __init__(self, thumbs_dir: Path):
        self.thumbs_dir = thumbs_dir
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)

    def soffice_path(self) -> Optional[str]:
        return shutil.which("soffice")

    def render_pptx(self, pptx_path: Path, file_hash: str, slides_count: int) -> RenderResult:
        sp = self.soffice_path()
        if sp:
            try:
                thumbs = self._render_with_soffice(sp, pptx_path, file_hash)
                if thumbs:
                    return RenderResult(ok=True, thumbs=thumbs, message="已使用 LibreOffice 渲染縮圖")
            except Exception as e:
                log.warning("LibreOffice 渲染失敗：%s", e)

        # fallback
        thumbs = []
        for i in range(1, max(1, slides_count) + 1):
            try:
                thumbs.append(self._make_placeholder(pptx_path, file_hash, i))
            except Exception as e:
                log.warning("產生 placeholder 縮圖失敗：%s", e)
        return RenderResult(ok=True, thumbs=thumbs, message="已使用 placeholder 縮圖（未偵測/無法使用 renderer）")

    def _thumb_path(self, file_hash: str, page: int) -> Path:
        return self.thumbs_dir / f"{file_hash}_p{page:04d}.png"

    def _render_with_soffice(self, soffice: str, pptx_path: Path, file_hash: str) -> List[Path]:
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                "--convert-to",
                "png",
                "--outdir",
                str(outdir),
                str(pptx_path),
            ]
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
            if p.returncode != 0:
                raise RuntimeError(p.stdout[-2000:])

            # LibreOffice 常見輸出：<name>.png 或 Slide1.png, Slide2.png... 依版本/匯出器而異
            pngs = sorted(outdir.glob("*.png"))
            thumbs: List[Path] = []
            for idx, src in enumerate(pngs, start=1):
                dst = self._thumb_path(file_hash, idx)
                try:
                    dst.write_bytes(src.read_bytes())
                    thumbs.append(dst)
                except Exception:
                    continue
            return thumbs

    def _make_placeholder(self, pptx_path: Path, file_hash: str, page: int) -> Path:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception as e:
            # 若 pillow 不在（理論上會裝），就寫空檔避免崩潰
            dst = self._thumb_path(file_hash, page)
            dst.write_bytes(b"")
            return dst

        w, h = 640, 360
        img = Image.new("RGB", (w, h), color=(245, 245, 245))
        draw = ImageDraw.Draw(img)
        title = pptx_path.name
        lines = [
            "（縮圖 placeholder）",
            f"檔案：{title}",
            f"頁碼：{page}",
        ]
        y = 40
        for line in lines:
            draw.text((40, y), line, fill=(40, 40, 40))
            y += 40

        dst = self._thumb_path(file_hash, page)
        img.save(str(dst), format="PNG")
        return dst
