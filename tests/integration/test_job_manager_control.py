from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


from tests.helpers import build_pptx, build_slide_xml, ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.config import JobOptions
from app.backend_daemon.db import now_epoch
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import JobManager


class TestJobManagerControl(unittest.IsolatedAsyncioTestCase):
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

    async def test_cancel_job_finalizes(self) -> None:
        pptx_path = self.root / "demo.pptx"
        slides = [build_slide_xml([str(i)]) for i in range(10)]
        build_pptx(pptx_path, slides, aspect="4:3")
        options = JobOptions(
            enable_text=True,
            enable_bm25=False,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            commit_every_pages=1,
        )

        async def slow_extract(pptx: str, page_no: int):
            time.sleep(0.1)
            from app.backend_daemon.text_extract import extract_page_text

            return extract_page_text(pptx, page_no)

        with patch("app.backend_daemon.job_manager.extract_page_text", new=slow_extract):
            job_id = await self.mgr.create_job(str(self.root), options)
            q = await self.bus.subscribe(job_id)
            await asyncio.wait_for(q.get(), timeout=2)
            await self.mgr.cancel_job(job_id)

            cancelled = False
            for _ in range(20):
                row = self.mgr.conn.execute(
                    "SELECT status FROM jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if row and row["status"] == "cancelled":
                    cancelled = True
                    break
                await asyncio.sleep(0.1)

        self.assertTrue(cancelled)
        tasks = self.mgr.conn.execute(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE job_id=? AND status IN ('queued','running')",
            (job_id,),
        ).fetchone()
        self.assertEqual(tasks["cnt"], 0)

    async def test_watchdog_marks_timeout(self) -> None:
        job_id = "job1"
        self.mgr.conn.execute(
            "INSERT INTO jobs(job_id,library_root,created_at,status,options_json) VALUES (?,?,?,?,?)",
            (job_id, str(self.root), now_epoch(), "running", "{}"),
        )
        self.mgr.conn.execute(
            "INSERT INTO tasks(job_id,kind,status,started_at,heartbeat_at) VALUES (?,?,?,?,?)",
            (job_id, "text", "running", now_epoch() - 1000, now_epoch() - 1000),
        )
        self.mgr.conn.commit()

        calls: list[int] = []

        async def fast_sleep(_delay: float) -> None:
            calls.append(1)
            if len(calls) > 1:
                raise asyncio.CancelledError()

        q = await self.bus.subscribe(job_id)
        with patch("app.backend_daemon.job_manager.asyncio.sleep", new=fast_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await self.mgr._watchdog_loop()

        ev = await asyncio.wait_for(q.get(), timeout=2)
        self.assertEqual(ev.type, "task_error")
        row = self.mgr.conn.execute(
            "SELECT status, error_code FROM tasks WHERE job_id=?",
            (job_id,),
        ).fetchone()
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_code"], "WATCHDOG_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
