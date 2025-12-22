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
        self._indexing_active = False
        self._cached_text_vectors: Dict[str, Any] = {}
        self._cached_image_vectors: Dict[str, Any] = {}
        self._vectors_loaded = False
        self._vector_mtimes = {
            "text": None,
            "text_delta": None,
            "image": None,
            "image_delta": None,
        }

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
        self._meta_files: Dict[str, Any] = {}
        self._slides_by_file_id: Dict[str, List[Dict[str, Any]]] = {}

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
            log.exception("更新掃描進度失敗")

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
        if hasattr(self.main_window, "page_status_tab"):
            self.main_window.page_status_tab.refresh_data()
        cat = self.ctx.store.load_manifest() if self.ctx else {}
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
        cat = self.ctx.store.load_manifest()
        slide_pages = self.ctx.store.load_slide_pages()
        text_vectors, image_vectors = self._load_vectors_cached()
        slides_by_file_id: Dict[str, List[Dict[str, Any]]] = {}
        for slide_id, text in slide_pages.items():
            if not isinstance(slide_id, str):
                continue
            if "#" not in slide_id:
                continue
            file_id, page_raw = slide_id.split("#", 1)
            try:
                page_no = int(page_raw)
            except Exception:
                continue
            thumb_path = self.ctx.store.paths.thumbs_dir / file_id / f"{page_no}.png"
            has_image = thumb_path.exists()
            text_value = "" if text is None else str(text)
            flags = {
                "has_text": bool(text_value.strip()),
                "has_bm25": bool(text_value.strip()),
                "has_text_vec": slide_id in text_vectors,
                "has_image": has_image,
                "has_image_vec": slide_id in image_vectors,
            }
            slide_entry = {
                "slide_id": slide_id,
                "file_id": file_id,
                "slide_no": page_no,
                "thumbnail_path": str(thumb_path) if has_image else None,
                "flags": flags,
            }
            slides_by_file_id.setdefault(file_id, []).append(slide_entry)
        self._meta_files = {}
        self._slides_by_file_id = slides_by_file_id
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]
        self._refresh_table_with_files(files)

    def _load_vectors_cached(self, *, force: bool = False) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if not self.ctx:
            return {}, {}
        paths = self.ctx.store.paths
        current_mtimes = {
            "text": paths.vec_text_npz.stat().st_mtime if paths.vec_text_npz.exists() else None,
            "text_delta": paths.vec_text_delta_npz.stat().st_mtime if paths.vec_text_delta_npz.exists() else None,
            "image": paths.vec_image_npz.stat().st_mtime if paths.vec_image_npz.exists() else None,
            "image_delta": paths.vec_image_delta_npz.stat().st_mtime if paths.vec_image_delta_npz.exists() else None,
        }
        changed = current_mtimes != self._vector_mtimes
        should_reload = force or not self._vectors_loaded
        if not self._indexing_active and (changed or should_reload):
            self._cached_text_vectors = self.ctx.store.load_text_vectors()
            self._cached_image_vectors = self.ctx.store.load_image_vectors()
            self._vector_mtimes = current_mtimes
            self._vectors_loaded = True
        return self._cached_text_vectors, self._cached_image_vectors

    def _refresh_table_with_files(self, files: List[Dict[str, Any]]) -> None:
        if not hasattr(self, "_slides_by_file_id"):
            self._slides_by_file_id = {}
        kw = (self.filter_edit.text() or "").strip().lower()
        if kw:
            files = [f for f in files if kw in (f.get("filename", "").lower())]
        status_filter = self.status_filter.currentData()
        if status_filter:
            files = [
                f
                for f in files
                if classify_doc_status(
                    f,
                    slides=self._slides_by_file_id.get(f.get("file_id"), []),
                )
                == status_filter
            ]
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
        file_id = f.get("file_id")
        slides = self._slides_by_file_id.get(file_id, []) if hasattr(self, "_slides_by_file_id") else []
        status = classify_doc_status(f, slides=slides)
        return STATUS_LABELS.get(status, "未處理")

    def _match_coverage_filter(self, f: Dict[str, Any], coverage: str) -> bool:
        file_id = f.get("file_id")
        slides = self._slides_by_file_id.get(file_id, []) if hasattr(self, "_slides_by_file_id") else []
        slide_count = f.get("slide_count")
        try:
            slide_total = int(slide_count) if slide_count is not None else 0
        except Exception:
            slide_total = 0
        if slide_total <= 0:
            return False
        flags = [s.get("flags", {}) for s in slides if isinstance(s.get("flags"), dict)]
        text_count = sum(1 for f in flags if f.get("has_text_vec"))
        image_count = sum(1 for f in flags if f.get("has_image_vec"))
        bm25_count = sum(1 for f in flags if f.get("has_bm25"))
        if coverage in {"text_missing", "bm25_missing"}:
            if coverage == "bm25_missing":
                return bm25_count < slide_total
            return text_count < slide_total
        if coverage == "image_missing":
            return image_count < slide_total
        if coverage == "fusion_missing":
            return text_count < slide_total or image_count < slide_total
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
        cat = self.ctx.store.load_manifest()
        files = [e for e in cat.get("files", []) if isinstance(e, dict)]
        path_to_file = {f.get("abs_path"): f for f in files if f.get("abs_path")}

        selected = []
        for idx in self.table.selectionModel().selectedRows():
            path = self.table.item(idx.row(), 1).text()
            if path in path_to_file:
                selected.append(path_to_file[path])
        return selected

    # ---------- indexing ----------
    def _choose_index_mode(self, files: List[Dict[str, Any]]) -> tuple[bool, bool] | None:
        while True:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("選擇索引類型")
            box.setText("請選擇要更新的索引類型：")

            details, detail_long, legacy_needed, fill_needed = self._build_index_status_details(files)
            if details:
                box.setInformativeText(details)
            if detail_long:
                box.setDetailedText(detail_long)

            btn_full = box.addButton("完整索引（文字 + 圖像）", QMessageBox.AcceptRole)
            btn_text = box.addButton("只更新文字索引", QMessageBox.AcceptRole)
            btn_image = box.addButton("只更新圖像索引", QMessageBox.AcceptRole)
            btn_migrate = None
            btn_fill = None
            if legacy_needed:
                btn_migrate = box.addButton("轉換舊版資料", QMessageBox.ActionRole)
            if fill_needed:
                btn_fill = box.addButton("補齊索引更新時間", QMessageBox.ActionRole)
            btn_cancel = box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(btn_full)
            box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.exec()
            clicked = box.clickedButton()
            if clicked == btn_cancel:
                return None
            if btn_migrate and clicked == btn_migrate:
                self._migrate_legacy_files()
                continue
            if btn_fill and clicked == btn_fill:
                self._fill_missing_index_timestamps()
                continue
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
        mode = self._choose_index_mode(files)
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
            cat = self.ctx.store.load_manifest()
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
        mode = self._choose_index_mode(files)
        if not mode:
            self._reset_prepare_ui()
            return
        update_text, update_image = mode
        self._set_last_action("開始索引（選取檔案）", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)

    def _build_index_status_details(
        self, files: List[Dict[str, Any]]
    ) -> tuple[str, str, bool, bool]:
        if not self.ctx:
            return "", "", False, False
        store = self.ctx.store
        paths = store.paths
        manifest = store.load_manifest()
        slide_pages = store.load_slide_pages()

        total_files = len([e for e in manifest.get("files", []) if isinstance(e, dict)])
        scope_files = len(files)
        indexed_files = sum(1 for e in manifest.get("files", []) if isinstance(e, dict) and e.get("indexed"))

        thumbs_count = 0
        try:
            if paths.thumbs_dir.exists():
                thumbs_count = sum(1 for _ in paths.thumbs_dir.rglob("*.png") if _.is_file())
        except Exception as exc:
            log.warning("讀取縮圖快取數量失敗：%s", exc)

        def fmt_time(ts: int | None) -> str:
            if not ts:
                return "未設定"
            try:
                return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return "時間格式錯誤"

        def file_info(path: Path) -> str:
            if not path.exists():
                return "不存在"
            try:
                mtime = int(path.stat().st_mtime)
                size = path.stat().st_size
                return f"存在（{fmt_time(mtime)} | {size:,} bytes）"
            except Exception:
                return "存在（無法讀取時間/大小）"

        legacy_needed = False
        legacy_lines = []
        legacy_candidates = [
            ("project.json", paths.project_json, paths.app_state_json),
            ("index.json", paths.index_json, paths.slide_pages_json),
        ]
        for legacy_name, legacy_path, new_path in legacy_candidates:
            if legacy_path.exists() and not new_path.exists():
                legacy_needed = True
                legacy_lines.append(f"- {legacy_name} 存在，但 {new_path.name} 缺失")

        fill_needed = False

        details = (
            f"本次範圍：{scope_files} 個檔案\n"
            f"已掃描檔案：{total_files} 個 | 已標記索引：{indexed_files} 個\n"
            f"投影片文字（slide_pages.json）：{len(slide_pages)}\n"
            f"縮圖快取：{thumbs_count} 張\n"
            "索引時間：以 manifest.json 的 indexed_at 為準"
        )

        detail_long_lines = [
            "=== 檔案存在狀態 ===",
            f"- app_state.json：{file_info(paths.app_state_json)}",
            f"- project.json：{file_info(paths.project_json)}",
            f"- manifest.json：{file_info(paths.manifest_json)}",
            f"- slide_pages.json：{file_info(paths.slide_pages_json)}",
            f"- index.json：{file_info(paths.index_json)}",
            "",
            "=== 索引時間概況 ===",
            "- 目前僅追蹤 manifest.json 的 indexed_at",
        ]
        if legacy_lines:
            detail_long_lines.append("")
            detail_long_lines.append("=== 舊版資料檢測 ===")
            detail_long_lines.extend(legacy_lines)

        return details, "\n".join(detail_long_lines), legacy_needed, fill_needed

    def _migrate_legacy_files(self) -> None:
        if not self.ctx:
            return
        try:
            store = self.ctx.store
            app_state = store.load_app_state()
            store.save_app_state(app_state)
            manifest = store.load_manifest()
            store.save_manifest(manifest)
            legacy_index = store.load_index()
            legacy_slides = legacy_index.get("slides", {}) if isinstance(legacy_index.get("slides"), dict) else {}
            slide_pages: Dict[str, str] = {}
            for slide_id, slide in legacy_slides.items():
                if not isinstance(slide_id, str) or not isinstance(slide, dict):
                    continue
                slide_pages[slide_id] = str(slide.get("text_for_bm25") or "")
            if slide_pages:
                store.save_slide_pages(slide_pages)
            QMessageBox.information(self, "轉換完成", "已完成舊版資料轉換並寫入新版檔案。")
        except Exception:
            log.exception("轉換舊版資料失敗")
            QMessageBox.warning(self, "轉換失敗", "舊版資料轉換失敗，請查看 logs/ 了解詳細原因。")

    def _fill_missing_index_timestamps(self) -> None:
        if not self.ctx:
            return
        store = self.ctx.store
        try:
            manifest = store.load_manifest()
            files = [e for e in manifest.get("files", []) if isinstance(e, dict)]
            if store.paths.slide_pages_json.exists():
                source_ts = int(store.paths.slide_pages_json.stat().st_mtime)
            elif store.paths.index_json.exists():
                source_ts = int(store.paths.index_json.stat().st_mtime)
            else:
                source_ts = int(datetime.datetime.now().timestamp())
            updated = 0
            for entry in files:
                if not entry.get("indexed_at"):
                    entry["indexed_at"] = source_ts
                    updated += 1
            if updated:
                manifest["files"] = files
                store.save_manifest(manifest)
            QMessageBox.information(
                self,
                "補齊完成",
                f"已補齊 {updated} 筆索引更新時間（來源：slide_pages.json 或 index.json 存檔日）。",
            )
        except Exception:
            log.exception("補齊索引更新時間失敗")
            QMessageBox.warning(self, "補齊失敗", "補齊索引更新時間失敗，請查看 logs/ 了解詳細原因。")


    def _start_index(self, files: List[Dict[str, Any]], update_text: bool, update_image: bool) -> None:
        render_status = None
        try:
            render_status = self.ctx.indexer.renderer.status()
        except Exception:
            log.exception("取得 renderer 狀態失敗")
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
        self._indexing_active = True
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
            if stage in {"file_done", "skip", "extracted", "slide_batch", "pause", "done"}:
                self._schedule_index_refresh()
                if hasattr(self.main_window, "page_status_tab"):
                    self.main_window.page_status_tab.refresh_data()
        except Exception:
            log.exception("更新索引進度失敗")

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
        self._indexing_active = False
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(True)
        self.btn_index_selected.setEnabled(True)
        self._load_vectors_cached(force=True)
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
            log.exception("更新索引完成訊息失敗")
            self.prog_label.setText("索引完成")

    def _on_error(self, tb: str) -> None:
        log.error("背景任務錯誤\n%s", tb)
        self._indexing_active = False
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
        self._set_last_action("清理缺失項目", self.clear_missing_files)
        self.prog_label.setText("清理中...")
        self.prog.setRange(0, 0)

        def task():
            return self.ctx.catalog.clear_missing_files()

        w = Worker(task)
        w.signals.finished.connect(self._on_clear_missing_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_clear_missing_done(self, removed: object) -> None:
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("清理完成")
        self.refresh_table()
        try:
            removed_count = int(removed)
        except Exception:
            log.exception("解析清理結果失敗")
            removed_count = 0
        QMessageBox.information(self, "清理完成", f"已清理 {removed_count} 筆缺失項目")
