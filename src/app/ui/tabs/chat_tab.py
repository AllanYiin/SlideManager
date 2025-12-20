# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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

        self.btn_send.clicked.connect(self.send)
        self.btn_cancel.clicked.connect(self.cancel_stream)
        self.input.returnPressed.connect(self.send)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self._messages = []
        self._current_assistant_buf = ""
        self.transcript.setText("")

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
        self.input.clear()

        self._append_user(text)
        self._messages.append({"role": "user", "content": text})

        # 先做本機搜尋當作 context
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

        if not (self.ctx.api_key or ""):
            # 無 API Key：只回本機結果
            reply = "我已根據您的問題在本機索引中搜尋，找到以下可能相關的投影片：\n\n" + context
            self._append_assistant(reply)
            self._messages.append({"role": "assistant", "content": reply})
            return

        # 有 API Key：串流回答
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
                "content": f"使用者問題：{text}\n\n相關投影片：\n{context}",
            },
        ]

        self._start_stream(messages)

    def _start_stream(self, messages: List[Dict[str, Any]]):
        self.btn_send.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._streaming = True
        self._current_assistant_buf = ""
        self._cancel_event = threading.Event()
        self.transcript.append("\n助理：")

        def task(_progress_emit):
            # 在背景 thread 跑 asyncio loop
            async def runner():
                from app.services.openai_client import OpenAIClient

                c = OpenAIClient(self.ctx.api_key)
                async for delta in c.stream_responses(
                    messages,
                    model="gpt-4.1",
                    temperature=0.4,
                    max_output_tokens=1024,
                    timeout=60.0,
                    cancel_event=self._cancel_event,
                ):
                    _progress_emit(delta)

            try:
                asyncio.run(runner())
                return 0
            except Exception as e:
                return e

        w = Worker(task, None)
        w.args = (w.signals.progress.emit,)
        w.signals.progress.connect(self._on_stream_delta)
        w.signals.finished.connect(self._on_stream_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_stream_delta(self, delta: object) -> None:
        if self._cancel_event and self._cancel_event.is_set():
            return
        d = str(delta)
        self._current_assistant_buf += d
        self.transcript.moveCursor(self.transcript.textCursor().End)
        self.transcript.insertPlainText(d)
        self.transcript.moveCursor(self.transcript.textCursor().End)

    def _on_stream_done(self, result: object) -> None:
        self.btn_send.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._streaming = False
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
        QMessageBox.critical(self, "發生錯誤", "發生錯誤。請複製以下訊息並發送給您的 AI 助手：\n\n" + tb)
        self.btn_send.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._streaming = False
        self._cancel_event = None

    def cancel_stream(self) -> None:
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.btn_cancel.setEnabled(False)

    def _append_user(self, text: str) -> None:
        self.transcript.append(f"\n使用者：{text}\n")

    def _append_assistant(self, text: str) -> None:
        self.transcript.append(f"\n助理：{text}\n")
