from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.helpers import ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.config import JobOptions
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import CancelToken, JobManager, PauseToken


class TestJobManagerEmbeddings(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "index.sqlite"
        self.schema_sql = load_schema_sql(ROOT)
        self.bus = EventBus()
        self.mgr = JobManager(self.db_path, self.schema_sql, self.bus)

    async def asyncTearDown(self) -> None:
        self.mgr.conn.close()
        self.temp_dir.cleanup()

    def _seed_page(self, page_id: int, sig: str, norm_text: str) -> None:
        self.mgr.conn.execute(
            "INSERT INTO page_text(page_id,raw_text,norm_text,text_sig,updated_at) VALUES (?,?,?,?,?)",
            (page_id, norm_text, norm_text, sig, 1),
        )
        self.mgr.conn.execute(
            "INSERT INTO artifacts(page_id,kind,status,updated_at) VALUES (?,?,?,?)",
            (page_id, "text_vec", "queued", 1),
        )
        self.mgr.conn.execute(
            "INSERT INTO tasks(job_id,page_id,kind,status,priority) VALUES (?,?,?,?,?)",
            ("job1", page_id, "text_vec", "queued", 0),
        )

    async def test_empty_text_uses_zero_vector(self) -> None:
        options = JobOptions(
            enable_text_vec=True,
            enable_text=False,
            enable_thumb=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        self.mgr._insert_job("job1", str(self.root), options)
        self.mgr.conn.execute(
            "INSERT INTO files(path,size_bytes,mtime_epoch,slide_aspect,last_scanned_at) VALUES (?,?,?,?,?)",
            ("/tmp/demo.pptx", 1, 1, "4:3", 1),
        )
        self.mgr.conn.execute(
            "INSERT INTO pages(page_id,file_id,page_no,aspect,source_size_bytes,source_mtime_epoch,created_at) VALUES (?,?,?,?,?,?,?)",
            (1, 1, 1, "4:3", 1, 1, 1),
        )
        self._seed_page(1, "", "")
        self.mgr.conn.commit()

        with patch(
            "app.backend_daemon.job_manager.embed_text_batch_openai", new=AsyncMock()
        ) as embed_mock:
            await self.mgr._run_text_embeddings("job1", options, CancelToken(), PauseToken())
            embed_mock.assert_not_called()

        row = self.mgr.conn.execute(
            "SELECT status FROM artifacts WHERE page_id=1 AND kind='text_vec'"
        ).fetchone()
        self.assertEqual(row["status"], "ready")

    async def test_cache_hit_skips_openai(self) -> None:
        options = JobOptions(
            enable_text_vec=True,
            enable_text=False,
            enable_thumb=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        self.mgr._insert_job("job1", str(self.root), options)
        self.mgr.conn.execute(
            "INSERT INTO files(path,size_bytes,mtime_epoch,slide_aspect,last_scanned_at) VALUES (?,?,?,?,?)",
            ("/tmp/demo.pptx", 1, 1, "4:3", 1),
        )
        self.mgr.conn.execute(
            "INSERT INTO pages(page_id,file_id,page_no,aspect,source_size_bytes,source_mtime_epoch,created_at) VALUES (?,?,?,?,?,?,?)",
            (1, 1, 1, "4:3", 1, 1, 1),
        )
        self._seed_page(1, "sig1", "hello")
        self.mgr.conn.execute(
            "INSERT INTO embedding_cache_text(model,text_sig,dim,vector_blob,created_at) VALUES (?,?,?,?,?)",
            (options.embed.model_text, "sig1", 1, b"\x00\x00\x00\x00", 1),
        )
        self.mgr.conn.commit()

        with patch(
            "app.backend_daemon.job_manager.embed_text_batch_openai", new=AsyncMock()
        ) as embed_mock:
            await self.mgr._run_text_embeddings("job1", options, CancelToken(), PauseToken())
            embed_mock.assert_not_called()

        row = self.mgr.conn.execute(
            "SELECT status FROM artifacts WHERE page_id=1 AND kind='text_vec'"
        ).fetchone()
        self.assertEqual(row["status"], "ready")


if __name__ == "__main__":
    unittest.main()
