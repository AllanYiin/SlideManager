from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path

from tests.helpers import ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.planner import scan_files_under

pytestmark = pytest.mark.unit


class TestPlanner(unittest.TestCase):
    def test_scan_files_under_is_not_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "top.pptx").write_bytes(b"demo")
            sub = root / "nested"
            sub.mkdir()
            (sub / "nested.pptx").write_bytes(b"demo")

            out = scan_files_under(root)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].path.endswith("top.pptx"))


if __name__ == "__main__":
    unittest.main()
