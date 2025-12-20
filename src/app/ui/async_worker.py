# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)  # result
    error = Signal(str)        # traceback text
    progress = Signal(object)  # arbitrary payload


class Worker(QRunnable):
    """在 QThreadPool 執行任務，避免 UI 卡死。"""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            # 將 progress emitter 注入
            if "_progress" in self.kwargs:
                # 使用者自帶，不覆蓋
                pass
            result = self.fn(*self.args, **self.kwargs)
            self.signals.finished.emit(result)
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self.signals.error.emit(tb)
