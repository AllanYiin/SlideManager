# -*- coding: utf-8 -*-

import sys
import importlib.util
import unittest
from pathlib import Path

if importlib.util.find_spec("numpy") is None:
    raise unittest.SkipTest("numpy 未安裝，跳過 vectors 測試。")

import numpy as np

# 讓 unittest 在任何工作目錄下都能找到 src/app
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.utils.vectors import b64_f32_to_vec, stable_hash_to_vec, vec_to_b64_f32


class TestVectors(unittest.TestCase):
    def test_roundtrip_b64_f32(self):
        v = np.arange(10, dtype=np.float32)
        b = vec_to_b64_f32(v)
        v2 = b64_f32_to_vec(b, 10)
        self.assertEqual(v2.size, 10)
        self.assertTrue(np.allclose(v, v2))

    def test_stable_hash(self):
        a = stable_hash_to_vec("hello", 32)
        b = stable_hash_to_vec("hello", 32)
        self.assertTrue(np.allclose(a, b))
        self.assertEqual(a.size, 32)


if __name__ == "__main__":
    unittest.main()
