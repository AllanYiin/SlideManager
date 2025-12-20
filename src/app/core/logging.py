# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOGGER_INITIALIZED = False


def setup_logging(log_dir: str | None = None) -> None:
    """初始化 logging。

    - logs/app.log：主要 log。
    - 不記錄敏感資訊（API Key）。
    """
    global _LOGGER_INITIALIZED
    if _LOGGER_INITIALIZED:
        return

    base = Path(log_dir) if log_dir else Path.cwd() / "logs"
    base.mkdir(parents=True, exist_ok=True)
    log_path = base / "app.log"

    level = logging.INFO
    if os.environ.get("LSM_DEBUG", "").strip() in {"1", "true", "TRUE", "yes", "YES"}:
        level = logging.DEBUG

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    root = logging.getLogger()
    root.setLevel(level)

    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt))

    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(logging.Formatter(fmt))

    root.addHandler(fh)
    root.addHandler(sh)

    _LOGGER_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
