# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

import zipfile

from app.core.logging import get_logger

log = get_logger(__name__)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_dt(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _read_core_properties_from_zip(zip_file: zipfile.ZipFile) -> Dict[str, Any]:
    try:
        core_xml = zip_file.read("docProps/core.xml")
    except KeyError:
        return {
            "title": None,
            "author": None,
            "created_epoch": None,
            "modified_epoch": None,
        }
    root = ET.fromstring(core_xml)
    core_properties = {
        "title": None,
        "author": None,
        "created_epoch": None,
        "modified_epoch": None,
    }
    for elem in root.iter():
        name = _local_name(elem.tag)
        if name == "title":
            core_properties["title"] = elem.text
        elif name == "creator":
            core_properties["author"] = elem.text
        elif name == "created":
            core_properties["created_epoch"] = _parse_dt(elem.text)
        elif name == "modified":
            core_properties["modified_epoch"] = _parse_dt(elem.text)
    return core_properties


def _read_slide_count_from_zip(zip_file: zipfile.ZipFile) -> Optional[int]:
    try:
        app_xml = zip_file.read("docProps/app.xml")
    except KeyError:
        return None
    root = ET.fromstring(app_xml)
    for elem in root.iter():
        if _local_name(elem.tag) == "Slides" and elem.text and elem.text.isdigit():
            return int(elem.text)
    return None


def _read_metadata_from_zip(pptx_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(pptx_path) as zip_file:
            core_properties = _read_core_properties_from_zip(zip_file)
            slide_count = _read_slide_count_from_zip(zip_file)
            return {"slide_count": slide_count, "core_properties": core_properties}
    except (zipfile.BadZipFile, OSError, ET.ParseError):
        return None


def read_pptx_metadata(pptx_path: Path) -> Dict[str, Any]:
    """讀取 PPTX metadata（core properties + slide_count）。"""
    from pptx import Presentation

    try:
        prs = Presentation(str(pptx_path))
        props = prs.core_properties
        core_properties = {
            "title": props.title,
            "author": props.author,
            "created_epoch": int(props.created.timestamp()) if props.created else None,
            "modified_epoch": int(props.modified.timestamp()) if props.modified else None,
        }
        return {
            "slide_count": len(prs.slides),
            "core_properties": core_properties,
        }
    except Exception as e:
        fallback = _read_metadata_from_zip(pptx_path)
        if fallback is not None:
            return fallback
        log.warning("讀取 PPTX metadata 失敗：%s (%s)", pptx_path, e)
        return {"slide_count": None, "core_properties": None}
