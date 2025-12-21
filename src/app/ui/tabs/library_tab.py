# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
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
    QComboBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger
from app.ui.async_worker import Worker
from app.ui.metrics import STATUS_LABELS, classify_doc_status

log = get_logger(__name__)


class SortableItem(QTableWidgetItem):
    def __init__(self, text: str, sort_key: object | None = None) -> None:
        super().__init__(text)
        self._sort_key = sort_key if sort_key is not None else text

    def __lt__(self, other: QTableWidgetItem) -> bool:
        other_key = getattr(other, "_sort_key", other.text())
        return self._sort_key < other_key


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
        self._pending_table_refresh = False
        self._pending_metrics_refresh = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)

        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self._flush_table_refresh)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setSingleShot(True)
        self._metrics_timer.setInterval(10000)

        self._metrics_timer.timeout.connect(self._flush_metrics_refresh)

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
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部狀態", None)
        self.status_filter.addItem("待索引", "pending")
        self.status_filter.addItem("已索引", "indexed")
        self.status_filter.addItem("已過期", "stale")
        self.status_filter.addItem("部分索引", "partial")
        self.status_filter.addItem("錯誤", "error")
        filter_row.addWidget(self.status_filter)
        self.coverage_filter = QComboBox()
        self.coverage_filter.addItem("全部覆蓋", None)
        self.coverage_filter.addItem("文字未覆蓋", "text_missing")
        self.coverage_filter.addItem("BM25 未覆蓋", "bm25_missing")
        self.coverage_filter.addItem("圖像未覆蓋", "image_missing")
        self.coverage_filter.addItem("融合未完整", "fusion_missing")
        filter_row.addWidget(self.coverage_filter)
        right_layout.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["檔名", "路徑", "修改時間", "大小", "狀態", "投影片數"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setStyleSheet(
            "QTableWidget { color: #0F172A; background: #FFFFFF; }"
            "QTableWidget::item:selected { background: #E2E8F0; color: #0F172A; }"
            "QHeaderView::section { color: #0F172A; background: #F1F5F9; }"
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSortIndicator(0, Qt.AscendingOrder)
        header.setSortIndicatorShown(True)
        self.table.setSortingEnabled(True)
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
        self.status_filter.currentIndexChanged.connect(self.refresh_table)
        self.coverage_filter.currentIndexChanged.connect(self.refresh_table)

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

        w = Worker(task)
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
        if hasattr(self.main_window, "dashboard_tab"):
            self.main_window.dashboard_tab.refresh_metrics()
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
        status_filter = self.status_filter.currentData()
        if status_filter:
            files = [f for f in files if classify_doc_status(f) == status_filter]
        coverage_filter = self.coverage_filter.currentData()
        if coverage_filter:
            files = [f for f in files if self._match_coverage_filter(f, coverage_filter)]

        sorting_enabled = self.table.isSortingEnabled()
        if sorting_enabled:
            self.table.setSortingEnabled(False)
        self.table.setRowCount(len(files))
        for r, f in enumerate(files):
            self._set_row(r, f)
        if sorting_enabled:
            self.table.setSortingEnabled(True)
            header = self.table.horizontalHeader()
            self.table.sortItems(header.sortIndicatorSection(), header.sortIndicatorOrder())

    def _set_row(self, r: int, f: Dict[str, Any]) -> None:
        path = f.get("abs_path") or f.get("file_path") or f.get("path") or ""
        fn = f.get("filename") or f.get("file_name") or f.get("name") or ""
        if not fn and path:
            try:
                fn = Path(path).name
            except Exception:
                fn = ""
        mtime = f.get("modified_time") or f.get("mtime") or f.get("modified_at")
        try:
            dt = datetime.datetime.fromtimestamp(int(mtime))
            mtime_s = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            mtime_s = ""
        size = f.get("size") or f.get("bytes") or 0
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
        sort_keys = [
            fn.lower(),
            path.lower(),
            int(mtime or 0),
            int(size or 0),
            {"未處理": 0, "部分索引": 1, "已擷取": 2, "已索引": 3}.get(status, 99),
            int(slides) if slides is not None else -1,
        ]
        for c, val in enumerate([fn, path, mtime_s, size_s, status, slides_s]):
            it = SortableItem(str(val), sort_keys[c])
            it.setFlags(it.flags() ^ Qt.ItemIsEditable)
            it.setToolTip(tooltip)
            if status == "已索引":
                it.setBackground(QColor("#E8F5E9"))
            self.table.setItem(r, c, it)

    def _status_text(self, f: Dict[str, Any]) -> str:
        return STATUS_LABELS.get(classify_doc_status(f), "未處理")

    def _match_coverage_filter(self, f: Dict[str, Any], coverage: str) -> bool:
        status = f.get("index_status") if isinstance(f.get("index_status"), dict) else {}
        slide_count = f.get("slide_count")
        if slide_count is None:
            slide_count = status.get("index_slide_count") or f.get("slides_count")
        try:
            slide_total = int(slide_count) if slide_count is not None else 0
        except Exception:
            slide_total = 0
        if slide_total <= 0:
            return False
        text_count = status.get("text_indexed_count")
        image_count = status.get("image_indexed_count")
        if coverage in {"text_missing", "bm25_missing"}:
            return isinstance(text_count, int) and text_count < slide_total
        if coverage == "image_missing":
            return isinstance(image_count, int) and image_count < slide_total
        if coverage == "fusion_missing":
            return (
                isinstance(text_count, int)
                and isinstance(image_count, int)
                and (text_count < slide_total or image_count < slide_total)
            )
        return False

    def apply_status_filter(self, status: str | None) -> None:
        idx = self.status_filter.findData(status)
        self.status_filter.setCurrentIndex(idx if idx != -1 else 0)
        self.refresh_table()

    def apply_coverage_filter(self, coverage: str | None) -> None:
        idx = self.coverage_filter.findData(coverage)
        self.coverage_filter.setCurrentIndex(idx if idx != -1 else 0)
        self.refresh_table()

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
        self._set_last_action("準備索引（需要者）", lambda: self.start_index_needed())
        self._prepare_index_needed()

    def start_index_selected(self) -> None:
        if not self.ctx:
            return
        files = self.selected_files()
        if not files:
            QMessageBox.information(self, "未選取", "請先在右側表格選取檔案")
            return
        selected_paths = [f.get("abs_path") for f in files if f.get("abs_path")]
        if not selected_paths:
            QMessageBox.information(self, "未選取", "請先在右側表格選取檔案")
            return
        self._set_last_action("準備索引（選取檔案）", lambda: self.start_index_selected())
        self._prepare_index_selected(selected_paths)

    def _set_prepare_ui(self, label: str) -> None:
        self.btn_index_needed.setEnabled(False)
        self.btn_index_selected.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暫停")
        self.prog.setRange(0, 0)
        self.prog.setValue(0)
        self.prog_label.setText(label)
        if hasattr(self.main_window, "status"):
            self.main_window.status.showMessage(label)

    def _reset_prepare_ui(self) -> None:
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("")
        self.btn_index_needed.setEnabled(True)
        self.btn_index_selected.setEnabled(True)

    def _prepare_index_needed(self) -> None:
        if not self.ctx:
            return
        self._set_prepare_ui("正在掃描並整理索引需求...")

        def task():
            self.ctx.catalog.scan()
            return self.ctx.indexer.compute_needed_files()

        w = Worker(task)
        w.signals.finished.connect(self._on_prepare_index_needed_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_prepare_index_needed_done(self, files: List[Dict[str, Any]]) -> None:
        if not files:
            self._reset_prepare_ui()
            QMessageBox.information(self, "不需要索引", "目前沒有需要更新的檔案")
            return
        mode = self._choose_index_mode()
        if not mode:
            self._reset_prepare_ui()
            return
        update_text, update_image = mode
        self._set_last_action("開始索引（需要者）", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)

    def _prepare_index_selected(self, selected_paths: List[str]) -> None:
        if not self.ctx:
            return
        self._set_prepare_ui("正在掃描並整理選取檔案...")

        def task():
            self.ctx.catalog.scan()
            cat = self.ctx.store.load_catalog()
            files = [e for e in cat.get("files", []) if isinstance(e, dict)]
            selected = []
            for entry in files:
                path = entry.get("abs_path")
                if path and path in selected_paths:
                    selected.append(entry)
            return selected

        w = Worker(task, None)
        w.signals.finished.connect(self._on_prepare_index_selected_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_prepare_index_selected_done(self, files: List[Dict[str, Any]]) -> None:
        if not files:
            self._reset_prepare_ui()
            QMessageBox.information(self, "未選取", "找不到選取的檔案，請重新選取後再試")
            return
        mode = self._choose_index_mode()
        if not mode:
            self._reset_prepare_ui()
            return
        update_text, update_image = mode
        self._set_last_action("開始索引（選取檔案）", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)


    def _start_index(self, files: List[Dict[str, Any]], update_text: bool, update_image: bool) -> None:
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
            stage = getattr(p, "stage", "")
            cur = int(getattr(p, "current", 0))
            total = int(getattr(p, "total", 0))
            avg_page_time = getattr(p, "avg_page_time", None)
            avg_extract_time = getattr(p, "avg_extract_time", None)
            avg_render_time = getattr(p, "avg_render_time", None)
            if total > 0:
                self.prog.setValue(int(cur / total * 100))
            metrics = []
            if avg_page_time is not None:
                metrics.append(f"平均每頁 {avg_page_time:.2f} 秒")
            if avg_extract_time is not None:
                metrics.append(f"文字抽取平均 {avg_extract_time:.2f} 秒/頁")
            if avg_render_time is not None:
                metrics.append(f"縮圖截取平均 {avg_render_time:.2f} 秒/頁")
            if metrics:
                display_msg = f"{msg or '索引中...'}\n" + " | ".join(metrics)
            else:
                display_msg = msg or "索引中..."
            self.prog_label.setText(display_msg)
            if msg:
                self.main_window.status.showMessage(msg)
            if stage in {"file_done", "skip", "extracted"}:
                self._schedule_index_refresh()
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
        if hasattr(self.main_window, "dashboard_tab"):
            self.main_window.dashboard_tab.refresh_metrics()

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

    def _schedule_index_refresh(self) -> None:
        self._pending_table_refresh = True
        self._pending_metrics_refresh = True
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        if not self._metrics_timer.isActive():
            self._metrics_timer.start()

    def _flush_table_refresh(self) -> None:
        if self._pending_table_refresh:
            self.refresh_table()
        self._pending_table_refresh = False

    def _flush_metrics_refresh(self) -> None:
        if self._pending_metrics_refresh and hasattr(self.main_window, "dashboard_tab"):
            self.main_window.dashboard_tab.refresh_metrics()
        self._pending_metrics_refresh = False

    def clear_missing_files(self) -> None:
        if not self.ctx:
            return
        removed = self.ctx.catalog.clear_missing_files()
        self.refresh_table()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 筆缺失項目")
