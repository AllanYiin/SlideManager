from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from tests.helpers import build_pptx, build_slide_xml, ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.pptx_meta import detect_aspect_from_pptx


class TestPptxMeta(unittest.TestCase):
    def test_detect_4_3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pptx_path = Path(td) / "demo.pptx"
            build_pptx(pptx_path, [build_slide_xml(["A"])], aspect="4:3")
            self.assertEqual(detect_aspect_from_pptx(str(pptx_path)), "4:3")

    def test_detect_16_9(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pptx_path = Path(td) / "demo.pptx"
            build_pptx(pptx_path, [build_slide_xml(["A"])], aspect="16:9")
            self.assertEqual(detect_aspect_from_pptx(str(pptx_path)), "16:9")

    def test_missing_sldsz_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pptx_path = Path(td) / "demo.pptx"
            build_pptx(
                pptx_path,
                [build_slide_xml(["A"])],
                aspect="unknown",
            )
            self.assertEqual(detect_aspect_from_pptx(str(pptx_path)), "unknown")

    def test_missing_presentation_xml_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pptx_path = Path(td) / "demo.pptx"
            build_pptx(
                pptx_path,
                [build_slide_xml(["A"])],
                include_presentation=False,
            )
            self.assertEqual(detect_aspect_from_pptx(str(pptx_path)), "unknown")


if __name__ == "__main__":
    unittest.main()
