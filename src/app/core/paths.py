# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path


def app_home_dir() -> Path:
    """回傳使用者層級的 App 資料夾。

    Windows：%APPDATA%\LocalSlideManager
    其他：~/.local_slide_manager
    """
    import os

    appdata = os.getenv("APPDATA")
    if appdata:
        d = Path(appdata) / "LocalSlideManager"
    else:
        d = Path.home() / ".local_slide_manager"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return app_home_dir() / "settings.json"


def secrets_path() -> Path:
    return app_home_dir() / "secrets.json"
