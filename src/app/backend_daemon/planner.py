from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class FileScan:
    path: str
    size_bytes: int
    mtime_epoch: int


def scan_files_under(root: Path) -> List[FileScan]:
    out: List[FileScan] = []

    for p in root.rglob("*.pptx"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            FileScan(
                path=str(p.resolve()),
                size_bytes=st.st_size,
                mtime_epoch=int(st.st_mtime),
            )
        )
    return out


def scan_specific_files(paths: list[str]) -> List[FileScan]:
    out: List[FileScan] = []
    for raw in paths:
        if not raw:
            continue
        p = Path(raw)
        if not (p.is_file() and p.suffix.lower() in (".pptx",)):

            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            FileScan(
                path=str(p.resolve()),
                size_bytes=st.st_size,
                mtime_epoch=int(st.st_mtime),
            )
        )
    return out
