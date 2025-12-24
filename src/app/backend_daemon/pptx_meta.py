from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from typing import Literal

Aspect = Literal["4:3", "16:9", "unknown"]

P_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def detect_aspect_from_pptx(pptx_path: str) -> Aspect:
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            with zf.open("ppt/presentation.xml") as f:
                xml = f.read()
        root = ET.fromstring(xml)
        sldSz = root.find(".//p:sldSz", namespaces=P_NS)
        if sldSz is None:
            return "unknown"
        cx = float(sldSz.attrib.get("cx", "0"))
        cy = float(sldSz.attrib.get("cy", "0"))
        if cx <= 0 or cy <= 0:
            return "unknown"
        ratio = cx / cy
        if abs(ratio - (4 / 3)) < 0.08:
            return "4:3"
        if abs(ratio - (16 / 9)) < 0.12:
            return "16:9"
        return "unknown"
    except Exception:
        return "unknown"
