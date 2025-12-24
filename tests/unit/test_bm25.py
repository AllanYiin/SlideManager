from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.bm25 import upsert_fts_page
from app.backend_daemon.db import init_schema, open_db


class TestBm25(unittest.TestCase):
    def test_upsert_fts_page(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = open_db(Path(td) / "index.sqlite")
            init_schema(conn, load_schema_sql(ROOT))

            upsert_fts_page(conn, 1, "hello")
            row = conn.execute(
                "SELECT norm_text FROM fts_pages WHERE page_id=1"
            ).fetchone()
            self.assertEqual(row[0], "hello")

            upsert_fts_page(conn, 1, "bye")
            row = conn.execute(
                "SELECT norm_text FROM fts_pages WHERE page_id=1"
            ).fetchone()
            self.assertEqual(row[0], "bye")
            conn.close()

    def test_upsert_empty_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = open_db(Path(td) / "index.sqlite")
            init_schema(conn, load_schema_sql(ROOT))
            upsert_fts_page(conn, 2, "")
            row = conn.execute(
                "SELECT norm_text FROM fts_pages WHERE page_id=2"
            ).fetchone()
            self.assertEqual(row[0], "")
            conn.close()


if __name__ == "__main__":
    unittest.main()
