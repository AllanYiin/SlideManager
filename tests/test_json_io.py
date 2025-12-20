# -*- coding: utf-8 -*-

import json
import sys
import tempfile
import unittest
from pathlib import Path

# 讓 unittest 在任何工作目錄下都能找到 src/app
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.utils.json_io import atomic_write_json, read_json


class TestJsonIo(unittest.TestCase):
    def test_atomic_write_creates_file_and_bak(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.json"
            atomic_write_json(path, {"a": 1})
            self.assertTrue(path.exists())

            atomic_write_json(path, {"a": 2}, keep_bak=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["a"], 2)

            bak_files = list(Path(td).glob("sample.json.*.bak"))
            self.assertTrue(bak_files)

    def test_read_json_default_on_missing_or_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.json"
            self.assertEqual(read_json(path, {"fallback": True}), {"fallback": True})

            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(read_json(path, {"fallback": True}), {"fallback": True})


if __name__ == "__main__":
    unittest.main()
