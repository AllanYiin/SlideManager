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
        st = p.stat()
        out.append(
            FileScan(
                path=str(p.resolve()),
                size_bytes=st.st_size,
                mtime_epoch=int(st.st_mtime),
            )
        )
    return out
