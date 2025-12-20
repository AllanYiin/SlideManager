# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger
from app.ui.async_worker import Worker

log = get_logger(__name__)


class LibraryTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None

        self._cancel_index = False
        self._pause_index = False
        self._last_action = None
        self._last_action_label = ""
        self._scan_files_cache: List[Dict[str, Any]] = []
        self._scan_count = 0

        root = QHBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        root.addWidget(split)

        # Left panel: whitelist dirs
        left = QWidget()
        left_layout = QVBoxLayout(left)
        gb = QGroupBox("白名單目錄")
        gbl = QVBoxLayout(gb)
        self.dir_list = QListWidget()
        gbl.addWidget(self.dir_list)

        btn_row = QHBoxLayout()
        self.btn_add_dir = QPushButton("新增")
        self.btn_remove_dir = QPushButton("移除")
        self.btn_toggle_enabled = QPushButton("啟用/停用")
        self.btn_toggle_recursive = QPushButton("遞迴/僅此層")
        btn_row.addWidget(self.btn_add_dir)
        btn_row.addWidget(self.btn_remove_dir)
        btn_row.addWidget(self.btn_toggle_enabled)
        btn_row.addWidget(self.btn_toggle_recursive)
        gbl.addLayout(btn_row)
        left_layout.addWidget(gb)

        self.btn_scan = QPushButton("掃描檔案")
        self.btn_index_needed = QPushButton("開始索引（需要者）")
        self.btn_index_selected = QPushButton("開始索引（選取檔案）")
        self.btn_clear_missing = QPushButton("清理缺失項目")
        self.btn_pause = QPushButton("暫停")
        self.btn_cancel = QPushButton("取消")
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setEnabled(False)

        left_layout.addWidget(self.btn_scan)
        left_layout.addWidget(self.btn_index_needed)
        left_layout.addWidget(self.btn_index_selected)
        left_layout.addWidget(self.btn_clear_missing)
        left_layout.addWidget(self.btn_pause)
        left_layout.addWidget(self.btn_cancel)
        left_layout.addStretch(1)

        # Right panel: file table + progress
        right = QWidget()
        right_layout = QVBoxLayout(right)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("篩選："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("輸入檔名關鍵字")
        filter_row.addWidget(self.filter_edit)
        right_layout.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["檔名", "路徑", "修改時間", "大小", "狀態", "投影片數"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        right_layout.addWidget(self.table)

        prog_row = QHBoxLayout()
        self.prog = QProgressBar()
        self.prog.setValue(0)
        self.prog_label = QLabel("就緒")
        prog_row.addWidget(self.prog)
        prog_row.addWidget(self.prog_label)
        right_layout.addLayout(prog_row)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)

        # Signals
        self.btn_add_dir.clicked.connect(self.add_dir)
        self.btn_remove_dir.clicked.connect(self.remove_dir)
        self.btn_toggle_enabled.clicked.connect(self.toggle_dir_enabled)
        self.btn_toggle_recursive.clicked.connect(self.toggle_dir_recursive)
        self.btn_scan.clicked.connect(self.scan_files)
        self.btn_index_needed.clicked.connect(self.start_index_needed)
        self.btn_index_selected.clicked.connect(self.start_index_selected)
        self.btn_clear_missing.clicked.connect(self.clear_missing_files)
        self.btn_pause.clicked.connect(self.toggle_pause_indexing)
        self.btn_cancel.clicked.connect(self.cancel_indexing)
        self.filter_edit.textChanged.connect(self.refresh_table)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self.refresh_dirs()
        self.refresh_table()

    # ---------- whitelist dirs ----------
    def refresh_dirs(self) -> None:
        self.dir_list.clear()
        if not self.ctx:
            return
        dirs = self.ctx.catalog.get_whitelist_entries()
        for d in dirs:
            path = d.get("path", "")
            enabled = "啟用" if d.get("enabled", True) else "停用"
            recursive = "遞迴" if d.get("recursive", True) else "僅此層"
            label = f"{path}  ({enabled} / {recursive})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            self.dir_list.addItem(item)

    def add_dir(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return
        d = QFileDialog.getExistingDirectory(self, "選擇要納入的資料夾")
        if not d:
            return
        self.ctx.catalog.add_whitelist_dir(d)
        self.refresh_dirs()

    def remove_dir(self) -> None:
        if not self.ctx:
            return
        item = self.dir_list.currentItem()
        if not item:
            return
        p = item.data(Qt.UserRole) or item.text()
        self.ctx.catalog.remove_whitelist_dir(p)
        self.refresh_dirs()

    def toggle_dir_enabled(self) -> None:
        if not self.ctx:
            return
        item = self.dir_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole) or item.text()
        entries = self.ctx.catalog.get_whitelist_entries()
        target = next((e for e in entries if e.get("path") == path), None)
        if not target:
            return
        self.ctx.catalog.set_whitelist_enabled(path, not target.get("enabled", True))
        self.refresh_dirs()

    def toggle_dir_recursive(self) -> None:
        if not self.ctx:
            return
        item = self.dir_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole) or item.text()
        entries = self.ctx.catalog.get_whitelist_entries()
        target = next((e for e in entries if e.get("path") == path), None)
        if not target:
            return
        self.ctx.catalog.set_whitelist_recursive(path, not target.get("recursive", True))
        self.refresh_dirs()

    # ---------- scan ----------
    def scan_files(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return

        self._set_last_action("掃描檔案", self.scan_files)
        self.prog_label.setText("掃描中...")
        self.prog.setRange(0, 0)
        self._scan_files_cache = []
        self._scan_count = 0

        def task(_progress_emit):
            return self.ctx.catalog.scan(on_progress=_progress_emit, progress_every=10)

        w = Worker(task, None)
        w.args = (w.signals.progress.emit,)
        w.signals.progress.connect(self._on_scan_progress)
        w.signals.finished.connect(self._on_scan_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_scan_progress(self, payload: object) -> None:
        try:
            if not isinstance(payload, dict):
                return
            batch = payload.get("batch", [])
            count = int(payload.get("count", 0))
            if isinstance(batch, list) and batch:
                self._scan_files_cache.extend(batch)
            self._scan_count = count
            self.prog_label.setText(f"掃描中... 已掃描 {self._scan_count} 筆")
            self._refresh_table_with_files(self._scan_files_cache)
        except Exception:
            pass

    def _on_scan_done(self, _result: object) -> None:
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("掃描完成")
        self._scan_files_cache = []
        self._scan_count = 0
        self.refresh_table()
        self.refresh_dirs()
        cat = self.ctx.store.load_catalog() if self.ctx else {}
        scan_errors = cat.get("scan_errors") if isinstance(cat, dict) else None
        if scan_errors:
            lines = []
            for err in scan_errors:
                if not isinstance(err, dict):
                    continue
                code = err.get("code", "")
                path = err.get("path", "")
                msg = err.get("message", "")
                lines.append(f"[{code}] {path}：{msg}")
            detail = "\n".join(lines)
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(
                    "掃描完成，但部分路徑無法存取，已略過（可查看詳細資訊）。",
                    level="warning",
                    timeout_ms=12000,
                )
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("掃描完成（部分路徑無法存取）")
            box.setText("部分白名單路徑無法存取，已略過。您可以調整權限或路徑後重試。")
            box.setDetailedText(detail)
            box.setStandardButtons(QMessageBox.Retry | QMessageBox.Close)
            box.setDefaultButton(QMessageBox.Retry)
            box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if box.exec() == QMessageBox.Retry:
                self.scan_files()

    # ---------- table ----------
    def refresh_table(self) -> None:
        if not self.ctx:
            self.table.setRowCount(0)
            return
        cat = self.ctx.store.load_catalog()
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]
        self._refresh_table_with_files(files)

    def _refresh_table_with_files(self, files: List[Dict[str, Any]]) -> None:
        kw = (self.filter_edit.text() or "").strip().lower()
        if kw:
            files = [f for f in files if kw in (f.get("filename", "").lower())]

        self.table.setRowCount(len(files))
        for r, f in enumerate(files):
            self._set_row(r, f)

    def _set_row(self, r: int, f: Dict[str, Any]) -> None:
        fn = f.get("filename", "")
        path = f.get("abs_path", "")
        mtime = f.get("modified_time")
        try:
            dt = datetime.datetime.fromtimestamp(int(mtime))
            mtime_s = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            mtime_s = ""
        size = f.get("size", 0)
        size_s = f"{int(size)/1024/1024:.1f} MB" if size else ""
        status = self._status_text(f)
        slides = f.get("slide_count")
        slides_s = str(slides) if slides is not None else "-"

        tooltip = "\n".join(
            [
                f"檔名：{fn}",
                f"路徑：{path}",
                f"修改時間：{mtime_s or '-'}",
                f"大小：{size_s or '-'}",
                f"狀態：{status or '-'}",
                f"投影片數：{slides_s}",
            ]
        )
        for c, val in enumerate([fn, path, mtime_s, size_s, status, slides_s]):
            it = QTableWidgetItem(str(val))
            it.setFlags(it.flags() ^ Qt.ItemIsEditable)
            it.setToolTip(tooltip)
            self.table.setItem(r, c, it)

    def _status_text(self, f: Dict[str, Any]) -> str:
        if f.get("missing"):
            return "缺失"
        status = f.get("index_status") if isinstance(f.get("index_status"), dict) else {}
        if status.get("last_error"):
            return "索引錯誤"
        indexed = bool(status.get("indexed")) if status else bool(f.get("indexed"))
        if not indexed:
            return "未索引"
        mtime = int(f.get("modified_time") or 0)
        index_mtime = int(status.get("index_mtime_epoch") or 0)
        slide_count = f.get("slide_count")
        index_slide_count = status.get("index_slide_count")
        if mtime > index_mtime:
            return "需要索引"
        if slide_count is not None and index_slide_count is not None:
            try:
                if int(slide_count) != int(index_slide_count):
                    return "需要索引"
            except Exception:
                return "需要索引"
        if status:
            text_indexed = status.get("text_indexed")
            image_indexed = status.get("image_indexed")
            if text_indexed is False and image_indexed is False:
                return "文字/圖像缺失"
            if text_indexed is False:
                return "缺文字索引"
            if image_indexed is False:
                return "缺圖像索引"
        return "已索引"

    def selected_files(self) -> List[Dict[str, Any]]:
        if not self.ctx:
            return []
        cat = self.ctx.store.load_catalog()
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]
        path_to_file = {f.get("abs_path"): f for f in files if f.get("abs_path")}

        selected = []
        for idx in self.table.selectionModel().selectedRows():
            path = self.table.item(idx.row(), 1).text()
            if path in path_to_file:
                selected.append(path_to_file[path])
        return selected

    # ---------- indexing ----------
    def _choose_index_mode(self) -> tuple[bool, bool] | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("選擇索引類型")
        box.setText("請選擇要更新的索引類型：")
        btn_full = box.addButton("完整索引（文字 + 圖像）", QMessageBox.AcceptRole)
        btn_text = box.addButton("只更新文字索引", QMessageBox.AcceptRole)
        btn_image = box.addButton("只更新圖像索引", QMessageBox.AcceptRole)
        btn_cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(btn_full)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
        clicked = box.clickedButton()
        if clicked == btn_cancel:
            return None
        if clicked == btn_text:
            return True, False
        if clicked == btn_image:
            return False, True
        return True, True

    def start_index_needed(self) -> None:
        if not self.ctx:
            return
        self.ctx.catalog.scan()
        files = self.ctx.indexer.compute_needed_files()
        if not files:
            QMessageBox.information(self, "不需要索引", "目前沒有需要更新的檔案")
            return
        mode = self._choose_index_mode()
        if not mode:
            return
        update_text, update_image = mode
        self._set_last_action("開始索引（需要者）", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)

    def start_index_selected(self) -> None:
        if not self.ctx:
            return
        self.ctx.catalog.scan()
        files = self.selected_files()
        if not files:
            QMessageBox.information(self, "未選取", "請先在右側表格選取檔案")
            return
        mode = self._choose_index_mode()
        if not mode:
            return
        update_text, update_image = mode
        self._set_last_action("開始索引（選取檔案）", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)


    def _start_index(self, files: List[Dict[str, Any]]):
        render_status = None
        try:
            render_status = self.ctx.indexer.renderer.status()
        except Exception:
            render_status = None
        if render_status and not render_status.get("available", False):
            status_map = render_status.get("status") or {}
            status_lines = []
            libreoffice_status = status_map.get("libreoffice")
            windows_status = status_map.get("windows_com")
            if libreoffice_status:
                status_lines.append(f"LibreOffice：{libreoffice_status}")
            if windows_status:
                status_lines.append(f"PowerPoint（Windows COM）：{windows_status}")
            status_detail = "\n".join(status_lines)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("未偵測到可用的 renderer")
            box.setText(
                "目前沒有可用的投影片 renderer（LibreOffice/PowerPoint）。"
                "此輪索引將只建立文字索引，不會產生縮圖。"
            )
            box.setInformativeText(
                "Renderer 是系統層級的投影片軟體，無法透過 requirements.txt 安裝。"
                "請先安裝 LibreOffice 或 PowerPoint 後再重試。要繼續索引嗎？"
            )
            if status_detail:
                box.setDetailedText(status_detail)
            box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Ok)
            box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if box.exec() != QMessageBox.Ok:
                return


        self._cancel_index = False
        self._pause_index = False
        self.btn_cancel.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(False)
        self.btn_index_selected.setEnabled(False)

        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("索引中...")

        def task(_progress_emit):
            def cancelled() -> bool:
                return self._cancel_index

            def progress_hook(p):
                _progress_emit(p)

            def paused() -> bool:
                return self._pause_index

            return self.ctx.indexer.rebuild_for_files(
                files,
                on_progress=progress_hook,
                cancel_flag=cancelled,
                pause_flag=paused,
                update_text=update_text,
                update_image=update_image,
            )

        w = Worker(task, None)
        # 將 signals.progress.emit 注入到 task 參數
        w.args = (w.signals.progress.emit,)
        w.signals.progress.connect(self._on_index_progress)
        w.signals.finished.connect(self._on_index_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_index_progress(self, p: object) -> None:
        try:
            msg = getattr(p, "message", "")
            cur = int(getattr(p, "current", 0))
            total = int(getattr(p, "total", 0))
            if total > 0:
                self.prog.setValue(int(cur / total * 100))
            self.prog_label.setText(msg or "索引中...")
            if msg:
                self.main_window.status.showMessage(msg)
        except Exception:
            pass

    def cancel_indexing(self) -> None:
        self._cancel_index = True
        self.prog_label.setText("取消中...")

    def toggle_pause_indexing(self) -> None:
        self._pause_index = not self._pause_index
        if self._pause_index:
            self.btn_pause.setText("續跑")
            self.prog_label.setText("已暫停")
        else:
            self.btn_pause.setText("暫停")
            self.prog_label.setText("索引中...")

    def _on_index_done(self, result: object) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(True)
        self.btn_index_selected.setEnabled(True)
        self.refresh_table()
        self.refresh_dirs()

        try:
            code, msg = result
            if code == 0:
                self.prog.setValue(100)
                self.prog_label.setText(msg)
            else:
                self.prog_label.setText(msg)
        except Exception:
            self.prog_label.setText("索引完成")

    def _on_error(self, tb: str) -> None:
        log.error("背景任務錯誤\n%s", tb)
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast("背景任務發生錯誤，已寫入 logs/app.log。", level="error", timeout_ms=12000)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("發生錯誤")
        box.setText("背景任務發生錯誤，已寫入 logs/app.log。您可以重試或將詳細資訊提供給支援人員。")
        box.setDetailedText(tb)
        box.setStandardButtons(QMessageBox.Retry | QMessageBox.Close)
        box.setDefaultButton(QMessageBox.Retry)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if box.exec() == QMessageBox.Retry and self._last_action:
            self._last_action()
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(True)
        self.btn_index_selected.setEnabled(True)
        self.prog_label.setText("發生錯誤")

    def _set_last_action(self, label: str, action) -> None:
        self._last_action_label = label
        self._last_action = action

    def clear_missing_files(self) -> None:
        if not self.ctx:
            return
        removed = self.ctx.catalog.clear_missing_files()
        self.refresh_table()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 筆缺失項目")
