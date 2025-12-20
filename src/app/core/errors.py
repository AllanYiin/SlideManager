# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    RENDERER_ERROR = "RENDERER_ERROR"
    OPENAI_ERROR = "OPENAI_ERROR"
    JSON_ERROR = "JSON_ERROR"
    ONNX_ERROR = "ONNX_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    MTIME_CHANGED = "MTIME_CHANGED"
    EMPTY_TEXT = "EMPTY_TEXT"


@dataclass(frozen=True)
class ErrorInfo:
    message: str
    hint: str
    retryable: bool = True


_ERRORS: dict[ErrorCode, ErrorInfo] = {
    ErrorCode.RENDERER_ERROR: ErrorInfo(
        message="縮圖渲染失敗，已改用純文字索引。",
        hint="請確認已安裝 LibreOffice 或 Windows PowerPoint，並重試索引。",
    ),
    ErrorCode.OPENAI_ERROR: ErrorInfo(
        message="OpenAI 服務連線失敗或金鑰設定有誤。",
        hint="請檢查網路與 API Key，稍後再重試。",
    ),
    ErrorCode.JSON_ERROR: ErrorInfo(
        message="專案檔案讀寫失敗。",
        hint="請確認專案資料夾權限或磁碟空間是否足夠。",
    ),
    ErrorCode.ONNX_ERROR: ErrorInfo(
        message="圖片模型載入失敗，已降級為純文字向量。",
        hint="請重新下載 ONNX 模型或檢查模型檔案是否損毀。",
    ),
    ErrorCode.PERMISSION_DENIED: ErrorInfo(
        message="權限不足，無法存取指定路徑。",
        hint="請調整資料夾權限後再重試。",
    ),
    ErrorCode.PATH_NOT_FOUND: ErrorInfo(
        message="找不到指定路徑。",
        hint="請確認路徑是否已被移除或重新掛載。",
    ),
    ErrorCode.MTIME_CHANGED: ErrorInfo(
        message="索引期間檔案已被修改。",
        hint="請重新掃描並重試索引。",
    ),
    ErrorCode.EMPTY_TEXT: ErrorInfo(
        message="投影片文字為空，已改用降級向量。",
        hint="可確認投影片內容是否有可讀文字。",
        retryable=False,
    ),
}


def get_error_info(code: ErrorCode) -> ErrorInfo:
    return _ERRORS.get(
        code,
        ErrorInfo(message="發生未知錯誤。", hint="請稍後重試或查看 logs/app.log。"),
    )


def format_user_message(code: ErrorCode | str, detail: Optional[str] = None) -> str:
    if isinstance(code, str):
        try:
            code = ErrorCode(code)
        except Exception:
            code = ErrorCode.JSON_ERROR
    info = get_error_info(code)
    parts = [info.message, info.hint, f"錯誤代碼：{code.value}"]
    if detail:
        parts.append(f"詳細資訊：{detail}")
    return "\n".join(parts)
