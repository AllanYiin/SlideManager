# -*- coding: utf-8 -*-

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.services.backend_client import BackendApiClient, BackendConfig

log = get_logger(__name__)


class BackendDaemonManager:
    def __init__(self, cfg: Optional[BackendConfig] = None, *, root_dir: Optional[Path] = None) -> None:
        self._cfg = cfg or BackendConfig()
        self._client = BackendApiClient(self._cfg)
        self._process: Optional[subprocess.Popen] = None
        self._stdout_file: Optional[object] = None
        self._lock = threading.Lock()
        self._root_dir = root_dir or Path.cwd()

    def ensure_running(self, *, timeout_sec: float = 6.0) -> bool:
        if self._client.health():
            return True
        self._start_daemon_if_needed()
        return self._wait_until_ready(timeout_sec)

    def _wait_until_ready(self, timeout_sec: float) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._client.health():
                return True
            time.sleep(0.5)
        return False

    def _start_daemon_if_needed(self) -> None:
        with self._lock:
            if self._process and self._process.poll() is None:
                return
            try:
                log_dir = self._root_dir / ".slidemanager" / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                stdout_path = log_dir / "backend_daemon.out.log"
                self._stdout_file = open(stdout_path, "a", encoding="utf-8")
                self._process = subprocess.Popen(
                    [sys.executable, "-m", "app.backend_daemon.main"],
                    cwd=str(self._root_dir),
                    stdout=self._stdout_file,
                    stderr=subprocess.STDOUT,
                )
                log.info("已嘗試啟動後台 daemon，PID=%s", self._process.pid)
            except Exception as exc:
                log.exception("啟動後台 daemon 失敗: %s", exc)
