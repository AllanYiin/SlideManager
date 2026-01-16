from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from tests.helpers import ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.config import JobOptions
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import JobManager


class TestJobManagerPlanning(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "index.sqlite"
        self.schema_sql = load_schema_sql(ROOT)
        self.bus = EventBus()
        self.mgr = JobManager(self.db_path, self.schema_sql, self.bus)

    def tearDown(self) -> None:
        self.mgr.conn.close()
        self.temp_dir.cleanup()

    def test_upsert_file_insert_and_update(self) -> None:
        options = JobOptions()
        self.mgr._insert_job("job1", str(self.root), options)
        file_id = self.mgr._upsert_file("/tmp/demo.pptx", 100, 1, "4:3")
        self.assertGreater(file_id, 0)

        same_id = self.mgr._upsert_file("/tmp/demo.pptx", 200, 2, "16:9")
        self.assertEqual(file_id, same_id)

        row = self.mgr.conn.execute(
            "SELECT size_bytes, mtime_epoch, slide_aspect FROM files WHERE file_id=?",
            (file_id,),
        ).fetchone()
        self.assertEqual(int(row["size_bytes"]), 200)
        self.assertEqual(int(row["mtime_epoch"]), 2)
        self.assertEqual(row["slide_aspect"], "16:9")

    def test_ensure_pages_rows_and_artifacts(self) -> None:
        options = JobOptions()
        self.mgr._insert_job("job1", str(self.root), options)
        file_id = self.mgr._upsert_file("/tmp/demo.pptx", 100, 1, "4:3")
        page_ids = self.mgr._ensure_pages_rows(file_id, 3, "4:3", 100, 1)
        self.assertEqual(len(page_ids), 3)

        artifacts = self.mgr.conn.execute(
            "SELECT kind, status FROM artifacts WHERE page_id=? ORDER BY kind",
            (page_ids[0],),
        ).fetchall()
        kinds = [r["kind"] for r in artifacts]
        self.assertEqual(kinds, ["bm25", "img_vec", "text", "text_vec", "thumb"])
        for r in artifacts:
            self.assertEqual(r["status"], "missing")


if __name__ == "__main__":
    unittest.main()
