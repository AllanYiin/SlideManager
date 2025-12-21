# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger
from app.ui.metrics import classify_doc_status

log = get_logger(__name__)


class ClickableFrame(QFrame):
    def __init__(self, on_click=None):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if callable(self._on_click):
            self._on_click()
        super().mousePressEvent(event)


class ClickableProgressBar(QProgressBar):
    def __init__(self, on_click=None):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if callable(self._on_click):
            self._on_click()
        super().mousePressEvent(event)


@dataclass
class DashboardMetrics:
    doc_total: int = 0
    doc_indexed: int = 0
    doc_pending: int = 0
    doc_stale: int = 0
    doc_error: int = 0
    doc_partial: int = 0
    slide_total: int = 0
    slide_indexed: int = 0
    avg_slides_per_doc: float = 0.0
    bm25_coverage: float = 0.0
    text_coverage: float = 0.0
    image_coverage: float = 0.0
    fusion_coverage: float = 0.0
    fusion_full_coverage: float = 0.0
    fusion_note: str = ""


class DashboardTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None

        self.setStyleSheet("background: #F8FAFC;")

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet("font-size: 20px; color: #0F172A; font-weight: 600;")
        header_row.addWidget(title)
        header_row.addStretch(1)
        self.btn_refresh = QPushButton("重新整理")
        self.btn_refresh.setStyleSheet(
            "QPushButton{background:#2563EB;color:#fff;padding:6px 14px;border-radius:8px;}"
            "QPushButton:hover{background:#1E4FD7;}"
        )
        header_row.addWidget(self.btn_refresh)
        layout.addLayout(header_row)

        subtitle = QLabel("目錄概況 / 索引覆蓋率 / 關鍵狀態")
        subtitle.setStyleSheet("font-size: 12px; color: #64748B;")
        layout.addWidget(subtitle)

        self.kpi_cards: Dict[str, QLabel] = {}
        kpi_grid = QGridLayout()
        kpi_grid.setHorizontalSpacing(16)
        kpi_grid.setVerticalSpacing(16)
        layout.addLayout(kpi_grid)

        kpi_specs = [
            ("doc_total", "PPTX 檔案數"),
            ("slide_total", "投影片總頁數"),
            ("avg_slides_per_doc", "平均每份頁數"),
        ]
        for idx, (key, label) in enumerate(kpi_specs):
            frame, value_label = self._build_kpi_card(label)
            self.kpi_cards[key] = value_label
            kpi_grid.addWidget(frame, 0, idx)

        self.status_frame = self._build_card("索引狀態摘要")
        status_layout = QHBoxLayout()
        status_layout.setSpacing(12)
        self.status_buttons: Dict[str, QPushButton] = {}
        for code, label, color in [
            ("pending", "待索引", "#F59E0B"),
            ("stale", "已過期", "#F59E0B"),
            ("partial", "部分索引", "#2563EB"),
            ("error", "錯誤", "#DC2626"),
            ("indexed", "已索引", "#16A34A"),
        ]:
            btn = QPushButton(label)
            btn.setProperty("status_code", code)
            btn.setStyleSheet(
                "QPushButton{border:1px solid #E2E8F0;border-radius:10px;padding:8px 12px;"
                f"color:{color};background:#FFFFFF;text-align:left;}}"
                "QPushButton:hover{background:#F1F5F9;}"
            )
            btn.clicked.connect(self._on_status_click)
            self.status_buttons[code] = btn
            status_layout.addWidget(btn)
        status_layout.addStretch(1)
        self.status_frame.layout().addLayout(status_layout)
        layout.addWidget(self.status_frame)

        self.index_ratio_frame = self._build_card("索引編列比例")
        ratio_layout = QVBoxLayout()
        ratio_layout.setSpacing(10)
        self.doc_ratio_row = self._build_ratio_row("文件層 (Indexed / Total)")
        self.slide_ratio_row = self._build_ratio_row("頁面層 (Indexed Slides / Total Slides)")
        ratio_layout.addLayout(self.doc_ratio_row.container)
        ratio_layout.addLayout(self.slide_ratio_row.container)
        self.index_ratio_frame.layout().addLayout(ratio_layout)
        layout.addWidget(self.index_ratio_frame)

        coverage_row = QHBoxLayout()
        self.coverage_frame = self._build_card("索引覆蓋率")
        coverage_layout = QVBoxLayout()
        self.bm25_row = self._build_ratio_row(
            "BM25 (文字關鍵字)",
            bar_color="#16A34A",
            on_click=lambda: self._apply_coverage_filter("bm25_missing"),
        )
        self.text_row = self._build_ratio_row(
            "Text Embedding (向量)",
            bar_color="#16A34A",
            on_click=lambda: self._apply_coverage_filter("text_missing"),
        )
        coverage_layout.addLayout(self.bm25_row.container)
        coverage_layout.addLayout(self.text_row.container)
        self.coverage_frame.layout().addLayout(coverage_layout)

        self.fusion_frame = self._build_card("圖像與融合")
        fusion_layout = QVBoxLayout()
        self.image_row = self._build_ratio_row(
            "Image Vector (512d)",
            bar_color="#F59E0B",
            on_click=lambda: self._apply_coverage_filter("image_missing"),
        )
        self.fusion_row = self._build_ratio_row(
            "Fusion Full (文字+圖像都具備)",
            bar_color="#2563EB",
            on_click=lambda: self._apply_coverage_filter("fusion_missing"),
        )
        fusion_layout.addLayout(self.image_row.container)
        fusion_layout.addLayout(self.fusion_row.container)
        self.fusion_note = QLabel("")
        self.fusion_note.setStyleSheet("font-size: 11px; color: #64748B;")
        fusion_layout.addWidget(self.fusion_note)
        self.fusion_frame.layout().addLayout(fusion_layout)

        coverage_row.addWidget(self.coverage_frame)
        coverage_row.addWidget(self.fusion_frame)
        layout.addLayout(coverage_row)

        self.btn_refresh.clicked.connect(self.refresh_metrics)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self.refresh_metrics()

    def refresh_metrics(self) -> None:
        metrics = self._compute_metrics()
        self._render_metrics(metrics)

    def _compute_metrics(self) -> DashboardMetrics:
        if not self.ctx:
            return DashboardMetrics()
        try:
            catalog = self.ctx.store.load_catalog()
            index = self.ctx.store.load_index()
            files = [e for e in catalog.get("files", []) if isinstance(e, dict)]
            slides = [s for s in index.get("slides", []) if isinstance(s, dict)]
        except Exception as exc:
            log.error("Dashboard 讀取資料失敗：%s", exc)
            if hasattr(self.main_window, "show_toast"):
                self.main_window.show_toast("Dashboard 讀取資料失敗，已寫入 logs/app.log。", level="error")
            return DashboardMetrics()

        docs = [f for f in files if not f.get("missing")]
        doc_total = len(docs)
        doc_indexed = 0
        doc_pending = 0
        doc_stale = 0
        doc_error = 0
        doc_partial = 0
        for entry in docs:
            status = classify_doc_status(entry)
            if status == "indexed":
                doc_indexed += 1
            elif status == "pending":
                doc_pending += 1
            elif status == "stale":
                doc_stale += 1
            elif status == "error":
                doc_error += 1
            elif status == "partial":
                doc_partial += 1

        slide_total = self._compute_slide_total(docs, slides)
        doc_paths = {f.get("abs_path") for f in docs if f.get("abs_path")}
        slide_indexed = sum(
            1
            for s in slides
            if s.get("indexed_at") and s.get("file_path") in doc_paths
        )

        text_dim = int(index.get("embedding", {}).get("text_dim") or 1536)
        image_dim = int(index.get("embedding", {}).get("image_dim") or 4096)
        concat_dim = text_dim + image_dim

        bm25_ok = 0
        text_ok = 0
        image_ok = 0
        fusion_ok = 0
        fusion_full_ok = 0
        for s in slides:
            if s.get("file_path") not in doc_paths:
                continue
            bm25_tokens = s.get("bm25_tokens") or []
            if isinstance(bm25_tokens, list) and len(bm25_tokens) > 0:
                bm25_ok += 1
            if self._vec_len_ok(s.get("text_vec"), text_dim):
                text_ok += 1
            if self._vec_len_ok(s.get("image_vec"), image_dim):
                image_ok += 1
            if self._vec_len_ok(s.get("concat_vec"), concat_dim):
                fusion_ok += 1
            if self._vec_len_ok(s.get("text_vec"), text_dim) and self._vec_len_ok(s.get("image_vec"), image_dim):
                fusion_full_ok += 1

        denom = slide_total if slide_total > 0 else 1
        fusion_note = "融合向量已存於 index.json"
        if not any(s.get("concat_vec") for s in slides):
            fusion_note = "融合向量為查詢時組合"

        return DashboardMetrics(
            doc_total=doc_total,
            doc_indexed=doc_indexed,
            doc_pending=doc_pending,
            doc_stale=doc_stale,
            doc_error=doc_error,
            doc_partial=doc_partial,
            slide_total=slide_total,
            slide_indexed=slide_indexed,
            avg_slides_per_doc=(slide_total / doc_total) if doc_total else 0.0,
            bm25_coverage=bm25_ok / denom,
            text_coverage=text_ok / denom,
            image_coverage=image_ok / denom,
            fusion_coverage=fusion_ok / denom,
            fusion_full_coverage=fusion_full_ok / denom,
            fusion_note=fusion_note,
        )

    def _compute_slide_total(self, docs: List[Dict[str, Any]], slides: List[Dict[str, Any]]) -> int:
        by_path: Dict[str, int] = {}
        for s in slides:
            path = s.get("file_path")
            if not path:
                continue
            by_path[path] = by_path.get(path, 0) + 1
        total = 0
        for doc in docs:
            slide_count = doc.get("slide_count")
            if slide_count is None:
                total += by_path.get(doc.get("abs_path"), 0)
                continue
            try:
                total += int(slide_count)
            except Exception:
                total += by_path.get(doc.get("abs_path"), 0)
        if total == 0:
            total = len(slides)
        return total

    def _vec_len_ok(self, vec_b64: Any, expected_dim: int) -> bool:
        if not vec_b64 or not expected_dim:
            return False
        if not isinstance(vec_b64, str):
            return False
        try:
            raw = base64.b64decode(vec_b64.encode("ascii"))
        except Exception:
            return False
        return len(raw) == expected_dim * 4

    def _build_kpi_card(self, title: str) -> tuple[QFrame, QLabel]:
        frame = ClickableFrame(self._clear_filters)
        frame.setStyleSheet(
            "QFrame{background:#FFFFFF;border:1px solid #E2E8F0;border-radius:16px;padding:12px;}"
        )
        layout = QVBoxLayout(frame)
        label = QLabel(title)
        label.setStyleSheet("font-size: 12px; color: #64748B;")
        value = QLabel("0")
        value.setStyleSheet("font-size: 38px; color: #0F172A; font-weight: 600;")
        layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(value)
        return frame, value

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

    @dataclass
    class _RatioRow:
        container: QHBoxLayout
        label: QLabel
        bar: QProgressBar
        value: QLabel

    def _build_ratio_row(
        self,
        title: str,
        *,
        bar_color: str = "#2563EB",
        on_click=None,
    ) -> _RatioRow:
        row = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-size: 12px; color: #64748B;")
        if on_click:
            bar = ClickableProgressBar(on_click)
        else:
            bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        bar.setStyleSheet(
            "QProgressBar{background:#E2E8F0;border:none;border-radius:5px;}"
            f"QProgressBar::chunk{{background:{bar_color};border-radius:5px;}}"
        )
        value = QLabel("0%")
        value.setStyleSheet("font-size: 12px; color: #0F172A;")
        row.addWidget(label, 2)
        row.addWidget(bar, 6)
        row.addWidget(value, 1)
        return DashboardTab._RatioRow(row, label, bar, value)

    def _render_metrics(self, m: DashboardMetrics) -> None:
        self.kpi_cards["doc_total"].setText(self._format_int(m.doc_total))
        self.kpi_cards["slide_total"].setText(self._format_int(m.slide_total))
        self.kpi_cards["avg_slides_per_doc"].setText(f"{m.avg_slides_per_doc:.1f}")

        self._set_ratio(self.doc_ratio_row, m.doc_indexed, m.doc_total)
        self._set_ratio(self.slide_ratio_row, m.slide_indexed, m.slide_total)

        self._set_percent(self.bm25_row, m.bm25_coverage)
        self._set_percent(self.text_row, m.text_coverage)
        self._set_percent(self.image_row, m.image_coverage)
        self._set_percent(self.fusion_row, m.fusion_full_coverage)

        self.fusion_note.setText(m.fusion_note)

        status_map = {
            "pending": m.doc_pending,
            "stale": m.doc_stale,
            "partial": m.doc_partial,
            "error": m.doc_error,
            "indexed": m.doc_indexed,
        }
        for code, btn in self.status_buttons.items():
            btn.setText(f"{btn.text().split(' ')[0]} {status_map.get(code, 0)}")

    def _set_ratio(self, row: _RatioRow, num: int, denom: int) -> None:
        pct = int((num / denom) * 100) if denom > 0 else 0
        row.bar.setValue(pct)
        row.value.setText(f"{pct}%")
        row.label.setText(f"{row.label.text().split('（')[0].strip()}（{num} / {denom}）")

    def _set_percent(self, row: _RatioRow, pct: float) -> None:
        value = max(0, min(100, int(pct * 100)))
        row.bar.setValue(value)
        row.value.setText(f"{value}%")

    def _format_int(self, value: int) -> str:
        return f"{value:,}"

    def _on_status_click(self) -> None:
        btn = self.sender()
        if not isinstance(btn, QPushButton):
            return
        status = btn.property("status_code")
        if hasattr(self.main_window, "library_tab"):
            self.main_window.tabs.setCurrentWidget(self.main_window.library_tab)
            self.main_window.library_tab.apply_status_filter(status)

    def _apply_coverage_filter(self, coverage: str) -> None:
        if hasattr(self.main_window, "library_tab"):
            self.main_window.tabs.setCurrentWidget(self.main_window.library_tab)
            self.main_window.library_tab.apply_coverage_filter(coverage)

    def _clear_filters(self) -> None:
        if hasattr(self.main_window, "library_tab"):
            self.main_window.tabs.setCurrentWidget(self.main_window.library_tab)
            self.main_window.library_tab.apply_status_filter(None)
            self.main_window.library_tab.apply_coverage_filter(None)
            self.main_window.library_tab.filter_edit.setText("")
