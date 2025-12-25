# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests
from PySide6.QtCore import QThread, Signal

from app.core.backend_config import get_backend_base_url
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class BackendConfig:
    base_url: str = field(default_factory=get_backend_base_url)
    connect_timeout: float = 3.0
    read_timeout: float = 30.0


class SseWorker(QThread):
    """SSE 訂閱執行緒。"""

    event_received = Signal(dict)
    state_changed = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        reconnect: bool = True,
        read_timeout: float = 60.0,
    ) -> None:
        super().__init__()
        self._url = url
        self._headers = headers or {}
        self._reconnect = reconnect
        self._read_timeout = read_timeout
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                self.state_changed.emit("connecting")
                with requests.get(
                    self._url,
                    headers=self._headers,
                    stream=True,
                    timeout=(5, self._read_timeout),
                ) as resp:
                    resp.raise_for_status()
                    self.state_changed.emit("connected")
                    backoff = 0.5

                    data_lines: list[str] = []
                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if self._stop.is_set():
                            return
                        if raw_line is None:
                            continue
                        line = raw_line.strip("\r")
                        if not line:
                            if data_lines:
                                payload_text = "\n".join(data_lines)
                                data_lines = []
                                try:
                                    obj = json.loads(payload_text)
                                    self.event_received.emit(obj)
                                except Exception as exc:
                                    log.warning("SSE JSON parse failed: %s", exc)
                                    self.error.emit(f"SSE JSON parse failed: {exc}")
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[len("data:") :].strip())
            except Exception as exc:
                state = "reconnecting" if self._reconnect else "disconnected"
                self.state_changed.emit(state)
                self.error.emit(f"SSE disconnected: {exc}")
                if not self._reconnect:
                    return
                time.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, 5.0)


class BackendApiClient:
    def __init__(self, cfg: BackendConfig) -> None:
        self.cfg = cfg

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def health(self) -> bool:
        try:
            resp = requests.get(self._url("/health"), timeout=self.cfg.connect_timeout)
            return resp.status_code == 200
        except Exception as exc:
            log.warning("health failed: %s", exc)
            return False

    def start_index_job(
        self,
        library_root: str,
        plan_mode: str,
        options: Dict[str, Any],
    ) -> Optional[str]:
        try:
            payload = {
                "library_root": library_root,
                "plan_mode": plan_mode,
                "options": options,
            }
            resp = requests.post(
                self._url("/jobs/index"),
                json=payload,
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json().get("job_id")
        except Exception as exc:
            log.exception("start_index_job failed: %s", exc)
            return None

    def pause_job(self, job_id: str) -> bool:
        try:
            resp = requests.post(
                self._url(f"/jobs/{job_id}/pause"),
                timeout=self.cfg.connect_timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.exception("pause_job failed: %s", exc)
            return False

    def resume_job(self, job_id: str) -> bool:
        try:
            resp = requests.post(
                self._url(f"/jobs/{job_id}/resume"),
                timeout=self.cfg.connect_timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.exception("resume_job failed: %s", exc)
            return False

    def cancel_job(self, job_id: str) -> bool:
        try:
            resp = requests.post(
                self._url(f"/jobs/{job_id}/cancel"),
                timeout=self.cfg.connect_timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.exception("cancel_job failed: %s", exc)
            return False

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                self._url(f"/jobs/{job_id}"),
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.exception("get_job failed: %s", exc)
            return None

    def get_library_summary(self, library_root: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                self._url("/library/summary"),
                params={"library_root": library_root},
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.exception("get_library_summary failed: %s", exc)
            return None

    def get_library_files(self, library_root: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                self._url("/library/files"),
                params={"library_root": library_root},
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.exception("get_library_files failed: %s", exc)
            return None

    def get_library_file_pages(self, file_id: int) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                self._url(f"/library/files/{file_id}/pages"),
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.exception("get_library_file_pages failed: %s", exc)
            return None

    def get_library_page(self, page_id: int) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                self._url(f"/library/pages/{page_id}"),
                timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.exception("get_library_page failed: %s", exc)
            return None

    def sse_worker_for_job(self, job_id: str) -> SseWorker:
        url = self._url(f"/jobs/{job_id}/events")
        return SseWorker(url, headers={})
