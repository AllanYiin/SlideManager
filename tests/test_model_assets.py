# -*- coding: utf-8 -*-

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable, List, Optional
from unittest.mock import patch

try:
    from requests.cookies import RequestsCookieJar
except ImportError:
    RequestsCookieJar = None

# 讓 unittest 在任何工作目錄下都能找到 src/app
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.services import model_assets


@unittest.skipUnless(RequestsCookieJar, "requests 尚未安裝")
class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: Optional[dict] = None,
        cookies: Optional[RequestsCookieJar] = None,
        text: str = "",
        content_chunks: Optional[Iterable[bytes]] = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.cookies = cookies or RequestsCookieJar()
        self.text = text
        self._content_chunks = list(content_chunks or [])

    def iter_content(self, chunk_size: int = 1024) -> Iterable[bytes]:
        for chunk in self._content_chunks:
            yield chunk


@unittest.skipUnless(RequestsCookieJar, "requests 尚未安裝")
class TestDownloadFromGoogleDrive(unittest.TestCase):
    def test_ensure_rerank_model_calls_download_when_missing(self) -> None:
        response = FakeResponse(
            headers={"Content-Length": "3", "Content-Type": "application/octet-stream"},
            content_chunks=[b"abc"],
        )
        with tempfile.TemporaryDirectory() as td, patch.object(
            model_assets, "_request_drive", return_value=response
        ) as request_drive:
            cache_dir = Path(td)
            result = model_assets.ensure_rerank_model(cache_dir)

        self.assertTrue(result.exists())
        self.assertEqual(result.read_bytes(), b"abc")
        self.assertTrue(request_drive.called)

    def test_downloads_and_reports_progress(self) -> None:
        chunks = [b"abc", b"defg"]
        total = sum(len(chunk) for chunk in chunks)
        response = FakeResponse(
            headers={"Content-Length": str(total), "Content-Type": "application/octet-stream"},
            content_chunks=chunks,
        )
        progress: List[model_assets.ModelDownloadProgress] = []

        with tempfile.TemporaryDirectory() as td, patch.object(
            model_assets, "_request_drive", return_value=response
        ):
            target = Path(td) / "model.onnx"
            result = model_assets._download_from_google_drive("file-id", target, on_progress=progress.append)

        self.assertEqual(result, target)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"abcdefg")
        self.assertTrue(any(p.stage == "download" for p in progress))

    def test_downloads_after_confirm_token(self) -> None:
        cookies = RequestsCookieJar()
        cookies.set("download_warning_123", "token123")
        first_response = FakeResponse(
            headers={"Content-Type": "text/html"},
            cookies=cookies,
            text="<html>warning</html>",
        )
        chunks = [b"ok"]
        second_response = FakeResponse(
            headers={"Content-Length": "2", "Content-Type": "application/octet-stream"},
            content_chunks=chunks,
        )

        with tempfile.TemporaryDirectory() as td, patch.object(
            model_assets, "_request_drive", side_effect=[first_response, second_response]
        ) as request_drive:
            target = Path(td) / "model.onnx"
            result = model_assets._download_from_google_drive("file-id", target)

        self.assertEqual(result, target)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"ok")
        confirm_calls = [
            call
            for call in request_drive.call_args_list
            if call.kwargs.get("params", {}).get("confirm") == "token123"
        ]
        self.assertTrue(confirm_calls)

    def test_downloads_from_html_download_url(self) -> None:
        html = (
            "<a href=\"https://drive.google.com/uc?export=download&id=abc\">download</a>"
        )
        first_response = FakeResponse(
            headers={"Content-Type": "text/html"},
            text=html,
        )
        chunks = [b"data"]
        second_response = FakeResponse(
            headers={"Content-Length": "4", "Content-Type": "application/octet-stream"},
            content_chunks=chunks,
        )

        with tempfile.TemporaryDirectory() as td, patch.object(
            model_assets, "_request_drive", side_effect=[first_response, second_response]
        ) as request_drive:
            target = Path(td) / "model.onnx"
            result = model_assets._download_from_google_drive("file-id", target)

        self.assertEqual(result, target)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"data")
        called_urls = [call.args[1] for call in request_drive.call_args_list]
        self.assertIn("https://drive.google.com/uc?export=download&id=abc", called_urls)


if __name__ == "__main__":
    unittest.main()
