from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.pdf_convert import convert_pptx_to_pdf_libreoffice


class TestPdfConvert(unittest.TestCase):
    def test_timeout_kills_process(self) -> None:
        proc = MagicMock()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="soffice", timeout=1)
        proc.pid = 1234

        with tempfile.TemporaryDirectory() as td, patch(
            "app.backend_daemon.pdf_convert.subprocess.Popen", return_value=proc
        ), patch("app.backend_daemon.pdf_convert.is_windows", return_value=True), patch(
            "app.backend_daemon.pdf_convert.kill_process_tree_windows"
        ) as killer:
            with self.assertRaises(RuntimeError):
                convert_pptx_to_pdf_libreoffice(
                    Path(td) / "demo.pptx", Path(td) / "out.pdf", None, 1
                )
            killer.assert_called_once_with(proc.pid)

    def test_nonzero_returncode_raises(self) -> None:
        proc = MagicMock()
        proc.communicate.return_value = ("", "fail")
        proc.returncode = 1
        with tempfile.TemporaryDirectory() as td, patch(
            "app.backend_daemon.pdf_convert.subprocess.Popen", return_value=proc
        ), patch("app.backend_daemon.pdf_convert.is_windows", return_value=False):
            with self.assertRaises(RuntimeError):
                convert_pptx_to_pdf_libreoffice(
                    Path(td) / "demo.pptx", Path(td) / "out.pdf", None, 1
                )

    def test_missing_pdf_raises(self) -> None:
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        with tempfile.TemporaryDirectory() as td, patch(
            "app.backend_daemon.pdf_convert.subprocess.Popen", return_value=proc
        ), patch("app.backend_daemon.pdf_convert.is_windows", return_value=False):
            with self.assertRaises(RuntimeError):
                convert_pptx_to_pdf_libreoffice(
                    Path(td) / "demo.pptx", Path(td) / "out.pdf", None, 1
                )

    def test_rename_output_pdf(self) -> None:
        proc = MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        with tempfile.TemporaryDirectory() as td, patch(
            "app.backend_daemon.pdf_convert.subprocess.Popen", return_value=proc
        ), patch("app.backend_daemon.pdf_convert.is_windows", return_value=False):
            root = Path(td)
            expected = root / "demo.pdf"
            expected.write_bytes(b"pdf")
            out_pdf = root / "out.pdf"
            convert_pptx_to_pdf_libreoffice(root / "demo.pptx", out_pdf, None, 1)
            self.assertTrue(out_pdf.exists())
            self.assertFalse(expected.exists())


if __name__ == "__main__":
    unittest.main()
