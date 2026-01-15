from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import build_pptx, build_slide_xml, ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.config import JobOptions
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.job_manager import CancelToken, JobManager, PauseToken

pytestmark = pytest.mark.integration


class TestJobManagerText(unittest.IsolatedAsyncioTestCase):
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

    async def _plan_for_pptx(self, pptx_path: Path, options: JobOptions) -> None:
        job_id = "job1"
        self.mgr._insert_job(job_id, str(self.root), options)
        await self.mgr._plan_jobs(job_id, self.root, options, CancelToken(), PauseToken())

    async def test_checkpoint_each_page(self) -> None:
        pptx_path = self.root / "demo.pptx"
        build_pptx(
            pptx_path,
            [build_slide_xml(["A"]), build_slide_xml(["B"])],
            aspect="4:3",
        )
        options = JobOptions(
            enable_text=True,
            enable_bm25=False,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            commit_every_pages=1,
            commit_every_sec=10.0,
        )
        await self._plan_for_pptx(pptx_path, options)

        async def slow_extract(pptx: str, page_no: int):
            if page_no == 2:
                time.sleep(0.2)
            from app.backend_daemon.text_extract import extract_page_text

            return extract_page_text(pptx, page_no)

        q = await self.bus.subscribe("job1")
        with patch("app.backend_daemon.job_manager.extract_page_text", new=slow_extract):
            task = asyncio.create_task(
                self.mgr._run_text_and_bm25("job1", options, CancelToken(), PauseToken())
            )
            ev = await asyncio.wait_for(q.get(), timeout=2)
            self.assertEqual(ev.type, "artifact_state_changed")
            page1 = self.mgr.conn.execute(
                "SELECT status FROM artifacts WHERE page_id=1 AND kind='text'"
            ).fetchone()
            page2 = self.mgr.conn.execute(
                "SELECT status FROM artifacts WHERE page_id=2 AND kind='text'"
            ).fetchone()
            self.assertEqual(page1["status"], "ready")
            self.assertNotEqual(page2["status"], "ready")
            await task

    async def test_checkpoint_by_time(self) -> None:
        pptx_path = self.root / "demo.pptx"
        build_pptx(
            pptx_path,
            [build_slide_xml(["A"]), build_slide_xml(["B"])],
            aspect="4:3",
        )
        options = JobOptions(
            enable_text=True,
            enable_bm25=False,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            commit_every_pages=100,
            commit_every_sec=0.0,
        )
        await self._plan_for_pptx(pptx_path, options)

        q = await self.bus.subscribe("job1")

        task = asyncio.create_task(
            self.mgr._run_text_and_bm25("job1", options, CancelToken(), PauseToken())
        )
        ev = await asyncio.wait_for(q.get(), timeout=2)
        self.assertEqual(ev.type, "artifact_state_changed")

        from app.backend_daemon.db import open_db

        reader = open_db(self.db_path)
        try:
            row = reader.execute(
                "SELECT status FROM artifacts WHERE page_id=1 AND kind='text'"
            ).fetchone()
            self.assertEqual(row["status"], "ready")
        finally:
            reader.close()

        await task

    async def test_corrupt_slide_is_skipped(self) -> None:
        pptx_path = self.root / "demo.pptx"
        slides = [
            build_slide_xml(["A"]),
            "<broken",
            build_slide_xml(["C"]),
        ]
        build_pptx(pptx_path, slides, aspect="4:3")
        options = JobOptions(
            enable_text=True,
            enable_bm25=False,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
        )
        await self._plan_for_pptx(pptx_path, options)
        await self.mgr._run_text_and_bm25("job1", options, CancelToken(), PauseToken())

        rows = self.mgr.conn.execute(
            "SELECT page_id, status, error_code FROM artifacts WHERE kind='text' ORDER BY page_id"
        ).fetchall()
        self.assertEqual(rows[0]["status"], "ready")
        self.assertEqual(rows[1]["status"], "error")
        self.assertEqual(rows[1]["error_code"], "TEXT_EXTRACT_FAIL")
        self.assertEqual(rows[2]["status"], "ready")

    async def test_pause_and_resume(self) -> None:
        pptx_path = self.root / "demo.pptx"
        slides = [build_slide_xml([str(i)]) for i in range(5)]
        build_pptx(pptx_path, slides, aspect="4:3")
        options = JobOptions(
            enable_text=True,
            enable_bm25=False,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            commit_every_pages=1,
        )
        await self._plan_for_pptx(pptx_path, options)

        pause = PauseToken()
        cancel = CancelToken()

        async def slow_extract(pptx: str, page_no: int):
            time.sleep(0.1)
            from app.backend_daemon.text_extract import extract_page_text

            return extract_page_text(pptx, page_no)

        q = await self.bus.subscribe("job1")
        with patch("app.backend_daemon.job_manager.extract_page_text", new=slow_extract):
            task = asyncio.create_task(
                self.mgr._run_text_and_bm25("job1", options, cancel, pause)
            )
            await asyncio.wait_for(q.get(), timeout=2)
            pause.pause()

            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(q.get(), timeout=0.2)

            pause.resume()
            await task

        count = self.mgr.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE kind='text' AND status='ready'"
        ).fetchone()[0]
        self.assertEqual(count, 5)


if __name__ == "__main__":
    unittest.main()
