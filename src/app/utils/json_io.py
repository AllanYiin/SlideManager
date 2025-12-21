# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import time
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)


def atomic_write_json(path: Path, data: object, *, keep_bak: bool = True) -> None:
    """原子寫入 JSON：先寫 temp 再 replace。

    - 會產生 .bak 以利復原（可選）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")

    if keep_bak and path.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak = path.with_suffix(path.suffix + f".{ts}.bak")
        try:
            bak.write_bytes(path.read_bytes())
        except Exception as e:
            log.warning("寫入 .bak 失敗：%s", e)

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            tmp.replace(path)
            last_err = None
            break
        except PermissionError as e:
            last_err = e
            log.warning("原子寫入重試中（%s/3）：%s", attempt, e)
            time.sleep(0.2 * attempt)

    if last_err is not None:
        log.exception("原子寫入 JSON 失敗：%s (%s)", path, last_err)
        raise last_err


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("[JSON_ERROR] 讀取 JSON 失敗：%s (%s)", path, e)
        return default
