from __future__ import annotations

import tempfile
import unittest

from pathlib import Path

from tests.helpers import ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.db import init_schema, open_db


class TestDb(unittest.TestCase):
    def test_open_db_pragmas(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "index.sqlite"
            conn = open_db(db_path)
            init_schema(conn, load_schema_sql(ROOT))
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(fk, 1)
            self.assertEqual(str(journal).lower(), "wal")
            self.assertEqual(timeout, 5000)
            conn.close()


if __name__ == "__main__":
    unittest.main()
