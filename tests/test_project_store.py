# -*- coding: utf-8 -*-

import sys
import tempfile
import unittest
from pathlib import Path

# 讓 unittest 在任何工作目錄下都能找到 src/app
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.services.project_store import ProjectStore


class TestProjectStore(unittest.TestCase):
    def test_init_and_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = ProjectStore(root)
            proj = s.load_project()
            self.assertIn("whitelist_dirs", proj)
            cat = s.load_catalog()
            self.assertIn("files", cat)
            idx = s.load_index()
            self.assertIn("slides", idx)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = ProjectStore(root)
            proj = s.load_project()
            proj["whitelist_dirs"] = ["C:/tmp"]
            s.save_project(proj)
            proj2 = s.load_project()
            self.assertEqual(proj2["whitelist_dirs"], ["C:/tmp"])


if __name__ == "__main__":
    unittest.main()
