from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from tests.helpers import build_pptx, build_slide_xml, ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.text_extract import (
    extract_page_text,
    extract_text_from_slide_xml,
    fast_text_sig,
    normalize_text,
)


class TestTextExtract(unittest.TestCase):
    def test_extract_text_from_slide_xml(self) -> None:
        xml = build_slide_xml(["Hello", "World"])
        out = extract_text_from_slide_xml(xml.encode("utf-8"))
        self.assertEqual(out, "Hello\nWorld")

    def test_normalize_text_removes_zero_width_and_whitespace(self) -> None:
        raw = "A\u200b  B\r\n\n C  \n"
        norm = normalize_text(raw)
        self.assertEqual(norm, "A B\nC")

    def test_empty_text_returns_empty_sig(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pptx_path = Path(td) / "empty.pptx"
            build_pptx(pptx_path, [build_slide_xml([])])
            _raw, norm, sig = extract_page_text(str(pptx_path), 1)
            self.assertEqual(norm, "")
            self.assertEqual(sig, "")

    def test_text_sig_stable(self) -> None:
        norm = "Alpha\nBeta"
        sig1 = fast_text_sig(norm)
        sig2 = fast_text_sig(norm)
        sig3 = fast_text_sig("Alpha\nBeta!")
        self.assertEqual(sig1, sig2)
        self.assertNotEqual(sig1, sig3)


if __name__ == "__main__":
    unittest.main()
