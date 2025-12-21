# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple
import zipfile
from xml.etree import ElementTree as ET

from app.core.errors import ErrorCode
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class SlideText:
    page: int
    title: str
    body: str
    all_text: str


class ExtractionService:
    """以最快路徑抽取 PPTX 文字（直接讀取 slide XML）。"""

    _NS = {
        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }

    def extract(self, pptx_path: Path, *, include_notes: bool = False) -> List[SlideText]:
        try:
            slide_texts = self._extract_pptx_slide_text_fast(pptx_path, include_notes=include_notes)
        except Exception as exc:
            log.error("讀取 PPTX 失敗：%s (%s)", pptx_path, exc)
            return []
        out: List[SlideText] = []
        empty_count = 0
        for page in sorted(slide_texts.keys()):
            text = slide_texts.get(page, "")
            title, body = self._split_title_body(text)
            all_text = text.strip()
            if not all_text:
                empty_count += 1
            out.append(SlideText(page=page, title=title, body=body, all_text=all_text))
        if empty_count:
            log.info("[%s] 投影片文字為空：%s (%s/%s)", ErrorCode.EMPTY_TEXT.value, pptx_path, empty_count, len(out))
        return out

    def _extract_pptx_slide_text_fast(
        self, pptx_path: Path, *, include_notes: bool = False
    ) -> Dict[int, str]:
        if pptx_path.suffix.lower() != ".pptx":
            raise ValueError(f"只支援 .pptx：{pptx_path}")

        results: Dict[int, str] = {}
        try:
            with zipfile.ZipFile(pptx_path, "r") as z:
                slide_files = [
                    name
                    for name in z.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                ]
                slide_files.sort(key=self._natural_key)

                for idx, slide_name in enumerate(slide_files, start=1):
                    xml_bytes = z.read(slide_name)
                    root = ET.fromstring(xml_bytes)
                    texts = [t.text for t in root.findall(".//a:t", self._NS) if t.text]
                    slide_text = "\n".join([line for line in (s.strip() for s in texts) if line])
                    results[idx] = slide_text

                if include_notes:
                    notes_files = [
                        name
                        for name in z.namelist()
                        if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")
                    ]
                    notes_files.sort(key=self._natural_key)
                    for idx, notes_name in enumerate(notes_files, start=1):
                        xml_bytes = z.read(notes_name)
                        root = ET.fromstring(xml_bytes)
                        texts = [t.text for t in root.findall(".//a:t", self._NS) if t.text]
                        notes_text = "\n".join([line for line in (s.strip() for s in texts) if line])
                        if notes_text:
                            prior = results.get(idx, "")
                            if prior:
                                results[idx] = prior + "\n\n[NOTES]\n" + notes_text
                            else:
                                results[idx] = "[NOTES]\n" + notes_text
        except Exception as exc:
            log.error("解析 PPTX 文字失敗：%s (%s)", pptx_path, exc)
            raise
        return results

    @staticmethod
    def _natural_key(s: str) -> Tuple:
        return tuple(int(t) if t.isdigit() else t.lower() for t in re.split(r"(\\d+)", s))

    @staticmethod
    def _split_title_body(text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        lines = [line for line in (l.strip() for l in text.splitlines()) if line]
        if not lines:
            return "", ""
        title = lines[0]
        body = "\n".join(lines[1:]).strip()
        return title, body
