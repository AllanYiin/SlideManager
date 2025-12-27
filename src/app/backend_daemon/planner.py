from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class FileScan:
    path: str
    size_bytes: int
    mtime_epoch: int


def scan_files_under(root: Path) -> List[FileScan]:
    out: List[FileScan] = []
    try:
        for p in root.rglob("*.pptx"):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError as exc:
                logger.warning("scan_files_under stat failed: %s err=%s", p, exc)
                continue
            out.append(
                FileScan(
                    path=str(p.resolve()),
                    size_bytes=st.st_size,
                    mtime_epoch=int(st.st_mtime),
                )
            )
    except Exception as exc:
        logger.exception("scan_files_under failed: %s", exc)
    logger.info("scan_files_under done root=%s files=%d", root, len(out))
    return out


def scan_specific_files(paths: list[str]) -> List[FileScan]:
    out: List[FileScan] = []
    for raw in paths:
        if not raw:
            continue
        try:
            p = Path(raw)
            if not (p.is_file() and p.suffix.lower() in (".pptx",)):
                logger.info("scan_specific_files skip_non_pptx path=%s", raw)
                continue
            try:
                st = p.stat()
            except OSError as exc:
                logger.warning("scan_specific_files stat failed: %s err=%s", p, exc)
                continue
            out.append(
                FileScan(
                    path=str(p.resolve()),
                    size_bytes=st.st_size,
                    mtime_epoch=int(st.st_mtime),
                )
            )
        except Exception as exc:
            logger.exception("scan_specific_files failed: path=%s err=%s", raw, exc)
    logger.info("scan_specific_files done total=%d input=%d", len(out), len(paths))
    return out
