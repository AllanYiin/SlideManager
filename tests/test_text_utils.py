# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

# 讓 unittest 在任何工作目錄下都能找到 src/app
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.utils.text import tokenize


class TestTextUtils(unittest.TestCase):
    def test_tokenize_empty(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("   "), [])

    def test_tokenize_mixed_text(self):
        text = "Hello 123 世界"
        tokens = tokenize(text)
        self.assertEqual(tokens, ["hello", "123", "世", "界", "世界"])

    def test_tokenize_cjk_bigram(self):
        tokens = tokenize("測試中文")
        self.assertEqual(tokens, ["測", "試", "中", "文", "測試", "試中", "中文"])


if __name__ == "__main__":
    unittest.main()
