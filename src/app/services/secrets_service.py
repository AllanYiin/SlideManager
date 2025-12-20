# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.core.paths import secrets_path, app_home_dir

log = get_logger(__name__)


@dataclass
class Secrets:
    schema_version: str = "1.0"
    openai_api_key_enc: str | None = None


class SecretsService:
    """本地加密保存敏感資訊。

    說明：此為「本地加密」而非硬體安全模組；在不引入額外安裝/登入流程下，
    以可用性為優先，至少避免明文落盤。
    """

    def __init__(self):
        self._path = secrets_path()
        self._key_path = app_home_dir() / "secrets.key"

    def _get_fernet(self):
        from cryptography.fernet import Fernet

        if not self._key_path.exists():
            self._key_path.write_bytes(Fernet.generate_key())
        key = self._key_path.read_bytes()
        return Fernet(key)

    def load(self) -> Secrets:
        if not self._path.exists():
            return Secrets()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return Secrets(
                schema_version=str(data.get("schema_version", "1.0")),
                openai_api_key_enc=data.get("openai_api_key_enc"),
            )
        except Exception as e:
            log.error("讀取 secrets.json 失敗：%s", e)
            return Secrets()

    def save(self, s: Secrets) -> None:
        try:
            self._path.write_text(
                json.dumps(
                    {
                        "schema_version": s.schema_version,
                        "openai_api_key_enc": s.openai_api_key_enc,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            log.error("寫入 secrets.json 失敗：%s", e)

    def set_openai_api_key(self, api_key: str) -> None:
        api_key = (api_key or "").strip()
        if not api_key:
            s = self.load()
            s.openai_api_key_enc = None
            self.save(s)
            return

        f = self._get_fernet()
        token = f.encrypt(api_key.encode("utf-8"))
        s = self.load()
        s.openai_api_key_enc = token.decode("ascii")
        self.save(s)

    def get_openai_api_key(self) -> Optional[str]:
        s = self.load()
        if not s.openai_api_key_enc:
            return None
        try:
            f = self._get_fernet()
            raw = f.decrypt(s.openai_api_key_enc.encode("ascii"))
            return raw.decode("utf-8")
        except Exception as e:
            log.error("解密 OpenAI API Key 失敗：%s", e)
            return None
