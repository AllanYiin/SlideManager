# -*- coding: utf-8 -*-

"""模型資產檢查與自動下載。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from app.core.logging import get_logger

log = get_logger(__name__)

RERANK_MODEL_NAME = "rerankTexure.onnx"
RERANK_MODEL_FILE_ID = "1Ha6eOT3L2bxd3fFCh_hRgDg3dku7iPmO"


@dataclass
class ModelDownloadProgress:
    stage: str
    current: int
    total: int
    message: str


def ensure_rerank_model(
    cache_dir: Path,
    *,
    on_progress: Optional[Callable[[ModelDownloadProgress], None]] = None,
) -> Path:
    """確認圖片模型存在，必要時下載。"""

    cache_dir.mkdir(parents=True, exist_ok=True)
    model_path = cache_dir / RERANK_MODEL_NAME

    if model_path.exists():
        log.info("圖片模型已存在：%s", model_path)
        _emit_progress(on_progress, "ready", 1, 1, "圖片模型已存在")
        return model_path

    _emit_progress(on_progress, "start", 0, 0, "準備下載圖片模型...")
    try:
        downloaded = _download_from_google_drive(
            RERANK_MODEL_FILE_ID,
            model_path,
            on_progress=on_progress,
        )
        log.info("圖片模型下載完成：%s", downloaded)
        _emit_progress(on_progress, "done", 1, 1, "圖片模型下載完成")
        return downloaded
    except Exception as exc:
        log.exception("圖片模型下載失敗：%s", exc)
        raise


def _download_from_google_drive(
    file_id: str,
    target_path: Path,
    *,
    on_progress: Optional[Callable[[ModelDownloadProgress], None]] = None,
) -> Path:
    url = "https://drive.google.com/uc"
    params = {"id": file_id, "export": "download"}
    session = requests.Session()

    response = _request_drive(session, url, params=params)

    if response.status_code != 200:
        raise RuntimeError(f"Google Drive 回應碼 {response.status_code}")

    token = _get_confirm_token(response)
    if not token and _is_html_response(response):
        token = _extract_confirm_token_from_html(response.text)

    if token:
        params["confirm"] = token
        response = _request_drive(session, url, params=params, error_message="Google Drive 確認下載請求失敗")
        if response.status_code != 200:
            raise RuntimeError(f"確認後下載仍失敗，回應碼 {response.status_code}")

    if _is_html_response(response):
        download_url = _extract_download_url_from_html(response.text)
        if download_url:
            response = _request_drive(session, download_url, error_message="Google Drive 轉向下載失敗")
        if _is_html_response(response):
            raise RuntimeError(
                "Google Drive 回傳警示頁面，請確認檔案分享權限，或手動下載後放到："
                f"{target_path}"
            )

    total = int(response.headers.get("Content-Length") or 0)
    downloaded = 0
    tmp_path = target_path.with_suffix(target_path.suffix + ".download")
    last_percent = -1

    try:
        with open(tmp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    percent = int(downloaded / total * 100)
                    if percent != last_percent:
                        last_percent = percent
                        _emit_progress(
                            on_progress,
                            "download",
                            downloaded,
                            total,
                            f"下載圖片模型中... {percent}%",
                        )
                else:
                    _emit_progress(on_progress, "download", downloaded, total, "下載圖片模型中...")
        tmp_path.replace(target_path)
        return target_path
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _get_confirm_token(response: requests.Response) -> Optional[str]:
    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            return value
    return None


def _extract_confirm_token_from_html(html: str) -> Optional[str]:
    match = re.search(r"confirm=([0-9A-Za-z_-]+)", html)
    if match:
        return match.group(1)
    return None


def _extract_download_url_from_html(html: str) -> Optional[str]:
    match = re.search(r'href="(https?://[^"]*export=download[^"]+)"', html)
    if match:
        return match.group(1).replace("&amp;", "&")
    match = re.search(r'href="(/[^"]*export=download[^"]+)"', html)
    if match:
        return f"https://drive.google.com{match.group(1).replace('&amp;', '&')}"
    return None


def _request_drive(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict] = None,
    error_message: str = "Google Drive 下載請求失敗",
) -> requests.Response:
    try:
        response = session.get(url, params=params, stream=True, timeout=30)
    except requests.RequestException as exc:
        log.exception("%s：%s", error_message, exc)
        raise RuntimeError(error_message) from exc
    return response


def _is_html_response(response: requests.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    return "text/html" in content_type


def _emit_progress(
    cb: Optional[Callable[[ModelDownloadProgress], None]],
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if not cb:
        return
    cb(ModelDownloadProgress(stage=stage, current=current, total=total, message=message))
