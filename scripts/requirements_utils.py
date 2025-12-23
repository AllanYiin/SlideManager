from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Iterable, List


_REQ_NAME_PATTERN = re.compile(r"[<=>!~\\[]")

_REQ_TO_MODULES: Dict[str, List[str]] = {
    "pillow": ["pil"],
    "python-pptx": ["pptx"],
    "rank-bm25": ["rank_bm25"],
    "pymupdf": ["fitz"],
    "pywin32": ["win32com", "pythoncom", "pywintypes"],
    "pyside6": ["pyside6"],
}


def parse_requirements(requirements_path: Path) -> list[str]:
    names: list[str] = []
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        name = _REQ_NAME_PATTERN.split(line, 1)[0].strip()
        if name:
            names.append(_normalize_requirement(name))
    return names


def requirement_to_modules(requirement_name: str) -> list[str]:
    normalized = _normalize_requirement(requirement_name)
    modules = _REQ_TO_MODULES.get(normalized)
    if modules:
        return modules
    return [normalized.replace("-", "_")]


def requirement_module_map(requirements: Iterable[str]) -> Dict[str, List[str]]:
    return {name: requirement_to_modules(name) for name in requirements}


def _normalize_requirement(name: str) -> str:
    return name.strip().lower().replace("_", "-")
