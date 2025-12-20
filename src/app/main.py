# -*- coding: utf-8 -*-

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def run() -> int:
    app = QApplication([])
    w = MainWindow()
    w.show()
    return app.exec()
