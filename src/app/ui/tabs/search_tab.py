# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QComboBox,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QTextEdit,
    QProgressBar,
)
from PySide6.QtWidgets import QAbstractItemView
from app.core.logging import get_logger
from app.services.search_service import SearchQuery
from app.ui.async_worker import Worker

log = get_logger(__name__)


class SearchTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None
        self._image_path: Optional[Path] = None
        self._last_results = []
        self._search_inflight = False

        root = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)

        # Left: controls + results
        left = QWidget()
        ll = QVBoxLayout(left)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("文字查詢："))
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("例如：交接流程 / 招募 / roadmap")
        row1.addWidget(self.query_edit)
        self.btn_search = QPushButton("搜尋")
        row1.addWidget(self.btn_search)
        ll.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("模式："))
        self.mode = QComboBox()
        self.mode.addItem("純文字（BM25）", "bm25")
        self.mode.addItem("文字（BM25 + 向量）", "text")
        self.mode.addItem("圖片（向量）", "image")
        self.mode.addItem("整體（文字+圖片向量）", "overall")
        self.mode.addItem("混合（BM25 + 整體向量）", "hybrid")
        row2.addWidget(self.mode)

        row2.addWidget(QLabel("Hybrid 權重（文字→向量）："))
        self.weight = QSlider(Qt.Horizontal)
        self.weight.setRange(0, 100)
        self.weight.setValue(50)
        row2.addWidget(self.weight)
        ll.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_pick_image = QPushButton("選擇圖片（以圖搜圖）")
        self.lbl_image = QLabel("未選擇")
        self.lbl_image.setMinimumWidth(200)
        row3.addWidget(self.btn_pick_image)
        row3.addWidget(self.lbl_image)
        ll.addLayout(row3)

        self.results = QTableWidget(0, 5)
        self.results.setHorizontalHeaderLabels(["縮圖", "分數", "檔名", "頁碼", "標題"])
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.horizontalHeader().setStretchLastSection(True)
        ll.addWidget(self.results)

        prog_row = QHBoxLayout()
        self.search_prog = QProgressBar()
        self.search_prog.setRange(0, 100)
        self.search_prog.setValue(0)
        self.search_prog.setVisible(False)
        self.search_label = QLabel("")
        prog_row.addWidget(self.search_prog)
        prog_row.addWidget(self.search_label)
        ll.addLayout(prog_row)

        # Right: preview
        right = QWidget()
        rl = QVBoxLayout(right)
        self.preview_img = QLabel("預覽")
        self.preview_img.setAlignment(Qt.AlignCenter)
        self.preview_img.setMinimumHeight(240)
        self.preview_img.setStyleSheet("border: 1px solid #ddd;")
        rl.addWidget(self.preview_img)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        rl.addWidget(self.preview_text)

        btn_row = QHBoxLayout()
        self.btn_open_file = QPushButton("開啟檔案位置")
        self.btn_open_file.setEnabled(False)
        btn_row.addWidget(self.btn_open_file)
        btn_row.addStretch(1)
        rl.addLayout(btn_row)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        # Signals
        self.btn_search.clicked.connect(self.do_search)
        self.query_edit.returnPressed.connect(self.do_search)
        self.btn_pick_image.clicked.connect(self.pick_image)
        self.results.itemSelectionChanged.connect(self.on_select_result)
        self.btn_open_file.clicked.connect(self.open_file_location)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self._last_results = []
        self.results.setRowCount(0)
        self.preview_img.setText("預覽")
        self.preview_text.setText("")
        self.btn_open_file.setEnabled(False)
        self._set_search_busy(False)

    def _set_search_busy(self, busy: bool, message: str = "搜尋中...") -> None:
        self._search_inflight = busy
        self.btn_search.setEnabled(not busy)
        self.btn_pick_image.setEnabled(not busy)
        if busy:
            self.search_prog.setVisible(True)
            self.search_prog.setRange(0, 0)
            self.search_label.setText(message)
        else:
            self.search_prog.setRange(0, 100)
            self.search_prog.setValue(0)
            self.search_prog.setVisible(False)
            self.search_label.setText("")

    def pick_image(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "選擇圖片", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not fp:
            return
        self._image_path = Path(fp)
        self.lbl_image.setText(self._image_path.name)

    def do_search(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return
        if self._search_inflight:
            return
        slide_pages = self.ctx.store.load_slide_pages()
        if not slide_pages:
            msg = "尚未建立索引，請先在「檔案庫/索引」執行掃描與索引。"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="warning", timeout_ms=12000)
            QMessageBox.information(self, "尚未建立索引", msg)
            return
        text = (self.query_edit.text() or "").strip()
        mode = self.mode.currentData() or self.mode.currentText()
        if not text and mode != "image":
            QMessageBox.information(self, "缺少查詢", "請輸入文字查詢")
            return
        if not (self.ctx.api_key or "") and mode in {"text", "hybrid", "overall"}:
            msg = "未設定 API Key，向量搜尋已停用，將改用純文字（BM25）搜尋。"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="warning", timeout_ms=12000)
            QMessageBox.information(self, "向量搜尋未啟用", msg)
            mode = "bm25"
            self.mode.setCurrentIndex(self.mode.findData("bm25"))

        m = mode
        wv = self.weight.value() / 100.0
        q = SearchQuery(
            text=text,
            mode=m,
            weight_text=1.0 - wv,
            weight_vector=wv,
            top_k=50,
        )

        image_path = self._image_path
        if m == "image":
            msg = "圖片搜尋已移至後台 daemon，目前 UI 尚未接線。"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="warning", timeout_ms=12000)
            QMessageBox.information(self, "功能尚未接線", msg)
            return

        self._set_search_busy(True)

        def task():
            import traceback

            image_vec = None

            try:
                results = self.ctx.search.search(q, image_vec=image_vec)
                return {"ok": True, "results": results}
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"搜尋失敗，請稍後再試（{exc}）",
                    "traceback": traceback.format_exc(),
                }

        w = Worker(task)
        w.signals.finished.connect(self._on_search_done)
        w.signals.error.connect(self._on_search_error)
        self.main_window.thread_pool.start(w)

    def _on_search_done(self, payload: object) -> None:
        self._set_search_busy(False)
        if not isinstance(payload, dict):
            log.error("搜尋回傳格式不正確：%s", payload)
            msg = "搜尋失敗，請稍後再試"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="error", timeout_ms=12000)
            QMessageBox.critical(self, "搜尋失敗", msg)
            return
        if not payload.get("ok"):
            tb = payload.get("traceback", "")
            log.error("搜尋失敗\n%s", tb)
            msg = payload.get("message") or "搜尋失敗，請稍後再試"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="error", timeout_ms=12000)
            QMessageBox.critical(self, "搜尋失敗", msg)
            return
        self._last_results = payload.get("results", [])
        self.render_results()

    def _on_search_error(self, tb: str) -> None:
        self._set_search_busy(False)
        log.error("搜尋任務錯誤\n%s", tb)
        msg = "搜尋發生錯誤，請查看 logs/app.log"
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast(msg, level="error", timeout_ms=12000)
        QMessageBox.critical(self, "搜尋失敗", msg)

    def render_results(self) -> None:
        self.results.setRowCount(len(self._last_results))
        for r, res in enumerate(self._last_results):
            s = res.slide
            thumb = s.get("thumb_path")

            # thumb
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            if thumb and Path(thumb).exists() and Path(thumb).stat().st_size > 0:
                pix = QPixmap(thumb)
                if not pix.isNull():
                    lbl.setPixmap(pix.scaled(160, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.results.setCellWidget(r, 0, lbl)

            self.results.setItem(r, 1, QTableWidgetItem(f"{res.score:.3f}"))
            self.results.setItem(r, 2, QTableWidgetItem(str(s.get("filename", ""))))
            self.results.setItem(r, 3, QTableWidgetItem(str(s.get("page", ""))))
            self.results.setItem(r, 4, QTableWidgetItem(str(s.get("title", ""))))

        self.results.resizeColumnsToContents()

    def on_select_result(self) -> None:
        sel = self.results.selectionModel().selectedRows()
        if not sel:
            self.btn_open_file.setEnabled(False)
            return
        row = sel[0].row()
        if row < 0 or row >= len(self._last_results):
            return
        s = self._last_results[row].slide

        thumb = s.get("thumb_path")
        if thumb and Path(thumb).exists() and Path(thumb).stat().st_size > 0:
            pix = QPixmap(thumb)
            if not pix.isNull():
                self.preview_img.setPixmap(pix.scaled(800, 450, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.preview_img.setText("（無法載入縮圖）")
        else:
            self.preview_img.setText("（無縮圖）")

        txt = f"檔案：{s.get('file_path','')}\n頁碼：{s.get('page','')}\n\n標題：{s.get('title','')}\n\n內容：\n{s.get('all_text','')}"
        self.preview_text.setText(txt)
        self.btn_open_file.setEnabled(True)

    def open_file_location(self) -> None:
        sel = self.results.selectionModel().selectedRows()
        if not sel:
            return
        row = sel[0].row()
        if row < 0 or row >= len(self._last_results):
            return
        s = self._last_results[row].slide
        p = s.get("file_path")
        if not p:
            return
        try:
            pp = Path(p)
            if os.name == "nt":
                os.startfile(str(pp.parent))  # type: ignore
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(pp.parent)])
        except Exception as e:
            log.exception("開啟檔案位置失敗：%s", e)
            msg = f"開啟檔案位置失敗：{e}"
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(msg, level="error", timeout_ms=12000)
            QMessageBox.critical(self, "開啟失敗", msg)
