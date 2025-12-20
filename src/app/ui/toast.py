# -*- coding: utf-8 -*-

from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, *, level: str = "info", timeout_ms: int = 8000) -> None:
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("Toast")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

        if level == "error":
            bg = "#FEE2E2"
            fg = "#7F1D1D"
            border = "#FCA5A5"
        elif level == "warning":
            bg = "#FEF3C7"
            fg = "#78350F"
            border = "#FCD34D"
        else:
            bg = "#E0F2FE"
            fg = "#0C4A6E"
            border = "#7DD3FC"

        self.setStyleSheet(
            f"#Toast {{ background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 8px; }}"
        )

        QTimer.singleShot(timeout_ms, self.close)

    def show_toast(self) -> None:
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            origin = parent.mapToGlobal(QPoint(0, 0))
            x = origin.x() + parent.width() - self.width() - 20
            y = origin.y() + parent.height() - self.height() - 20
            self.move(x, y)
        self.show()
        self.raise_()
