from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tests.helpers import build_pdf, ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.thumb_render import render_pdf_page_to_thumb, thumb_size


class TestThumbRender(unittest.TestCase):
    def test_thumb_size_variants(self) -> None:
        self.assertEqual(thumb_size("4:3", 320, 240, 180), (320, 240))
        self.assertEqual(thumb_size("16:9", 320, 240, 180), (320, 180))
        self.assertEqual(thumb_size("unknown", 320, 240, 180), (320, 180))

    def test_render_pdf_page_to_thumb(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdf_path = Path(td) / "demo.pdf"
            build_pdf(pdf_path, pages=1, width=400, height=300)
            out_img = Path(td) / "thumb.jpg"

            render_pdf_page_to_thumb(pdf_path, 0, out_img, 320, 240)
            self.assertTrue(out_img.exists())

            with Image.open(out_img) as img:
                self.assertEqual(img.size, (320, 240))


if __name__ == "__main__":
    unittest.main()
