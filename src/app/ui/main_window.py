# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QTabWidget,
    QToolBar,
)

from app.core.logging import get_logger
from app.core.settings import AppSettings, load_settings, save_settings
from app.services.catalog_service import CatalogService
from app.services.index_service import IndexService
from app.services.project_store import ProjectStore
from app.services.search_service import SearchService
from app.services.secrets_service import SecretsService
from app.ui.async_worker import Worker
from app.ui.tabs.chat_tab import ChatTab
from app.ui.tabs.dashboard_tab import DashboardTab
from app.ui.tabs.library_tab import LibraryTab
from app.ui.tabs.page_status_tab import PageStatusTab
from app.ui.tabs.search_tab import SearchTab
from app.ui.tabs.settings_tab import SettingsTab
from app.ui.toast import Toast

log = get_logger(__name__)


@dataclass
class AppContext:
    project_root: Path
    store: ProjectStore
    secrets: SecretsService
    api_key: Optional[str]
    catalog: CatalogService
    indexer: IndexService
    search: SearchService


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("個人投影片管理")

        self.thread_pool = QThreadPool.globalInstance()
        self.settings: AppSettings = load_settings()
        self.secrets = SecretsService()

        self.ctx: Optional[AppContext] = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.library_tab = LibraryTab(self)
        self.dashboard_tab = DashboardTab(self)
        self.search_tab = SearchTab(self)
        self.page_status_tab = PageStatusTab(self)
        self.chat_tab = ChatTab(self)
        self.settings_tab = SettingsTab(self)

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.library_tab, "檔案庫/索引")
        self.tabs.addTab(self.search_tab, "搜尋")
        self.tabs.addTab(self.page_status_tab, "頁面狀態")
        self.tabs.addTab(self.chat_tab, "對話")
        self.tabs.addTab(self.settings_tab, "設定/診斷")

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.model_download_prog = QProgressBar()
        self.model_download_prog.setRange(0, 100)
        self.model_download_prog.setValue(0)
        self.model_download_prog.setVisible(False)
        self.model_download_prog.setMaximumWidth(180)
        self.status.addPermanentWidget(self.model_download_prog)

        self._build_menu()
        self._restore_window_state()

        # 嘗試自動開啟上次專案
        if self.settings.last_project_dir:
            pr = Path(self.settings.last_project_dir)
            if pr.exists():
                self.open_project(pr)

        if self.ctx is None:
            self.status.showMessage("請先開啟或建立專案資料夾")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    # -------- UI chrome --------
    def _build_menu(self) -> None:
        tb = QToolBar("主工具列")
        self.addToolBar(tb)

        act_open = QAction("開啟/建立專案", self)
        act_open.triggered.connect(self.action_open_project)
        tb.addAction(act_open)

        act_scan = QAction("掃描檔案", self)
        act_scan.triggered.connect(self.action_scan)
        tb.addAction(act_scan)

        act_index = QAction("開始索引", self)
        act_index.triggered.connect(self.action_index_needed)
        tb.addAction(act_index)

    def _restore_window_state(self) -> None:
        # geometry
        if self.settings.window_geometry_b64:
            try:
                raw = base64.b64decode(self.settings.window_geometry_b64.encode("ascii"))
                self.restoreGeometry(raw)
            except Exception:
                log.exception("還原視窗狀態失敗")
        self.tabs.setCurrentIndex(int(self.settings.last_tab_index or 0))

    def closeEvent(self, event):
        try:
            self.settings.last_tab_index = int(self.tabs.currentIndex())
            self.settings.window_geometry_b64 = base64.b64encode(self.saveGeometry()).decode("ascii")
            if self.ctx:
                self.settings.last_project_dir = str(self.ctx.project_root)
            save_settings(self.settings)
        except Exception:
            log.exception("儲存視窗狀態失敗")
        super().closeEvent(event)

    def _on_tab_changed(self, idx: int) -> None:
        self.settings.last_tab_index = int(idx)
        if self.tabs.widget(idx) is self.settings_tab and self.ctx:
            self.settings_tab.refresh_whitelist()
            self.settings_tab.refresh_diagnostics()
        if self.tabs.widget(idx) is self.dashboard_tab and self.ctx:
            self.dashboard_tab.refresh_metrics()
        if self.tabs.widget(idx) is self.page_status_tab and self.ctx:
            self.page_status_tab.refresh_data()

    # -------- Actions --------
    def action_open_project(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "選擇專案資料夾")
        if not d:
            return
        self.open_project(Path(d))

    def open_project(self, project_root: Path) -> None:
        try:
            project_root = project_root.resolve()
            project_root.mkdir(parents=True, exist_ok=True)

            store = ProjectStore(project_root)
            api_key = self.secrets.get_openai_api_key()

            catalog = CatalogService(store)
            indexer = IndexService(store, catalog, api_key)
            search = SearchService(store, api_key)

            self.ctx = AppContext(
                project_root=project_root,
                store=store,
                secrets=self.secrets,
                api_key=api_key,
                catalog=catalog,
                indexer=indexer,
                search=search,
            )

            # 通知各 tab
            self.library_tab.set_context(self.ctx)
            self.dashboard_tab.set_context(self.ctx)
            self.search_tab.set_context(self.ctx)
            self.page_status_tab.set_context(self.ctx)
            self.chat_tab.set_context(self.ctx)
            self.settings_tab.set_context(self.ctx)

            self.status.showMessage(f"已開啟專案：{project_root}")
            self.settings.last_project_dir = str(project_root)
            save_settings(self.settings)
            self._disable_image_model_ui()
        except Exception as e:
            log.exception("開啟專案失敗：%s", e)
            QMessageBox.critical(self, "開啟專案失敗", f"發生錯誤：{e}")

    def action_scan(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return
        self.library_tab.scan_files()

    def action_index_needed(self) -> None:
        if not self.ctx:
            QMessageBox.information(self, "尚未開啟專案", "請先開啟或建立專案資料夾")
            return
        self.library_tab.start_index_needed()

    def show_toast(self, message: str, *, level: str = "info", timeout_ms: int = 8000) -> None:
        toast = Toast(self, message, level=level, timeout_ms=timeout_ms)
        toast.show_toast()

    def _disable_image_model_ui(self) -> None:
        self.model_download_prog.setVisible(False)
        self.status.showMessage("圖片模型下載已移除（後台改為 daemon）", 5000)
