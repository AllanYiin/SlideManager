# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger

log = get_logger(__name__)


class SortableItem(QTableWidgetItem):
    def __init__(self, text: str, sort_key: object | None = None) -> None:
        super().__init__(text)
        self._sort_key = sort_key if sort_key is not None else text

    def __lt__(self, other: QTableWidgetItem) -> bool:
        other_key = getattr(other, "_sort_key", other.text())
        return self._sort_key < other_key


@dataclass
class PageMetrics:
    slide_total: int = 0
    text_count: int = 0
    image_count: int = 0
    text_vec_count: int = 0
    image_vec_count: int = 0
    bm25_count: int = 0


class PageStatusTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None

        root = QVBoxLayout(self)
        header_row = QHBoxLayout()
        title = QLabel("頁面狀態")
        title.setStyleSheet("font-size: 20px; color: #0F172A; font-weight: 600;")
        header_row.addWidget(title)
        header_row.addStretch(1)
        self.btn_refresh = QPushButton("重新整理")
        self.btn_refresh.setStyleSheet(
            "QPushButton{background:#2563EB;color:#fff;padding:6px 14px;border-radius:8px;}"
            "QPushButton:hover{background:#1E4FD7;}"
        )
        header_row.addWidget(self.btn_refresh)
        root.addLayout(header_row)

        subtitle = QLabel("每一頁的文字/縮圖/向量/BM25 狀態與統計")
        subtitle.setStyleSheet("font-size: 12px; color: #64748B;")
        root.addWidget(subtitle)

        self.summary_frame = self._build_card("投影片覆蓋率")
        summary_layout = QVBoxLayout()
        summary_layout.setSpacing(8)
        self.total_label = QLabel("總頁數：0")
        self.total_label.setStyleSheet("font-size: 12px; color: #0F172A;")
        summary_layout.addWidget(self.total_label)
        self.text_row = self._build_ratio_row("文字資料")
        self.image_row = self._build_ratio_row("縮圖資料")
        self.text_vec_row = self._build_ratio_row("文字向量")
        self.image_vec_row = self._build_ratio_row("縮圖向量")
        self.bm25_row = self._build_ratio_row("BM25 索引")
        for row in [self.text_row, self.image_row, self.text_vec_row, self.image_vec_row, self.bm25_row]:
            summary_layout.addLayout(row.container)
        self.summary_frame.layout().addLayout(summary_layout)
        root.addWidget(self.summary_frame)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("篩選："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("檔名/路徑關鍵字")
        filter_row.addWidget(self.filter_edit)
        self.missing_only = QCheckBox("只顯示缺漏")
        filter_row.addWidget(self.missing_only)
        filter_row.addStretch(1)
        root.addLayout(filter_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["檔名", "頁碼", "文字", "縮圖", "文字向量", "縮圖向量", "BM25"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        root.addWidget(self.table, 1)

        self.btn_refresh.clicked.connect(self.refresh_data)
        self.filter_edit.textChanged.connect(self.refresh_table)
        self.missing_only.stateChanged.connect(self.refresh_table)

        self._cached_rows: List[Dict[str, Any]] = []

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self.refresh_data()

    def refresh_data(self) -> None:
        if not self.ctx:
            self._cached_rows = []
            self.table.setRowCount(0)
            self._render_metrics(PageMetrics())
            return
        try:
            catalog = self.ctx.store.load_manifest()
            meta = self.ctx.store.load_meta()
            files = [e for e in catalog.get("files", []) if isinstance(e, dict)]
            slides_map = meta.get("slides", {}) if isinstance(meta.get("slides"), dict) else {}
        except Exception as exc:
            log.error("讀取頁面狀態失敗：%s", exc)
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("讀取頁面狀態失敗，已寫入 logs/app.log。", level="error")
            self._cached_rows = []
            self.table.setRowCount(0)
            self._render_metrics(PageMetrics())
            return

        file_map = {f.get("file_id"): f for f in files if f.get("file_id")}
        slides_by_file: Dict[str, Dict[int, Dict[str, Any]]] = {}
        for slide in slides_map.values():
            if not isinstance(slide, dict):
                continue
            file_id = slide.get("file_id")
            slide_no = slide.get("slide_no")
            if not file_id or not slide_no:
                continue
            try:
                slide_no_int = int(slide_no)
            except Exception:
                continue
            slides_by_file.setdefault(file_id, {})[slide_no_int] = slide

        rows: List[Dict[str, Any]] = []
        for file_entry in files:
            file_id = file_entry.get("file_id")
            if not file_id:
                continue
            slide_count = file_entry.get("slide_count")
            try:
                slide_total = int(slide_count) if slide_count is not None else 0
            except Exception:
                slide_total = 0
            file_rows = self._build_file_rows(
                file_entry,
                slides_by_file.get(file_id, {}),
                slide_total=slide_total,
            )
            rows.extend(file_rows)

        if not rows:
            for slide in slides_map.values():
                if not isinstance(slide, dict):
                    continue
                rows.append(self._build_row_from_slide(slide, file_map.get(slide.get("file_id"))))

        self._cached_rows = rows
        metrics = self._compute_metrics(files, rows)
        self._render_metrics(metrics)
        self.refresh_table()

    def refresh_table(self) -> None:
        kw = (self.filter_edit.text() or "").strip().lower()
        missing_only = self.missing_only.isChecked()
        rows = self._cached_rows
        if kw:
            rows = [
                r
                for r in rows
                if kw in r.get("filename", "").lower() or kw in r.get("path", "").lower()
            ]
        if missing_only:
            rows = [r for r in rows if not r.get("all_ok")]

        self.table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            self._set_row(idx, row)

    def _build_file_rows(
        self,
        file_entry: Dict[str, Any],
        slides: Dict[int, Dict[str, Any]],
        *,
        slide_total: int,
    ) -> List[Dict[str, Any]]:
        file_rows: List[Dict[str, Any]] = []
        if slide_total > 0:
            for page in range(1, slide_total + 1):
                slide = slides.get(page)
                if slide:
                    file_rows.append(self._build_row_from_slide(slide, file_entry))
                else:
                    file_rows.append(self._build_placeholder_row(file_entry, page))
            return file_rows
        for page in sorted(slides.keys()):
            file_rows.append(self._build_row_from_slide(slides[page], file_entry))
        return file_rows

    def _build_placeholder_row(self, file_entry: Dict[str, Any], page: int) -> Dict[str, Any]:
        return {
            "filename": self._display_filename(file_entry),
            "path": self._display_path(file_entry),
            "page": page,
            "has_text": False,
            "has_image": False,
            "has_text_vec": False,
            "has_image_vec": False,
            "has_bm25": False,
            "all_ok": False,
        }

    def _build_row_from_slide(
        self, slide: Dict[str, Any], file_entry: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        flags = slide.get("flags") if isinstance(slide.get("flags"), dict) else {}
        has_text = bool(flags.get("has_text"))
        has_image = bool(flags.get("has_image"))
        has_text_vec = bool(flags.get("has_text_vec"))
        has_image_vec = bool(flags.get("has_image_vec"))
        has_bm25 = bool(flags.get("has_bm25"))
        return {
            "filename": self._display_filename(file_entry, slide),
            "path": self._display_path(file_entry, slide),
            "page": slide.get("slide_no") or slide.get("page") or 0,
            "has_text": has_text,
            "has_image": has_image,
            "has_text_vec": has_text_vec,
            "has_image_vec": has_image_vec,
            "has_bm25": has_bm25,
            "all_ok": all([has_text, has_image, has_text_vec, has_image_vec, has_bm25]),
        }

    def _display_filename(
        self, file_entry: Optional[Dict[str, Any]], slide: Optional[Dict[str, Any]] = None
    ) -> str:
        if file_entry:
            return file_entry.get("filename") or file_entry.get("file_name") or file_entry.get("name") or ""
        if slide:
            return str(slide.get("file_id") or "")
        return ""

    def _display_path(
        self, file_entry: Optional[Dict[str, Any]], slide: Optional[Dict[str, Any]] = None
    ) -> str:
        if file_entry:
            return file_entry.get("abs_path") or file_entry.get("file_path") or file_entry.get("path") or ""
        if slide:
            return str(slide.get("file_id") or "")
        return ""

    @dataclass
    class _RatioRow:
        container: QHBoxLayout
        label: QLabel
        bar: QProgressBar
        value: QLabel

    def _build_card(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:16px;padding:12px;}"
        )
        layout = QVBoxLayout(frame)
        header = QLabel(title)
        header.setStyleSheet("font-size: 14px; color: #0F172A; font-weight: 600;")
        layout.addWidget(header)
        return frame

    def _build_ratio_row(self, title: str) -> _RatioRow:
        row = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-size: 12px; color: #64748B;")
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bar.setStyleSheet(
            "QProgressBar{background:#E2E8F0;border:none;border-radius:5px;}"
            "QProgressBar::chunk{background:#2563EB;border-radius:5px;}"
        )
        value = QLabel("0%")
        value.setStyleSheet("font-size: 12px; color: #0F172A;")
        row.addWidget(label, 2)
        row.addWidget(bar, 6)
        row.addWidget(value, 1)
        return PageStatusTab._RatioRow(row, label, bar, value)

    def _compute_metrics(self, files: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> PageMetrics:
        slide_total = self._compute_slide_total(files, rows)
        text_count = sum(1 for r in rows if r.get("has_text"))
        image_count = sum(1 for r in rows if r.get("has_image"))
        text_vec_count = sum(1 for r in rows if r.get("has_text_vec"))
        image_vec_count = sum(1 for r in rows if r.get("has_image_vec"))
        bm25_count = sum(1 for r in rows if r.get("has_bm25"))
        return PageMetrics(
            slide_total=slide_total,
            text_count=text_count,
            image_count=image_count,
            text_vec_count=text_vec_count,
            image_vec_count=image_vec_count,
            bm25_count=bm25_count,
        )

    def _compute_slide_total(self, files: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> int:
        total = 0
        for file_entry in files:
            slide_count = file_entry.get("slide_count")
            if slide_count is None:
                continue
            try:
                total += int(slide_count)
            except Exception:
                continue
        if total > 0:
            return total
        return len(rows)

    def _render_metrics(self, metrics: PageMetrics) -> None:
        self.total_label.setText(f"總頁數：{metrics.slide_total}")
        self._set_ratio(self.text_row, metrics.text_count, metrics.slide_total)
        self._set_ratio(self.image_row, metrics.image_count, metrics.slide_total)
        self._set_ratio(self.text_vec_row, metrics.text_vec_count, metrics.slide_total)
        self._set_ratio(self.image_vec_row, metrics.image_vec_count, metrics.slide_total)
        self._set_ratio(self.bm25_row, metrics.bm25_count, metrics.slide_total)

    def _set_ratio(self, row: _RatioRow, num: int, denom: int) -> None:
        pct = int((num / denom) * 100) if denom > 0 else 0
        row.bar.setValue(pct)
        row.value.setText(f"{pct}%")
        label_title = row.label.text().split("（")[0].strip()
        row.label.setText(f"{label_title}（{num} / {denom}）")

    def _set_row(self, r: int, row: Dict[str, Any]) -> None:
        filename = row.get("filename", "")
        page = row.get("page", 0)
        values = [
            filename,
            str(page),
            self._format_flag(row.get("has_text")),
            self._format_flag(row.get("has_image")),
            self._format_flag(row.get("has_text_vec")),
            self._format_flag(row.get("has_image_vec")),
            self._format_flag(row.get("has_bm25")),
        ]
        sort_keys = [
            filename.lower(),
            int(page) if str(page).isdigit() else 0,
            int(row.get("has_text", False)),
            int(row.get("has_image", False)),
            int(row.get("has_text_vec", False)),
            int(row.get("has_image_vec", False)),
            int(row.get("has_bm25", False)),
        ]
        for c, val in enumerate(values):
            item = SortableItem(str(val), sort_keys[c])
            item.setFlags(item.flags() ^ Qt.ItemIsEditable)
            if c >= 2 and val == "無":
                item.setBackground(QColor("#FEF3C7"))
            if not row.get("all_ok") and c == 0:
                item.setToolTip("此頁面資料尚未完整")
            self.table.setItem(r, c, item)

    @staticmethod
    def _format_flag(flag: Any) -> str:
        return "有" if flag else "無"
