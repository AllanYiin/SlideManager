from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import pytest

from tests.helpers import ensure_src_path, load_schema_sql

ROOT = ensure_src_path()

from app.backend_daemon.config import JobOptions
from app.backend_daemon.main import create_app

pytestmark = pytest.mark.integration


class TestApiContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "index.sqlite"
        self.schema_sql = load_schema_sql(ROOT)
        self.app = create_app(self.db_path, self.schema_sql)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_post_jobs_index_returns_job_id(self) -> None:
        options = JobOptions(
            enable_text=True,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        payload = {"library_root": str(self.root), "options": options.model_dump()}
        res = self.client.post("/jobs/index", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("job_id", data)

    def test_post_jobs_index_invalid_root(self) -> None:
        options = JobOptions(
            enable_text=True,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        payload = {"library_root": str(self.root / "missing"), "options": options.model_dump()}
        res = self.client.post("/jobs/index", json=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("message", res.json())

    def test_pause_resume_cancel_returns_ok(self) -> None:
        options = JobOptions(
            enable_text=True,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        payload = {"library_root": str(self.root), "options": options.model_dump()}
        res = self.client.post("/jobs/index", json=payload)
        job_id = res.json()["job_id"]

        res = self.client.post(f"/jobs/{job_id}/pause")
        self.assertEqual(res.json(), {"ok": True})
        res = self.client.post(f"/jobs/{job_id}/resume")
        self.assertEqual(res.json(), {"ok": True})
        res = self.client.post(f"/jobs/{job_id}/cancel")
        self.assertEqual(res.json(), {"ok": True})

    def test_get_job_payload_contains_fields(self) -> None:
        options = JobOptions(
            enable_text=True,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        payload = {"library_root": str(self.root), "options": options.model_dump()}
        res = self.client.post("/jobs/index", json=payload)
        job_id = res.json()["job_id"]

        res = self.client.get(f"/jobs/{job_id}")
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("status", data)
        self.assertIn("stats", data)
        self.assertIn("now_running", data)

    def test_sse_first_event_is_hello(self) -> None:
        options = JobOptions(
            enable_text=True,
            enable_thumb=False,
            enable_text_vec=False,
            enable_img_vec=False,
            enable_bm25=False,
        )
        payload = {"library_root": str(self.root), "options": options.model_dump()}
        res = self.client.post("/jobs/index", json=payload)
        job_id = res.json()["job_id"]

        with self.client.stream("GET", f"/jobs/{job_id}/events") as resp:
            line = next(resp.iter_lines())
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        self.assertTrue(line.startswith("data: "))
        data = json.loads(line[len("data: ") :])
        self.assertEqual(data["type"], "hello")
        self.assertEqual(data["job_id"], job_id)


if __name__ == "__main__":
    unittest.main()
