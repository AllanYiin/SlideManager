# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from app.core.paths import settings_path
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class AppSettings:
    schema_version: str = "1.0"
    last_project_dir: str | None = None
    window_geometry_b64: str | None = None
    last_tab_index: int = 0


def load_settings() -> AppSettings:
    p = settings_path()
    if not p.exists():
        return AppSettings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        s = AppSettings()
        s.schema_version = str(data.get("schema_version", s.schema_version))
        s.last_project_dir = data.get("last_project_dir")
        s.window_geometry_b64 = data.get("window_geometry_b64")
        s.last_tab_index = int(data.get("last_tab_index", s.last_tab_index))
        return s
    except Exception as e:
        log.error("讀取 settings.json 失敗：%s", e)
        return AppSettings()


def save_settings(s: AppSettings) -> None:
    p = settings_path()
    try:
        p.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error("寫入 settings.json 失敗：%s", e)
