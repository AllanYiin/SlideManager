from __future__ import annotations

import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Tuple

A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

_ws_re = re.compile(r"\s+")
_zero_width = "\u200b"


def extract_text_from_slide_xml(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    texts = []
    for t in root.findall(".//a:t", namespaces=A_NS):
        if t.text:
            texts.append(t.text)
    return "\n".join(texts)


def normalize_text(s: str) -> str:
    s = s.replace(_zero_width, "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_ws_re.sub(" ", line).strip() for line in s.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def fast_text_sig(norm_text: str) -> str:
    h = hashlib.blake2b(norm_text.encode("utf-8"), digest_size=8)
    return h.hexdigest()


def extract_page_text(pptx_path: str, page_no: int) -> Tuple[str, str, str]:
    slide_name = f"ppt/slides/slide{page_no}.xml"
    with zipfile.ZipFile(pptx_path) as zf:
        with zf.open(slide_name) as f:
            xml = f.read()
    raw = extract_text_from_slide_xml(xml)
    norm = normalize_text(raw)
    sig = fast_text_sig(norm) if norm else ""
    return raw, norm, sig
