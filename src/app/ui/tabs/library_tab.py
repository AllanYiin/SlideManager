# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

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

from app.core.backend_config import get_backend_host, get_backend_port
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
        self._cancel_scan = False
        self._cancel_prepare = False
        self._prepare_in_progress = False
        self._last_action = None
        self._last_action_label = ""
        self._scan_files_cache: List[Dict[str, Any]] = []
        self._scan_count = 0
        self._prepare_scan_count = 0
        self._pending_table_refresh = False
        self._pending_metrics_refresh = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)

        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._flush_table_refresh)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setSingleShot(True)
        self._metrics_timer.setInterval(10000)

        self._metrics_timer.timeout.connect(self._flush_metrics_refresh)
        self._scan_in_progress = False
        self._indexing_active = False
        self._cached_text_vector_keys: Set[str] = set()
        self._cached_image_vector_keys: Set[str] = set()
        self._vectors_loaded = False
        self._vector_mtimes = {
            "text": None,
            "text_delta": None,
            "image": None,
            "image_delta": None,
        }
        self._table_refresh_inflight = False
        self._table_fill_job_id = 0
        self._table_fill_cursor = 0
        self._table_fill_files: List[Dict[str, Any]] = []
        self._cached_files: List[Dict[str, Any]] = []
        self._index_status_payload: Dict[str, Any] = {}
        self._index_action_inflight = False
        self._job_id = None
        self._job_paused = False
        self._job_worker = None
        self._job_poll_inflight = False
        self._job_poll_timer = QTimer(self)
        self._job_poll_timer.setInterval(2000)
        self._job_poll_timer.timeout.connect(self._poll_job_status)
        self._job_snapshot_timer = QTimer(self)
        self._job_snapshot_timer.setSingleShot(True)
        self._job_snapshot_timer.setInterval(300)
        self._job_snapshot_timer.timeout.connect(self._request_job_snapshot)
        self._pending_index_roots: List[str] = []
        self._pending_index_options: Dict[str, Any] = {}
        self._pending_index_files_by_root: Dict[str, List[str]] = {}

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
        self.filter_edit.textChanged.connect(self.refresh_table_view)
        self.status_filter.currentIndexChanged.connect(self.refresh_table_view)
        self.coverage_filter.currentIndexChanged.connect(self.refresh_table_view)

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
        self._scan_in_progress = True
        self._cancel_scan = False
        self._scan_files_cache = []
        self._scan_count = 0
        self._meta_files: Dict[str, Any] = {}
        self._slides_by_file_id: Dict[str, List[Dict[str, Any]]] = {}
        self.btn_cancel.setEnabled(True)

        def task(_progress_emit, _cancel_flag):
            return self.ctx.catalog.scan(on_progress=_progress_emit, progress_every=10, cancel_flag=_cancel_flag)

        w = Worker(task)
        w.args = (w.signals.progress.emit, lambda: self._cancel_scan)
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
            self._pending_table_refresh = True
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        except Exception:
            log.exception("更新掃描進度失敗")

    def _on_scan_done(self, _result: object) -> None:
        if isinstance(_result, dict) and _result.get("cancelled"):
            self.prog.setRange(0, 100)
            self.prog.setValue(0)
            self.prog_label.setText("掃描已取消")
            self._scan_in_progress = False
            self._scan_files_cache = []
            self._scan_count = 0
            self.btn_cancel.setEnabled(False)
            return
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("掃描完成")
        self._scan_in_progress = False
        self._scan_files_cache = []
        self._scan_count = 0
        self.btn_cancel.setEnabled(False)
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
        if self._table_refresh_inflight:
            self._pending_table_refresh = True
            return
        self._table_refresh_inflight = True
        ctx = self.ctx
        cached_text_vector_keys = self._cached_text_vector_keys
        cached_image_vector_keys = self._cached_image_vector_keys
        vectors_loaded = self._vectors_loaded
        vector_mtimes = dict(self._vector_mtimes)

        def task():
            import traceback

            try:
                cat = ctx.store.load_manifest()
                slide_pages = ctx.store.load_slide_pages()
                paths = ctx.store.paths
                current_mtimes = {
                    "text": paths.vec_text_npz.stat().st_mtime if paths.vec_text_npz.exists() else None,
                    "text_delta": paths.vec_text_delta_npz.stat().st_mtime if paths.vec_text_delta_npz.exists() else None,
                    "image": paths.vec_image_npz.stat().st_mtime if paths.vec_image_npz.exists() else None,
                    "image_delta": paths.vec_image_delta_npz.stat().st_mtime if paths.vec_image_delta_npz.exists() else None,
                }
                if not vectors_loaded or current_mtimes != vector_mtimes:
                    text_vector_keys = ctx.store.load_text_vector_keys()
                    image_vector_keys = ctx.store.load_image_vector_keys()
                else:
                    text_vector_keys = cached_text_vector_keys
                    image_vector_keys = cached_image_vector_keys
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
                    thumb_path = ctx.store.paths.thumbs_dir / file_id / f"{page_no}.png"
                    has_image = thumb_path.exists()
                    text_value = "" if text is None else str(text)
                    flags = {
                        "has_text": bool(text_value.strip()),
                        "has_bm25": bool(text_value.strip()),
                        "has_text_vec": slide_id in text_vector_keys,
                        "has_image": has_image,
                        "has_image_vec": slide_id in image_vector_keys,
                    }
                    slide_entry = {
                        "slide_id": slide_id,
                        "file_id": file_id,
                        "slide_no": page_no,
                        "thumbnail_path": str(thumb_path) if has_image else None,
                        "flags": flags,
                    }
                    slides_by_file_id.setdefault(file_id, []).append(slide_entry)
                files = [e for e in cat.get("files", []) if isinstance(e, dict)]
                return {
                    "ok": True,
                    "files": files,
                    "slides_by_file_id": slides_by_file_id,
                    "text_vector_keys": text_vector_keys,
                    "image_vector_keys": image_vector_keys,
                    "vector_mtimes": current_mtimes,
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"載入檔案清單失敗：{exc}",
                    "traceback": traceback.format_exc(),
                }

        w = Worker(task)
        w.signals.finished.connect(self._on_refresh_table_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def refresh_table_view(self) -> None:
        if not self.ctx:
            self.table.setRowCount(0)
            return
        files = self._cached_files
        if not files:
            self.table.setRowCount(0)
            return
        self._refresh_table_with_files(files)

    def _on_refresh_table_done(self, payload: object) -> None:
        self._table_refresh_inflight = False
        if not isinstance(payload, dict):
            log.error("表格資料回傳格式不正確：%s", payload)
            return
        if not payload.get("ok"):
            log.error("載入檔案清單失敗\n%s", payload.get("traceback", ""))
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("載入檔案清單失敗，已寫入 logs/app.log。", level="error")
            return
        self._cached_text_vector_keys = payload.get("text_vector_keys", set())
        self._cached_image_vector_keys = payload.get("image_vector_keys", set())
        self._vector_mtimes = payload.get("vector_mtimes", self._vector_mtimes)
        self._vectors_loaded = True
        self._slides_by_file_id = payload.get("slides_by_file_id", {})
        files = payload.get("files", [])
        self._cached_files = files if isinstance(files, list) else []
        self._refresh_table_with_files(self._cached_files)
        if self._pending_table_refresh:
            self._pending_table_refresh = False
            self.refresh_table()

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

        self._table_fill_job_id += 1
        job_id = self._table_fill_job_id
        self._table_fill_files = list(files)
        self._table_fill_cursor = 0
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(files))
        QTimer.singleShot(0, lambda: self._fill_table_chunk(job_id))

    def _fill_table_chunk(self, job_id: int) -> None:
        if job_id != self._table_fill_job_id:
            return
        files = self._table_fill_files
        cursor = self._table_fill_cursor
        chunk_size = 100
        end = min(cursor + chunk_size, len(files))
        for i in range(cursor, end):
            self._set_row(i, files[i])
        self._table_fill_cursor = end
        if end < len(files):
            QTimer.singleShot(0, lambda: self._fill_table_chunk(job_id))
            return
        self.table.setUpdatesEnabled(True)
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
        self.refresh_table_view()

    def apply_coverage_filter(self, coverage: str | None) -> None:
        idx = self.coverage_filter.findData(coverage)
        self.coverage_filter.setCurrentIndex(idx if idx != -1 else 0)
        self.refresh_table_view()

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
        details = self._index_status_payload.get("details", "")
        detail_long = self._index_status_payload.get("detail_long", "")
        scope_only = bool(self._index_status_payload.get("scope_only"))
        legacy_needed = bool(self._index_status_payload.get("legacy_needed")) and not scope_only
        fill_needed = bool(self._index_status_payload.get("fill_needed")) and not scope_only
        log.info(
            "[INDEX_FLOW][MODE] scope_only=%s files_count=%d legacy_needed=%s fill_needed=%s",
            scope_only,
            len(files),
            legacy_needed,
            fill_needed,
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("選擇索引類型")
        if scope_only:
            box.setText("請選擇要更新的索引類型（僅限選取檔案）：")
        else:
            box.setText("請選擇要更新的索引類型：")

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
            log.info("[INDEX_FLOW][MODE] selection=cancel")
            return None
        if btn_migrate and clicked == btn_migrate:
            log.info("[INDEX_FLOW][MODE] selection=migrate_legacy")
            self._index_action_inflight = True
            self._run_migrate_legacy_files(files)
            return None
        if btn_fill and clicked == btn_fill:
            log.info("[INDEX_FLOW][MODE] selection=fill_missing_index_timestamps")
            self._index_action_inflight = True
            self._run_fill_missing_index_timestamps(files)
            return None
        if clicked == btn_text:
            log.info("[INDEX_FLOW][MODE] selection=text_only")
            return True, False
        if clicked == btn_image:
            log.info("[INDEX_FLOW][MODE] selection=image_only")
            return False, True
        log.info("[INDEX_FLOW][MODE] selection=full")
        return True, True

    def start_index_needed(self) -> None:
        if not self.ctx:
            return
        log.info("[INDEX_FLOW][UI] action=start_index_needed")
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
        self.btn_cancel.setEnabled(True)
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
        self.btn_cancel.setEnabled(False)
        self._prepare_in_progress = False

    def _prepare_index_needed(self) -> None:
        if not self.ctx:
            return
        self._set_prepare_ui("正在掃描並整理索引需求...")
        self._prepare_scan_count = 0
        self._prepare_in_progress = True
        self._cancel_prepare = False

        def task(_progress_emit, _cancel_flag):
            log.info("[INDEX_FLOW][PREPARE] step=scan_start source=catalog.scan")
            result = self.ctx.catalog.scan(on_progress=_progress_emit, progress_every=10, cancel_flag=_cancel_flag)
            if isinstance(result, dict) and result.get("cancelled"):
                log.info("[INDEX_FLOW][PREPARE] step=scan_cancelled")
                return {"cancelled": True}
            log.info("[INDEX_FLOW][PREPARE] step=scan_done scanned_count=%d", len(result.get("files", [])))
            log.info("[INDEX_FLOW][PREPARE] step=compute_needed_files_start source=indexer.compute_needed_files")
            return self.ctx.indexer.compute_needed_files()

        w = Worker(task)
        w.args = (w.signals.progress.emit, lambda: self._cancel_prepare)
        w.signals.progress.connect(self._on_prepare_scan_progress)
        w.signals.finished.connect(self._on_prepare_index_needed_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_prepare_index_needed_done(self, files: List[Dict[str, Any]]) -> None:
        if isinstance(files, dict) and files.get("cancelled"):
            log.info("[INDEX_FLOW][PREPARE] step=prepare_cancelled")
            self.prog_label.setText("已取消")
            self._reset_prepare_ui()
            return
        if not files:
            log.info("[INDEX_FLOW][PREPARE] step=no_files_needed")
            self._reset_prepare_ui()
            QMessageBox.information(self, "不需要索引", "目前沒有需要更新的檔案")
            return
        log.info("[INDEX_FLOW][PREPARE] step=files_needed count=%d", len(files))
        self._request_index_mode(files)

    def _prepare_index_selected(self, selected_paths: List[str]) -> None:
        if not self.ctx:
            return
        self._set_prepare_ui("正在整理選取檔案...")
        self._prepare_in_progress = True
        self._cancel_prepare = False

        def task():
            if self._cancel_prepare:
                return {"cancelled": True}
            cat = self.ctx.store.load_manifest()
            files = [e for e in cat.get("files", []) if isinstance(e, dict)]
            selected_set = {p for p in selected_paths if p}
            log.info(
                "[INDEX_SCOPE][UI] filter=selected_paths before=%d after=%d conditions=abs_path in selected_set",
                len(files),
                len(selected_set),
            )
            selected = []
            total_entries = len(files)
            for idx, entry in enumerate(files, start=1):
                if self._cancel_prepare:
                    return {"cancelled": True}
                path = entry.get("abs_path")
                if path and path in selected_set:
                    selected.append(entry)
                if idx % 50 == 0 or idx == total_entries:
                    log.info(
                        "[INDEX_SCOPE][UI] enumerate=manifest.files total=%d current=%d matched=%d",
                        total_entries,
                        idx,
                        len(selected),
                    )
            log.info("[INDEX_SCOPE][UI] selected_paths=%s", selected_paths)
            log.info(
                "[INDEX_SCOPE][UI] resolved_selected_files=%s",
                [(e.get("file_id"), e.get("abs_path")) for e in selected],
            )
            if len(selected) != len(selected_paths):
                missing_paths = [p for p in selected_paths if p and p not in {e.get("abs_path") for e in selected}]
                log.warning(
                    "[INDEX_SCOPE][UI] unmatched_selected_paths=%s",
                    missing_paths,
                )
            return selected

        w = Worker(task)
        w.signals.finished.connect(self._on_prepare_index_selected_done)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_prepare_index_selected_done(self, files: List[Dict[str, Any]]) -> None:
        if isinstance(files, dict) and files.get("cancelled"):
            self.prog_label.setText("已取消")
            self._reset_prepare_ui()
            return
        if not files:
            self._reset_prepare_ui()
            QMessageBox.information(self, "未選取", "找不到選取的檔案，請重新選取後再試")
            return
        log.info(
            "[INDEX_SCOPE][UI] scope_only=True files_count=%d files=%s",
            len(files),
            [(f.get("file_id"), f.get("abs_path")) for f in files],
        )
        self._request_index_mode(files, scope_only=True)

    def _request_index_mode(self, files: List[Dict[str, Any]], scope_only: bool = False) -> None:
        if not self.ctx:
            return
        if scope_only and (not files or any(not f.get("file_id") for f in files)):
            file_names = []
            for entry in files or []:
                path = entry.get("abs_path")
                if path:
                    file_names.append(Path(path).name)
            shown_names = "、".join(file_names[:20]) if file_names else "（無）"
            if len(file_names) > 20:
                shown_names += "…"
            log.error(
                "[INDEX_SCOPE][UI] scope_only files invalid: files=%s",
                [(f.get("file_id"), f.get("abs_path")) for f in files or []],
            )
            QMessageBox.warning(
                self,
                "索引範圍異常",
                f"選取檔案資料異常，已取消本次索引。\n"
                f"本次 scope 檔案數：{len(files) if files else 0}\n"
                f"檔名清單：{shown_names}",
            )
            self._reset_prepare_ui()
            return
        self._set_prepare_ui("正在整理索引狀態...")
        ctx = self.ctx

        def task(_progress_emit):
            import traceback

            try:
                if self._cancel_prepare:
                    return {"ok": False, "cancelled": True}

                if _progress_emit:
                    _progress_emit({"stage": "manifest", "message": "正在讀取索引清單..."})

                store = ctx.store
                paths = store.paths
                manifest = store.load_manifest()
                if _progress_emit and not scope_only:
                    _progress_emit({"stage": "slide_pages", "message": "正在讀取投影片文字索引..."})
                slide_pages = [] if scope_only else store.load_slide_pages()

                scope_files = len(files)
                if scope_only:
                    total_files = scope_files
                    indexed_files = sum(1 for e in files if isinstance(e, dict) and e.get("indexed"))
                else:
                    total_files = len([e for e in manifest.get("files", []) if isinstance(e, dict)])
                    indexed_files = sum(
                        1 for e in manifest.get("files", []) if isinstance(e, dict) and e.get("indexed")
                    )

                thumbs_count = 0
                if not scope_only:
                    try:
                        if total_files == 0 and scope_files == 0:
                            if _progress_emit:
                                _progress_emit({"stage": "thumbs", "message": "縮圖快取：0 張"})

                        elif indexed_files == 0:
                            if _progress_emit:
                                _progress_emit({"stage": "thumbs", "message": "縮圖快取：0 張"})

                        elif paths.thumbs_dir.exists():
                            if _progress_emit:
                                _progress_emit({"stage": "thumbs", "message": "正在統計縮圖快取..."})
                            thumb_list = list(paths.thumbs_dir.rglob("*.png"))
                            total_thumbs = len(thumb_list)
                            log.info(
                                "[INDEX_FLOW][PREPARE] enumerate=thumbs_dir.rglob total=%d path=%s",
                                total_thumbs,
                                paths.thumbs_dir,
                            )
                            for idx, thumb in enumerate(thumb_list, start=1):
                                if self._cancel_prepare:
                                    return {"ok": False, "cancelled": True}
                                if thumb.is_file():
                                    thumbs_count += 1
                                if _progress_emit and idx % 50 == 0:
                                    _progress_emit(
                                        {
                                            "stage": "thumbs",
                                            "message": f"正在統計縮圖快取... 已掃描 {idx} 筆",
                                        }
                                    )
                    except Exception:
                        log.exception("讀取縮圖快取數量失敗")

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
                total_legacy = len(legacy_candidates)
                log.info(
                    "[INDEX_FLOW][PREPARE] enumerate=legacy_candidates total=%d source=_request_index_mode",
                    total_legacy,
                )
                for idx, (legacy_name, legacy_path, new_path) in enumerate(legacy_candidates, start=1):
                    log.info(
                        "[INDEX_FLOW][PREPARE] enumerate=legacy_candidates total=%d current=%d legacy=%s new=%s",
                        total_legacy,
                        idx,
                        legacy_path,
                        new_path,
                    )
                    if legacy_path.exists() and not new_path.exists():
                        legacy_needed = True
                        legacy_lines.append(f"- {legacy_name} 存在，但 {new_path.name} 缺失")

                fill_needed = False

                slide_pages_summary = "略過" if scope_only else str(len(slide_pages))
                thumbs_summary = "略過" if scope_only else f"{thumbs_count} 張"
                scope_file_names = []

                total_scope = len(files)
                log.info(
                    "[INDEX_FLOW][PREPARE] enumerate=scope_files total=%d source=_request_index_mode.files",
                    total_scope,
                )
                for idx, entry in enumerate(files, start=1):

                    if self._cancel_prepare:
                        return {"ok": False, "cancelled": True}
                    if not isinstance(entry, dict):
                        continue
                    path = entry.get("abs_path")
                    if path:
                        scope_file_names.append(Path(path).name)
                    if _progress_emit and idx % 50 == 0:
                        _progress_emit(
                            {
                                "stage": "scope",
                                "message": f"正在整理檔案清單... 已處理 {idx} 筆",
                            }
                        )
                    if idx % 50 == 0 or idx == total_scope:
                        log.info(
                            "[INDEX_FLOW][PREPARE] enumerate=scope_files total=%d current=%d",
                            total_scope,
                            idx,
                        )
                scope_names_display = "、".join(scope_file_names[:20]) if scope_file_names else "（無）"
                if len(scope_file_names) > 20:
                    scope_names_display += "…"

                details = (
                    f"本次範圍：{scope_files} 個檔案\n"
                    f"檔名清單：{scope_names_display}\n"
                    f"已掃描檔案：{total_files} 個 | 已標記索引：{indexed_files} 個\n"
                    f"投影片文字（slide_pages.json）：{slide_pages_summary}\n"
                    f"縮圖快取：{thumbs_summary}\n"
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

                return {
                    "ok": True,
                    "details": details,
                    "detail_long": "\n".join(detail_long_lines),
                    "legacy_needed": legacy_needed,
                    "fill_needed": fill_needed,
                    "scope_only": scope_only,
                }
            except Exception as exc:
                log.exception("整理索引狀態失敗: %s", exc)
                return {
                    "ok": False,
                    "message": f"整理索引狀態失敗：{exc}",
                    "traceback": traceback.format_exc(),
                }

        w = Worker(task)
        w.args = (w.signals.progress.emit,)
        w.signals.progress.connect(self._on_prepare_status_progress)
        w.signals.finished.connect(lambda payload: self._on_index_status_ready(files, payload))
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_index_status_ready(self, files: List[Dict[str, Any]], payload: object) -> None:
        if isinstance(payload, dict) and payload.get("cancelled"):
            self.prog_label.setText("已取消")
            self._reset_prepare_ui()
            return
        if not isinstance(payload, dict) or not payload.get("ok"):
            log.error("整理索引狀態失敗\n%s", payload if isinstance(payload, dict) else payload)
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("整理索引狀態失敗，已寫入 logs/app.log。", level="error")
            self._reset_prepare_ui()
            return
        self._index_status_payload = payload
        mode = self._choose_index_mode(files)
        if not mode:
            if self._index_action_inflight:
                self._index_action_inflight = False
                return
            self._reset_prepare_ui()
            return
        update_text, update_image = mode
        self._set_last_action("開始索引", lambda: self._start_index(files, update_text, update_image))
        self._start_index(files, update_text, update_image)

    def _on_prepare_scan_progress(self, payload: object) -> None:
        try:
            if not isinstance(payload, dict):
                return
            count = int(payload.get("count", 0))
            self._prepare_scan_count = count
            self.prog_label.setText(f"正在掃描並整理索引需求... 已掃描 {self._prepare_scan_count} 筆")
        except Exception:
            log.exception("更新索引準備進度失敗")


    def _on_prepare_status_progress(self, payload: object) -> None:
        try:
            if not isinstance(payload, dict):
                return
            message = str(payload.get("message") or "").strip()
            if message:
                self.prog_label.setText(message)
        except Exception:
            log.exception("更新索引狀態整理進度失敗")


    def _run_migrate_legacy_files(self, files: List[Dict[str, Any]]) -> None:
        if not self.ctx:
            return
        self._set_last_action("轉換舊版資料", lambda: self._run_migrate_legacy_files(files))
        self._set_prepare_ui("正在轉換舊版資料...")
        ctx = self.ctx

        def task():
            import traceback

            try:
                if self._cancel_prepare:
                    return {"ok": False, "cancelled": True}
                store = ctx.store
                app_state = store.load_app_state()
                store.save_app_state(app_state)
                manifest = store.load_manifest()
                store.save_manifest(manifest)
                legacy_index = store.load_index()
                legacy_slides = legacy_index.get("slides", {}) if isinstance(legacy_index.get("slides"), dict) else {}
                slide_pages: Dict[str, str] = {}
                total_slides = len(legacy_slides)
                log.info(
                    "[INDEX_FLOW][MIGRATE] enumerate=legacy_slides total=%d source=index.json",
                    total_slides,
                )
                for idx, (slide_id, slide) in enumerate(legacy_slides.items(), start=1):
                    if self._cancel_prepare:
                        return {"ok": False, "cancelled": True}
                    if not isinstance(slide_id, str) or not isinstance(slide, dict):
                        continue
                    slide_pages[slide_id] = str(slide.get("text_for_bm25") or "")
                    if idx % 200 == 0 or idx == total_slides:
                        log.info(
                            "[INDEX_FLOW][MIGRATE] enumerate=legacy_slides total=%d current=%d",
                            total_slides,
                            idx,
                        )
                if slide_pages:
                    store.save_slide_pages(slide_pages)
                return {"ok": True}
            except Exception:
                log.exception("轉換舊版資料失敗")
                return {"ok": False, "traceback": traceback.format_exc()}

        w = Worker(task)
        w.signals.finished.connect(lambda payload: self._on_migrate_done(files, payload))
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_migrate_done(self, files: List[Dict[str, Any]], payload: object) -> None:
        if isinstance(payload, dict) and payload.get("cancelled"):
            self.prog_label.setText("已取消")
            self._reset_prepare_ui()
            return
        if not isinstance(payload, dict) or not payload.get("ok"):
            log.error("轉換舊版資料失敗\n%s", payload.get("traceback", "") if isinstance(payload, dict) else payload)
            QMessageBox.warning(self, "轉換失敗", "舊版資料轉換失敗，請查看 logs/ 了解詳細原因。")
            self._reset_prepare_ui()
            return
        QMessageBox.information(self, "轉換完成", "已完成舊版資料轉換並寫入新版檔案。")
        self._request_index_mode(files)

    def _run_fill_missing_index_timestamps(self, files: List[Dict[str, Any]]) -> None:
        if not self.ctx:
            return
        self._set_last_action("補齊索引更新時間", lambda: self._run_fill_missing_index_timestamps(files))
        self._set_prepare_ui("正在補齊索引更新時間...")
        ctx = self.ctx

        def task():
            import traceback

            try:
                if self._cancel_prepare:
                    return {"ok": False, "cancelled": True}
                store = ctx.store
                manifest = store.load_manifest()
                entries = [e for e in manifest.get("files", []) if isinstance(e, dict)]
                if store.paths.slide_pages_json.exists():
                    source_ts = int(store.paths.slide_pages_json.stat().st_mtime)
                elif store.paths.index_json.exists():
                    source_ts = int(store.paths.index_json.stat().st_mtime)
                else:
                    source_ts = int(datetime.datetime.now().timestamp())
                updated = 0
                total_entries = len(entries)
                log.info(
                    "[INDEX_FLOW][FILL] enumerate=manifest.files total=%d source=manifest.json",
                    total_entries,
                )
                for idx, entry in enumerate(entries, start=1):
                    if self._cancel_prepare:
                        return {"ok": False, "cancelled": True}
                    if not entry.get("indexed_at"):
                        entry["indexed_at"] = source_ts
                        updated += 1
                    if idx % 200 == 0 or idx == total_entries:
                        log.info(
                            "[INDEX_FLOW][FILL] enumerate=manifest.files total=%d current=%d updated=%d",
                            total_entries,
                            idx,
                            updated,
                        )
                if updated:
                    manifest["files"] = entries
                    store.save_manifest(manifest)
                return {"ok": True, "updated": updated}
            except Exception:
                log.exception("補齊索引更新時間失敗")
                return {"ok": False, "traceback": traceback.format_exc()}

        w = Worker(task)
        w.signals.finished.connect(lambda payload: self._on_fill_done(files, payload))
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_fill_done(self, files: List[Dict[str, Any]], payload: object) -> None:
        if isinstance(payload, dict) and payload.get("cancelled"):
            self.prog_label.setText("已取消")
            self._reset_prepare_ui()
            return
        if not isinstance(payload, dict) or not payload.get("ok"):
            log.error("補齊索引更新時間失敗\n%s", payload.get("traceback", "") if isinstance(payload, dict) else payload)
            QMessageBox.warning(self, "補齊失敗", "補齊索引更新時間失敗，請查看 logs/ 了解詳細原因。")
            self._reset_prepare_ui()
            return
        updated = payload.get("updated", 0)
        QMessageBox.information(
            self,
            "補齊完成",
            f"已補齊 {updated} 筆索引更新時間（來源：slide_pages.json 或 index.json 存檔日）。",
        )
        self._request_index_mode(files)


    def _start_index(self, files: List[Dict[str, Any]], update_text: bool, update_image: bool) -> None:
        if not self.ctx:
            return
        library_roots = self._select_library_roots()
        if not library_roots:
            return
        log.info(
            "[INDEX_FLOW][START] scope_only=%s files_count=%d options=text:%s image:%s roots=%s",
            self._index_status_payload.get("scope_only"),
            len(files),
            update_text,
            update_image,
            library_roots,
        )
        options = {
            "enable_text": update_text,
            "enable_thumb": update_image,
            "enable_text_vec": update_text,
            "enable_img_vec": update_image,
            "enable_bm25": update_text,
        }
        file_paths = [f.get("abs_path") for f in files if f.get("abs_path")]
        if len(library_roots) > 1 and hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast(
                f"偵測到多個目錄，將依序索引 {len(library_roots)} 個。",
                level="info",
                timeout_ms=8000,
            )
        roots_with_files: List[str] = []
        files_by_root: Dict[str, List[str]] = {}
        for root in library_roots:
            root_path = Path(root)
            scoped = []
            for path in file_paths:
                try:
                    if root_path in Path(path).parents or Path(path) == root_path:
                        scoped.append(path)
                except Exception:
                    continue
            if scoped:
                roots_with_files.append(root)
                files_by_root[root] = scoped

        if not roots_with_files:
            QMessageBox.information(self, "沒有可索引檔案", "目前沒有符合條件的 PPTX 檔案可索引。")
            self._reset_prepare_ui()
            return

        self._pending_index_roots = list(roots_with_files[1:])
        self._pending_index_options = dict(options)
        self._pending_index_files_by_root = files_by_root
        self._start_index_job_for_root(roots_with_files[0], options, files_by_root[roots_with_files[0]])

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

    def _select_library_roots(self) -> List[str]:
        if not self.ctx:
            return []
        entries = [e for e in self.ctx.catalog.get_whitelist_entries() if e.get("enabled", True)]
        if not entries:
            QMessageBox.information(self, "尚未設定目錄", "請先在白名單新增至少一個目錄。")
            return []
        roots = [str(e.get("path", "")).strip() for e in entries if str(e.get("path", "")).strip()]
        return [r for r in roots if r]

    def _start_index_job_for_root(
        self,
        library_root: str,
        options: Dict[str, Any],
        file_paths: List[str],
    ) -> None:
        if not self.ctx:
            return
        log.info(
            "[INDEX_FLOW][START] step=start_index_job root=%s options=%s",
            library_root,
            options,
        )
        self._cancel_index = False
        self._indexing_active = True
        self._job_paused = False
        self.btn_cancel.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(False)
        self.btn_index_selected.setEnabled(False)

        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog_label.setText("索引中...")

        def task():
            log.info("[INDEX_FLOW][START] step=ensure_backend_ready")
            if not self.ctx.indexer.ensure_backend_ready():
                backend_host = get_backend_host()
                backend_port = get_backend_port()
                return {
                    "job_id": None,
                    "error": (
                        f"無法連線後台 daemon（{backend_host}:{backend_port}）。"
                        "請確認 daemon 是否已啟動。"
                    ),
                }
            job_id = self.ctx.indexer.start_index_job(
                library_root,
                plan_mode="missing_or_changed",
                options={**options, "file_paths": file_paths},
            )
            if not job_id:
                return {"job_id": None, "error": "啟動後台任務失敗，請確認 daemon 狀態。"}
            log.info("[INDEX_FLOW][START] step=job_created job_id=%s root=%s", job_id, library_root)
            return {"job_id": job_id, "root": library_root}

        w = Worker(task)
        w.signals.finished.connect(self._on_job_started)
        w.signals.error.connect(self._on_error)
        self.main_window.thread_pool.start(w)

    def _on_job_started(self, payload: object) -> None:
        job_id = None
        error_message = None
        root = None
        if isinstance(payload, dict):
            job_id = payload.get("job_id")
            error_message = payload.get("error")
            root = payload.get("root")
        if not job_id:
            log.error("[INDEX_FLOW][START] step=job_start_failed error=%s", error_message)
            self._indexing_active = False
            self.btn_cancel.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_index_needed.setEnabled(True)
            self.btn_index_selected.setEnabled(True)
            self._pending_index_roots = []
            message = error_message or "啟動後台任務失敗，請確認 daemon 狀態"
            self.prog_label.setText(message)
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast(message, level="error", timeout_ms=12000)
            return
        self._job_id = str(job_id)
        log.info("[INDEX_FLOW][START] step=job_started job_id=%s root=%s", job_id, root)
        if root and hasattr(self.main_window, "status"):
            self.main_window.status.showMessage(f"正在索引目錄：{root}")
        self.prog_label.setText(f"已送出任務 {job_id}")
        self._start_job_sse(job_id)
        self._schedule_job_snapshot()

    def _start_job_sse(self, job_id: str) -> None:
        self._stop_job_sse()
        if not self.ctx:
            return
        worker = self.ctx.indexer.sse_worker_for_job(job_id)
        worker.event_received.connect(self._on_job_event)
        worker.state_changed.connect(self._on_job_state)
        worker.error.connect(self._on_job_error)
        self._job_worker = worker
        worker.start()

    def _stop_job_sse(self) -> None:
        if self._job_worker is None:
            return
        try:
            self._job_worker.stop()
            self._job_worker.wait(500)
        except Exception:
            log.exception("停止 SSE worker 失敗")
        self._job_worker = None

    def _on_job_event(self, payload: dict) -> None:
        event_type = payload.get("type")
        ev_payload = payload.get("payload") or {}
        log.info("[INDEX_FLOW][EVENT] type=%s payload_keys=%s", event_type, list(ev_payload.keys()))
        if event_type == "artifact_state_changed":
            file_path = ev_payload.get("file") or ""
            page_no = ev_payload.get("page_no")
            kind = ev_payload.get("kind")
            msg = f"完成 {kind}：{file_path}"
            if page_no:
                msg = f"完成 {kind}：{file_path} (第 {page_no} 頁)"
            self.prog_label.setText(msg)
        elif event_type == "job_completed":
            self._finish_job("索引完成", status="completed")
            return
        elif event_type == "job_cancelled":
            self._finish_job("已取消", status="cancelled")
            return
        elif event_type == "job_failed":
            self._finish_job("索引失敗", status="failed")
            return
        elif event_type in {"job_paused", "job_resumed"}:
            self._job_paused = event_type == "job_paused"
            self.btn_pause.setText("續跑" if self._job_paused else "暫停")
        self._schedule_job_snapshot()

    def _on_job_state(self, state: str) -> None:
        log.info("[INDEX_FLOW][EVENT] state=%s", state)
        if state in {"reconnecting", "disconnected"}:
            if not self._job_poll_timer.isActive():
                self._job_poll_timer.start()
            self.prog_label.setText("事件串流中斷，正在重連...")
        elif state == "connected":
            if self._job_poll_timer.isActive():
                self._job_poll_timer.stop()
            self._schedule_job_snapshot()

    def _on_job_error(self, message: str) -> None:
        log.warning("[INDEX_FLOW][EVENT] SSE 錯誤：%s", message)
        if hasattr(self.main_window, "show_toast"):
            self.main_window.show_toast("事件串流中斷，正在嘗試重連。", level="warning")

    def _schedule_job_snapshot(self) -> None:
        if not self._job_snapshot_timer.isActive():
            self._job_snapshot_timer.start()

    def _request_job_snapshot(self) -> None:
        if not self.ctx or not self._job_id or self._job_poll_inflight:
            return
        self._job_poll_inflight = True
        job_id = self._job_id

        def task():
            return self.ctx.indexer.get_job(job_id)

        w = Worker(task)
        w.signals.finished.connect(self._on_job_snapshot)
        w.signals.error.connect(self._on_job_snapshot_error)
        self.main_window.thread_pool.start(w)

    def _poll_job_status(self) -> None:
        if not self._job_id:
            return
        self._request_job_snapshot()

    def _on_job_snapshot(self, payload: object) -> None:
        self._job_poll_inflight = False
        if not isinstance(payload, dict) or not payload.get("ok"):
            log.warning("[INDEX_FLOW][SNAPSHOT] payload_invalid=%s", payload)
            return
        self._apply_job_snapshot(payload)

    def _on_job_snapshot_error(self, tb: str) -> None:
        self._job_poll_inflight = False
        log.warning("Job snapshot 失敗：%s", tb)

    def _apply_job_snapshot(self, payload: dict) -> None:
        stats = payload.get("stats", {}) if isinstance(payload.get("stats"), dict) else {}
        options = payload.get("options", {}) if isinstance(payload.get("options"), dict) else {}
        enabled_kinds = []
        if options.get("enable_text"):
            enabled_kinds.append("text")
        if options.get("enable_thumb"):
            enabled_kinds.append("thumb")
        if options.get("enable_text_vec"):
            enabled_kinds.append("text_vec")
        if options.get("enable_img_vec"):
            enabled_kinds.append("img_vec")
        if options.get("enable_bm25"):
            enabled_kinds.append("bm25")

        total = 0
        ready = 0
        running = 0
        queued = 0
        error = 0
        total_kinds = len(enabled_kinds)
        log.info(
            "[INDEX_FLOW][SNAPSHOT] enumerate=enabled_kinds total=%d kinds=%s",
            total_kinds,
            enabled_kinds,
        )
        for idx, kind in enumerate(enabled_kinds, start=1):
            kind_stats = stats.get(kind, {})
            total += sum(int(v) for v in kind_stats.values())
            ready += int(kind_stats.get("ready", 0))
            running += int(kind_stats.get("running", 0))
            queued += int(kind_stats.get("queued", 0))
            error += int(kind_stats.get("error", 0))
            log.info(
                "[INDEX_FLOW][SNAPSHOT] enumerate=enabled_kinds total=%d current=%d kind=%s",
                total_kinds,
                idx,
                kind,
            )

        if total > 0:
            percent = int(ready / total * 100)
            self.prog.setValue(percent)
            self.prog.setRange(0, 100)
            detail = f"{ready}/{total} 完成"
            if running:
                detail += f"，執行中 {running}"
            if queued:
                detail += f"，排隊中 {queued}"
            if error:
                detail += f"，錯誤 {error}"
            self.prog_label.setText(detail)
            log.info(
                "[INDEX_FLOW][SNAPSHOT] progress total=%d ready=%d running=%d queued=%d error=%d",
                total,
                ready,
                running,
                queued,
                error,
            )

        now_running = payload.get("now_running") or {}
        if isinstance(now_running, dict) and now_running.get("file_path"):
            running_msg = f"正在處理：{now_running.get('file_path')}"
            if now_running.get("page_no"):
                running_msg += f" (第 {now_running.get('page_no')} 頁)"
            if now_running.get("kind"):
                running_msg += f" [{now_running.get('kind')}]"
            self.main_window.status.showMessage(running_msg)

        status = str(payload.get("status", ""))
        if status in {"completed", "cancelled", "failed"}:
            status_map = {
                "completed": "索引完成",
                "cancelled": "已取消",
                "failed": "索引失敗",
            }
            log.info("[INDEX_FLOW][SNAPSHOT] status=%s", status)
            self._finish_job(status_map.get(status, "索引完成"), status=status)

    def cancel_indexing(self) -> None:
        self._cancel_index = True
        self._cancel_scan = True
        self._cancel_prepare = True
        self._pending_index_roots = []
        if self._job_id and self.ctx:
            ok = self.ctx.indexer.cancel_job(self._job_id)
            if not ok and hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("取消任務失敗，請確認 daemon 狀態。", level="error")
        self.prog_label.setText("取消中...")

    def toggle_pause_indexing(self) -> None:
        if not self._job_id or not self.ctx:
            return
        if self._job_paused:
            ok = self.ctx.indexer.resume_job(self._job_id)
            if ok:
                self._job_paused = False
                self.btn_pause.setText("暫停")
                self.prog_label.setText("索引中...")
            elif hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("恢復任務失敗，請確認 daemon 狀態。", level="error")
        else:
            ok = self.ctx.indexer.pause_job(self._job_id)
            if ok:
                self._job_paused = True
                self.btn_pause.setText("續跑")
                self.prog_label.setText("已暫停")
            elif hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("暫停任務失敗，請確認 daemon 狀態。", level="error")

    def _on_index_done(self, result: object) -> None:
        try:
            code, msg = result
        except Exception:
            code, msg = 0, "索引完成"
        self._finish_job(msg if code == 0 else "索引完成")

    def _finish_job(self, message: str, *, status: str | None = None) -> None:
        log.info("[INDEX_FLOW][FINISH] status=%s message=%s", status, message)
        self._indexing_active = False
        self._job_id = None
        self._job_paused = False
        if self._job_poll_timer.isActive():
            self._job_poll_timer.stop()
        if self._job_snapshot_timer.isActive():
            self._job_snapshot_timer.stop()
        self._stop_job_sse()
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暫停")
        self.btn_index_needed.setEnabled(True)
        self.btn_index_selected.setEnabled(True)
        self._vectors_loaded = False
        self._cached_text_vector_keys = set()
        self._cached_image_vector_keys = set()
        self.refresh_table()
        self.refresh_dirs()
        if hasattr(self.main_window, "dashboard_tab"):
            self.main_window.dashboard_tab.refresh_metrics()
        self.prog.setValue(100)
        self.prog_label.setText(message)
        if status == "completed" and self._pending_index_roots:
            next_root = None
            while self._pending_index_roots:
                candidate = self._pending_index_roots.pop(0)
                if self._pending_index_files_by_root.get(candidate):
                    next_root = candidate
                    break
            if next_root:
                options = dict(self._pending_index_options)
                if hasattr(self.main_window, "show_toast"):
                    self.main_window.show_toast(
                        f"開始索引下一個目錄：{next_root}",
                        level="info",
                        timeout_ms=8000,
                    )
                file_paths = self._pending_index_files_by_root.get(next_root, [])
                self._start_index_job_for_root(next_root, options, file_paths)
        elif status in {"failed", "cancelled"}:
            self._pending_index_roots = []
            self._pending_index_files_by_root = {}
        elif status == "completed" and not self._pending_index_roots:
            self._pending_index_files_by_root = {}

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
            if self._scan_in_progress and self._scan_files_cache:
                self._refresh_table_with_files(self._scan_files_cache)
            else:
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
