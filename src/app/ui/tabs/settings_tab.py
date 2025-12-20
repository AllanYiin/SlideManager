# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.logging import get_logger

log = get_logger(__name__)


class SettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ctx = None

        root = QVBoxLayout(self)

        gb_key = QGroupBox("OpenAI API Key")
        gl = QVBoxLayout(gb_key)

        row = QHBoxLayout()
        row.addWidget(QLabel("API Key："))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("以 sk-... 開頭")
        row.addWidget(self.api_key_edit)
        self.btn_save_key = QPushButton("儲存")
        row.addWidget(self.btn_save_key)
        gl.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_test = QPushButton("測試連線（Embeddings）")
        self.btn_refresh = QPushButton("重新載入專案服務")
        row2.addWidget(self.btn_test)
        row2.addWidget(self.btn_refresh)
        row2.addStretch(1)
        gl.addLayout(row2)

        root.addWidget(gb_key)

        gb_diag = QGroupBox("診斷資訊")
        dl = QVBoxLayout(gb_diag)
        self.diag = QTextEdit()
        self.diag.setReadOnly(True)
        dl.addWidget(self.diag)

        row3 = QHBoxLayout()
        self.btn_open_logs = QPushButton("開啟 logs 資料夾")
        row3.addWidget(self.btn_open_logs)
        row3.addStretch(1)
        dl.addLayout(row3)

        root.addWidget(gb_diag)
        root.addStretch(1)

        self.btn_save_key.clicked.connect(self.save_key)
        self.btn_test.clicked.connect(self.test_connection)
        self.btn_refresh.clicked.connect(self.refresh_services)
        self.btn_open_logs.clicked.connect(self.open_logs_folder)

    def set_context(self, ctx) -> None:
        self.ctx = ctx
        self._load_key()
        self.refresh_diagnostics()

    def _load_key(self) -> None:
        try:
            key = self.main_window.secrets.get_openai_api_key() or ""
            self.api_key_edit.setText(key)
        except Exception:
            self.api_key_edit.setText("")

    def save_key(self) -> None:
        key = (self.api_key_edit.text() or "").strip()
        try:
            self.main_window.secrets.set_openai_api_key(key)
            QMessageBox.information(self, "已儲存", "已儲存 API Key（本機加密保存）")
            self.refresh_services()
        except Exception as e:
            QMessageBox.critical(self, "儲存失敗", f"{e}")

    def refresh_services(self) -> None:
        if not self.ctx:
            return
        # 重新開啟目前專案以重建 services（載入新的 api_key）
        self.main_window.open_project(self.ctx.project_root)

    def test_connection(self) -> None:
        key = (self.api_key_edit.text() or "").strip()
        if not key:
            QMessageBox.information(self, "缺少 API Key", "請先輸入並儲存 API Key")
            return
        try:
            from app.services.openai_client import OpenAIClient

            c = OpenAIClient(key)
            vecs = c.embed_texts(["test"], model="text-embedding-3-large")
            if vecs and len(vecs[0]) > 0:
                QMessageBox.information(self, "測試成功", f"已取得向量維度：{len(vecs[0])}")
            else:
                QMessageBox.warning(self, "測試失敗", "未取得向量，請檢查 Key/網路")
        except Exception as e:
            QMessageBox.critical(self, "測試失敗", f"{e}")

    def refresh_diagnostics(self) -> None:
        if not self.ctx:
            self.diag.setText("尚未開啟專案")
            return
        idx = self.ctx.store.load_index()
        emb = idx.get("embedding", {})

        lines = []
        lines.append(f"專案路徑：{self.ctx.project_root}")
        lines.append(f"白名單目錄數：{len(self.ctx.catalog.get_whitelist_dirs())}")
        lines.append(f"已索引投影片：{len(idx.get('slides', []))}")
        lines.append("")
        lines.append("[Embedding]")
        lines.append(f"text_model：{emb.get('text_model')}")
        lines.append(f"text_dim：{emb.get('text_dim')}")
        lines.append(f"image_dim：{emb.get('image_dim')}")
        lines.append(f"text_source：{emb.get('text_source')}")
        lines.append(f"image_source：{emb.get('image_source')}")
        lines.append("")
        lines.append("[Renderer]")
        lines.append(f"LibreOffice soffice：{self.ctx.indexer.renderer.soffice_path() or '未偵測'}")
        lines.append(f"ONNX 啟用：{'是' if self.ctx.indexer.image_embedder.enabled_onnx() else '否（退化 hash）'}")
        lines.append("")
        lines.append("提示：若未設定 API Key，向量搜尋仍可用，但品質較差（fallback_hash）。")

        self.diag.setText("\n".join(lines))

    def open_logs_folder(self) -> None:
        try:
            p = Path.cwd() / "logs"
            p.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            QMessageBox.critical(self, "開啟失敗", f"{e}")
