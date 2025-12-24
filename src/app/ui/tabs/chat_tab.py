# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger
from app.services.search_service import SearchQuery
from app.ui.async_worker import Worker

log = get_logger(__name__)


class ChatTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None
        self._messages: List[Dict[str, Any]] = []
        self._current_assistant_buf = ""
        self._cancel_event: Optional[threading.Event] = None
        self._streaming = False

        root = QVBoxLayout(self)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        root.addWidget(self.transcript)

        row = QHBoxLayout()
        row.addWidget(QLabel("訊息："))
        self.input = QLineEdit()
        self.input.setPlaceholderText("輸入問題，例如：幫我找『交接流程』相關的投影片")
        row.addWidget(self.input)
        self.btn_send = QPushButton("送出")
        row.addWidget(self.btn_send)
        self.btn_cancel = QPushButton("取消串流")
        self.btn_cancel.setEnabled(False)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

        prog_row = QHBoxLayout()
        self.chat_prog = QProgressBar()
        self.chat_prog.setRange(0, 100)
        self.chat_prog.setValue(0)
        self.chat_prog.setVisible(False)
        self.chat_label = QLabel("")
        prog_row.addWidget(self.chat_prog)
        prog_row.addWidget(self.chat_label)
        root.addLayout(prog_row)

        self.btn_send.clicked.connect(self.send)
        self.btn_cancel.clicked.connect(self.cancel_stream)
        self.input.returnPressed.connect(self.send)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self._messages = []
        self._current_assistant_buf = ""
        self.transcript.setText("")
        self._set_chat_busy(False)

    def send(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return
        if self._streaming:
            QMessageBox.information(self, "正在回覆中", "請等待目前串流完成，或按下「取消串流」。")
            return

        text = (self.input.text() or "").strip()
        if not text:
            return
        if not (self.ctx.api_key or ""):
            msg = "尚未設定 API Key，對話功能無法連線至後端。請到「設定/診斷」設定後重試。"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="warning", timeout_ms=12000)
            QMessageBox.information(self, "尚未設定 API Key", msg)
            return
        self.input.clear()

        self._append_user(text)
        self._messages.append({"role": "user", "content": text})

        self._set_chat_busy(True, "準備回覆中...")

        def task():
            import traceback

            try:
                q = SearchQuery(text=text, mode="hybrid", weight_text=0.5, weight_vector=0.5, top_k=5)
                results = self.ctx.search.search(q)
                context_lines = []
                for i, r in enumerate(results, start=1):
                    s = r.slide
                    snippet = ((s.get("all_text", "") or "")[:200]).replace("\n", " ")
                    context_lines.append(
                        f"[{i}] {s.get('filename')} p{s.get('page')} | {s.get('title','')}. 內容摘要：{snippet}"
                    )
                context = "\n".join(context_lines) if context_lines else "（未找到相關投影片）"
                return {"ok": True, "context": context}
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"搜尋失敗，請稍後再試（{exc}）",
                    "traceback": traceback.format_exc(),
                }

        w = Worker(task)
        w.signals.finished.connect(self._on_context_ready)
        w.signals.error.connect(self._on_context_error)
        self.main_window.thread_pool.start(w)

    def _on_context_ready(self, payload: object) -> None:
        if not isinstance(payload, dict):
            log.error("搜尋回傳格式不正確：%s", payload)
            self._set_chat_busy(False)
            msg = "搜尋失敗，請稍後再試"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="error", timeout_ms=12000)
            QMessageBox.critical(self, "搜尋失敗", msg)
            return
        if not payload.get("ok"):
            tb = payload.get("traceback", "")
            if tb:
                log.error("搜尋失敗\n%s", tb)
            self._set_chat_busy(False)
            msg = payload.get("message") or "搜尋失敗，請稍後再試"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="error", timeout_ms=12000)
            QMessageBox.critical(self, "搜尋失敗", msg)
            return
        context = payload.get("context", "（未找到相關投影片）")

        system_prompt = (
            "你是『個人投影片管理』助理。\n"
            "請用繁體中文回答。\n"
            "你可以引用『相關投影片』中的資訊，並在需要時建議使用者點擊搜尋結果查看原始投影片。\n"
            "若資料不足，請明確說明並提出下一步建議。"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"使用者問題：{self._messages[-1]['content']}\n\n相關投影片：\n{context}",
            },
        ]

        self._start_stream(messages)

    def _on_context_error(self, tb: str) -> None:
        log.error("搜尋背景任務錯誤\n%s", tb)
        self._set_chat_busy(False)
        msg = "搜尋發生錯誤，請查看 logs/app.log"
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast(msg, level="error", timeout_ms=12000)
        QMessageBox.critical(self, "搜尋失敗", msg)

    def _start_stream(self, messages: List[Dict[str, Any]]):
        self._set_chat_busy(False)
        msg = "對話功能已移至後台 daemon，目前 UI 尚未接線。"
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast(msg, level="warning", timeout_ms=12000)
        QMessageBox.information(self, "功能尚未接線", msg)

    def _on_stream_delta(self, delta: object) -> None:
        if self._cancel_event and self._cancel_event.is_set():
            return
        d = str(delta)
        self._current_assistant_buf += d
        self.transcript.moveCursor(self.transcript.textCursor().End)
        self.transcript.insertPlainText(d)
        self.transcript.moveCursor(self.transcript.textCursor().End)

    def _on_stream_done(self, result: object) -> None:
        self._set_chat_busy(False)
        cancelled = bool(self._cancel_event and self._cancel_event.is_set())
        if cancelled:
            self.transcript.append("\n（已取消串流）")
        self._cancel_event = None
        # 保存到 messages
        final = self._current_assistant_buf.strip()
        if final:
            self._messages.append({"role": "assistant", "content": final})

    def _on_error(self, tb: str) -> None:
        log.error("Chat 背景任務錯誤\n%s", tb)
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast("對話背景任務發生錯誤，已寫入 logs/app.log。", level="error", timeout_ms=12000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("發生錯誤")
        box.setText("對話背景任務發生錯誤，已寫入 logs/app.log。您可以重試或提供詳細資訊。")
        box.setDetailedText(tb)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setStandardButtons(QMessageBox.Close)
        box.exec()
        self._set_chat_busy(False)
        self._cancel_event = None

    def cancel_stream(self) -> None:
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.btn_cancel.setEnabled(False)

    def _set_chat_busy(self, busy: bool, message: str = "", *, streaming: bool = False) -> None:
        if busy:
            self.chat_prog.setVisible(True)
            self.chat_prog.setRange(0, 0)
            self.chat_label.setText(message)
        else:
            self.chat_prog.setRange(0, 100)
            self.chat_prog.setValue(0)
            self.chat_prog.setVisible(False)
            self.chat_label.setText("")
        self.btn_send.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy and streaming)
        self._streaming = busy and streaming

    def _append_user(self, text: str) -> None:
        self.transcript.append(f"\n使用者：{text}\n")

    def _append_assistant(self, text: str) -> None:
        self.transcript.append(f"\n助理：{text}\n")
