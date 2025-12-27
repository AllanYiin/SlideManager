from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.backend_daemon.bm25 import upsert_fts_page
from app.backend_daemon.config import JobOptions
from app.backend_daemon.db import now_epoch
from app.backend_daemon.embedder import (
    embed_text_batch_openai,
    pack_f32,
    zero_vector,
)
from app.backend_daemon.enums import (
    ArtifactKind,
    ArtifactStatus,
    JobStatus,
    TaskKind,
    TaskStatus,
)
from app.backend_daemon.event_bus import EventBus
from app.backend_daemon.pdf_convert import convert_pptx_to_pdf_libreoffice
from app.backend_daemon.pptx_meta import detect_aspect_from_pptx
from app.backend_daemon.rate_limit import DualTokenBucket
from app.backend_daemon.text_extract import extract_page_text
from app.backend_daemon.thumb_render import render_pdf_page_to_thumb, thumb_size
from app.backend_daemon.utils_win import is_windows, which_soffice_windows
from app.backend_daemon.planner import FileScan, scan_specific_files

logger = logging.getLogger(__name__)


@dataclass
class CancelToken:
    flag: asyncio.Event

    def __init__(self) -> None:
        self.flag = asyncio.Event()

    def cancel(self) -> None:
        self.flag.set()

    async def check(self) -> None:
        if self.flag.is_set():
            raise asyncio.CancelledError()


@dataclass
class PauseToken:
    flag: asyncio.Event

    def __init__(self) -> None:
        self.flag = asyncio.Event()
        self.flag.set()

    def pause(self) -> None:
        self.flag.clear()

    def resume(self) -> None:
        self.flag.set()

    async def wait_if_paused(self) -> None:
        await self.flag.wait()


class JobManager:
    def __init__(self, db_path: Path, schema_sql: str, event_bus: EventBus) -> None:
        from app.backend_daemon.db import open_db, init_schema

        self.db_path = db_path
        self.conn = open_db(db_path)
        init_schema(self.conn, schema_sql)
        self.bus = event_bus

        self._jobs: Dict[str, Dict[str, object]] = {}
        self._lock = asyncio.Lock()
        self._watchdog_task: Optional[asyncio.Task] = None

    async def start_watchdog(self) -> None:
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            now = now_epoch()
            rows = self.conn.execute(
                "SELECT task_id, job_id, kind, status, heartbeat_at, started_at "
                "FROM tasks WHERE status = ?",
                (TaskStatus.RUNNING,),
            ).fetchall()
            for r in rows:
                hb = r["heartbeat_at"] or r["started_at"] or now
                if now - int(hb) > 30:
                    self.conn.execute(
                        "UPDATE tasks SET status=?, finished_at=?, error_code=?, error_message=? WHERE task_id=?",
                        (
                            TaskStatus.ERROR,
                            now,
                            "WATCHDOG_TIMEOUT",
                            "task heartbeat timeout",
                            r["task_id"],
                        ),
                    )
                    self.conn.commit()
                    await self.bus.publish(
                        r["job_id"],
                        "task_error",
                        {
                            "task_id": r["task_id"],
                            "kind": r["kind"],
                            "error_code": "WATCHDOG_TIMEOUT",
                        },
                        ts=now,
                    )

    def _insert_job(self, job_id: str, library_root: str, options: JobOptions) -> None:
        now = now_epoch()
        self.conn.execute(
            "INSERT INTO jobs(job_id,library_root,created_at,status,options_json) VALUES (?,?,?,?,?)",
            (job_id, library_root, now, JobStatus.CREATED, options.model_dump_json()),
        )
        self.conn.commit()

    async def create_job(self, library_root: str, options: JobOptions) -> str:
        await self.start_watchdog()
        job_id = f"J{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._insert_job(job_id, library_root, options)
        await self.bus.publish(
            job_id, "job_created", {"library_root": library_root}, ts=now_epoch()
        )

        cancel = CancelToken()
        pause = PauseToken()

        async with self._lock:
            self._jobs[job_id] = {"cancel": cancel, "pause": pause}

        asyncio.create_task(
            self._run_job(job_id, Path(library_root), options, cancel, pause)
        )
        return job_id

    async def pause_job(self, job_id: str) -> None:
        j = self._jobs.get(job_id)
        if not j:
            return
        pause: PauseToken = j["pause"]
        pause.pause()
        self.conn.execute("UPDATE jobs SET status=? WHERE job_id=?", (JobStatus.PAUSED, job_id))
        self.conn.commit()
        await self.bus.publish(job_id, "job_paused", {}, ts=now_epoch())

    async def resume_job(self, job_id: str) -> None:
        j = self._jobs.get(job_id)
        if not j:
            return
        pause: PauseToken = j["pause"]
        pause.resume()
        self.conn.execute("UPDATE jobs SET status=? WHERE job_id=?", (JobStatus.RUNNING, job_id))
        self.conn.commit()
        await self.bus.publish(job_id, "job_resumed", {}, ts=now_epoch())

    async def cancel_job(self, job_id: str) -> None:
        j = self._jobs.get(job_id)
        if not j:
            return
        cancel: CancelToken = j["cancel"]
        cancel.cancel()
        self.conn.execute(
            "UPDATE jobs SET status=? WHERE job_id=?",
            (JobStatus.CANCEL_REQUESTED, job_id),
        )
        self.conn.commit()
        await self.bus.publish(job_id, "job_cancel_requested", {}, ts=now_epoch())

    async def _run_job(
        self,
        job_id: str,
        root: Path,
        options: JobOptions,
        cancel: CancelToken,
        pause: PauseToken,
    ) -> None:
        await asyncio.sleep(0)
        now = now_epoch()
        self.conn.execute(
            "UPDATE jobs SET status=?, started_at=? WHERE job_id=?",
            (JobStatus.PLANNING, now, job_id),
        )
        self.conn.commit()
        await self.bus.publish(job_id, "job_planning_started", {}, ts=now_epoch())

        try:
            await self._plan_jobs(job_id, root, options, cancel, pause)
        except Exception as exc:
            logger.exception("planning failed: %s", exc)
            self.conn.execute(
                "UPDATE jobs SET status=?, finished_at=? WHERE job_id=?",
                (JobStatus.FAILED, now_epoch(), job_id),
            )
            self.conn.commit()
            await self.bus.publish(
                job_id, "job_failed", {"error": str(exc)}, ts=now_epoch()
            )
            return

        now = now_epoch()
        self.conn.execute(
            "UPDATE jobs SET status=?, started_at=COALESCE(started_at,?) WHERE job_id=?",
            (JobStatus.RUNNING, now, job_id),
        )
        self.conn.commit()
        await self.bus.publish(job_id, "job_started", {}, ts=now_epoch())

        try:
            await self._run_text_and_bm25(job_id, options, cancel, pause)
            await self._run_pdf_and_thumbs(job_id, root, options, cancel, pause)
            await self._run_text_embeddings(job_id, options, cancel, pause)
            await cancel.check()
        except asyncio.CancelledError:
            self._finalize_cancel(job_id)
            await self.bus.publish(job_id, "job_cancelled", {}, ts=now_epoch())
            return
        except Exception as exc:
            logger.exception("job failed: %s", exc)
            self.conn.execute(
                "UPDATE jobs SET status=?, finished_at=? WHERE job_id=?",
                (JobStatus.FAILED, now_epoch(), job_id),
            )
            self.conn.commit()
            await self.bus.publish(
                job_id, "job_failed", {"error": str(exc)}, ts=now_epoch()
            )
            return

        self.conn.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE job_id=?",
            (JobStatus.COMPLETED, now_epoch(), job_id),
        )
        self.conn.commit()
        await self.bus.publish(job_id, "job_completed", {}, ts=now_epoch())

    async def _plan_jobs(
        self,
        job_id: str,
        root: Path,
        options: JobOptions,
        cancel: CancelToken,
        pause: PauseToken,
    ) -> None:
        root_resolved = root.resolve()
        allowed_paths: Optional[set[str]] = None
        if options.file_paths:
            allowed_paths = set()
            for raw in options.file_paths:
                if not raw:
                    continue
                try:
                    allowed_paths.add(str(Path(raw).resolve()))
                except Exception:
                    logger.warning("[INDEX_PLAN] skip_invalid_file_path path=%s", raw)

        skipped_counts: dict[str, int] = {}
        skipped_examples: dict[str, list[str]] = {}
        skipped_source: str | None = None

        def record_skip(reason: str, path: str) -> None:
            skipped_counts[reason] = skipped_counts.get(reason, 0) + 1
            bucket = skipped_examples.setdefault(reason, [])
            if len(bucket) < 20 and path:
                bucket.append(path)

        def is_under_root(path: Path) -> bool:
            try:
                resolved = path.resolve()
            except Exception:
                return False
            return resolved == root_resolved or root_resolved in resolved.parents

        scans: List[FileScan] = []
        if options.file_scans:
            skipped_source = "frontend_scans"
            logger.info(
                "[INDEX_PLAN] source=frontend_scans file_scans=%d",
                len(options.file_scans),
            )
            total_entries = len(options.file_scans)
            for idx, entry in enumerate(options.file_scans, start=1):
                try:
                    raw_path = getattr(entry, "path", None) or ""
                    if not raw_path:
                        record_skip("missing_path", raw_path)
                        continue
                    p = Path(raw_path)
                    if p.suffix.lower() not in (".pptx",):
                        logger.warning(
                            "[INDEX_PLAN] skip_non_pptx current=%d total=%d path=%s",
                            idx,
                            total_entries,
                            raw_path,
                        )
                        record_skip("non_pptx", raw_path)
                        continue
                    if not is_under_root(p):
                        logger.warning(
                            "[INDEX_PLAN] skip_outside_root current=%d total=%d path=%s root=%s",
                            idx,
                            total_entries,
                            raw_path,
                            root_resolved,
                        )
                        record_skip("outside_root", raw_path)
                        continue
                    resolved_path = str(p.resolve())
                    if allowed_paths is not None and resolved_path not in allowed_paths:
                        logger.warning(
                            "[INDEX_PLAN] skip_unselected_path current=%d total=%d path=%s",
                            idx,
                            total_entries,
                            resolved_path,
                        )
                        record_skip("unselected_path", resolved_path)
                        continue
                    scans.append(
                        FileScan(
                            path=resolved_path,
                            size_bytes=int(entry.size_bytes),
                            mtime_epoch=int(entry.mtime_epoch),
                        )
                    )
                except Exception as exc:
                    logger.exception(
                        "[INDEX_PLAN] parse_frontend_scan_failed current=%d total=%d error=%s",
                        idx,
                        total_entries,
                        exc,
                    )
                    record_skip("parse_failed", raw_path if "raw_path" in locals() else "")
            logger.info(
                "[INDEX_PLAN] resolved_frontend_scans total=%d valid=%d",
                total_entries,
                len(scans),
            )
        if not scans:
            if options.file_paths:
                skipped_source = "frontend_paths"
                logger.info(
                    "[INDEX_PLAN] source=frontend_paths file_paths=%d root=%s",
                    len(options.file_paths),
                    root,
                )
                scans = scan_specific_files(options.file_paths)
                scans = [fs for fs in scans if is_under_root(Path(fs.path))]
            else:
                logger.error(
                    "[INDEX_PLAN] missing_frontend_inputs file_paths=0 file_scans=0 root=%s",
                    root,
                )
                raise ValueError("missing_frontend_scan_inputs")

        def slide_count_fast(pptx: str) -> int:
            import zipfile

            with zipfile.ZipFile(pptx) as zf:
                return sum(
                    1
                    for n in zf.namelist()
                    if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                )

        for fs in scans:
            await pause.wait_if_paused()
            await cancel.check()

            aspect = "unknown"
            cur = self.conn.execute(
                "SELECT file_id,size_bytes,mtime_epoch FROM files WHERE path=?",
                (fs.path,),
            )
            prev = cur.fetchone()
            file_id = self._upsert_file(fs.path, fs.size_bytes, fs.mtime_epoch, aspect)

            try:
                if not zipfile.is_zipfile(fs.path):
                    msg = "File is not a zip file"
                    logger.error("slide_count failed: %s", msg)
                    self.conn.execute(
                        "UPDATE files SET scan_error=? WHERE file_id=?",
                        (msg, file_id),
                    )
                    self.conn.commit()
                    continue

                aspect = detect_aspect_from_pptx(fs.path)
                self.conn.execute(
                    "UPDATE files SET slide_aspect=? WHERE file_id=?",
                    (aspect, file_id),
                )

                try:
                    sc = slide_count_fast(fs.path)
                    self.conn.execute(
                        "UPDATE files SET slide_count=? WHERE file_id=?",
                        (sc, file_id),
                    )
                except Exception as exc:
                    logger.exception("slide_count failed: %s", exc)
                    self.conn.execute(
                        "UPDATE files SET scan_error=? WHERE file_id=?",
                        (str(exc), file_id),
                    )
                    self.conn.commit()
                    continue

                self.conn.commit()

                page_ids = self._ensure_pages_rows(
                    file_id, sc, aspect, fs.size_bytes, fs.mtime_epoch
                )
                self.conn.commit()

                changed = False
                if prev is None:
                    changed = True
                else:
                    changed = (
                        int(prev["size_bytes"]) != fs.size_bytes
                        or int(prev["mtime_epoch"]) != fs.mtime_epoch
                    )

                need_pdf = False
                for page_id in page_ids:
                    status_map = self._artifact_status_map(page_id)
                    text_needed = self._artifact_needs_refresh(status_map.get(str(ArtifactKind.TEXT)), changed)
                    thumb_needed = self._artifact_needs_refresh(status_map.get(str(ArtifactKind.THUMB)), changed)
                    bm25_needed = self._artifact_needs_refresh(status_map.get(str(ArtifactKind.BM25)), changed)
                    text_vec_needed = self._artifact_needs_refresh(
                        status_map.get(str(ArtifactKind.TEXT_VEC)), changed
                    )
                    img_vec_needed = self._artifact_needs_refresh(
                        status_map.get(str(ArtifactKind.IMG_VEC)), changed
                    )

                    if options.enable_text and text_needed:
                        self._artifact_set(
                            job_id,
                            page_id,
                            ArtifactKind.TEXT,
                            ArtifactStatus.QUEUED,
                            options=params_for_text(options),
                        )
                    if options.enable_thumb and options.thumb.enabled and options.pdf.enabled:
                        if thumb_needed:
                            self._artifact_set(
                                job_id,
                                page_id,
                                ArtifactKind.THUMB,
                                ArtifactStatus.QUEUED,
                                options=params_for_thumb(options, aspect),
                            )
                            need_pdf = True
                    if options.enable_bm25 and bm25_needed:
                        self._artifact_set(
                            job_id,
                            page_id,
                            ArtifactKind.BM25,
                            ArtifactStatus.QUEUED,
                            options=params_for_bm25(options),
                        )
                    if options.enable_text_vec and options.embed.enabled_text and text_vec_needed:
                        self._artifact_set(
                            job_id,
                            page_id,
                            ArtifactKind.TEXT_VEC,
                            ArtifactStatus.QUEUED,
                            options=params_for_text_vec(options),
                        )
                    if (
                        options.enable_img_vec
                        and options.embed.enabled_image
                        and options.thumb.enabled
                        and img_vec_needed
                    ):
                        self._artifact_set(
                            job_id,
                            page_id,
                            ArtifactKind.IMG_VEC,
                            ArtifactStatus.QUEUED,
                            options=params_for_img_vec(options),
                        )
            except Exception as exc:
                logger.exception("file planning failed: %s", exc)
                self.conn.execute(
                    "UPDATE files SET scan_error=? WHERE file_id=?",
                    (str(exc), file_id),
                )
                self.conn.commit()
                continue

            if options.pdf.enabled and options.thumb.enabled and (changed or need_pdf):
                self._enqueue_file_task_pdf(job_id, file_id, fs.path, priority=10)

        self.conn.commit()
        task_rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS cnt FROM tasks WHERE job_id=? GROUP BY kind",
            (job_id,),
        ).fetchall()
        task_counts = {str(r["kind"]): int(r["cnt"]) for r in task_rows}
        total_tasks = sum(task_counts.values())
        if total_tasks == 0:
            logger.warning(
                "[INDEX_PLAN] no_tasks_created job_id=%s files=%d options=%s",
                job_id,
                len(scans),
                options.model_dump(),
            )
        await self.bus.publish(
            job_id,
            "job_planning_finished",
            {
                "files": len(scans),
                "task_counts": task_counts,
                "task_total": total_tasks,
                "skipped": {
                    "source": skipped_source,
                    "counts": skipped_counts,
                    "examples": skipped_examples,
                },
            },
            ts=now_epoch(),
        )

    def _upsert_file(
        self, path: str, size_bytes: int, mtime_epoch: int, aspect: str
    ) -> int:
        cur = self.conn.execute("SELECT file_id FROM files WHERE path = ?", (path,))
        row = cur.fetchone()
        now = now_epoch()
        if row is None:
            self.conn.execute(
                "INSERT INTO files(path,size_bytes,mtime_epoch,slide_aspect,last_scanned_at,scan_error) VALUES (?,?,?,?,?,NULL)",
                (path, size_bytes, mtime_epoch, aspect, now),
            )
            return int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.conn.execute(
            "UPDATE files SET size_bytes=?, mtime_epoch=?, slide_aspect=COALESCE(?,slide_aspect), last_scanned_at=?, scan_error=NULL WHERE file_id=?",
            (size_bytes, mtime_epoch, aspect, now, row["file_id"]),
        )
        return int(row["file_id"])

    def _ensure_pages_rows(
        self,
        file_id: int,
        slide_count: int,
        aspect: str,
        size_bytes: int,
        mtime_epoch: int,
    ) -> List[int]:
        now = now_epoch()
        page_ids: List[int] = []

        for page_no in range(1, slide_count + 1):
            cur = self.conn.execute(
                "SELECT page_id FROM pages WHERE file_id=? AND page_no=?",
                (file_id, page_no),
            )
            r = cur.fetchone()
            if r is None:
                self.conn.execute(
                    "INSERT INTO pages(file_id,page_no,aspect,source_size_bytes,source_mtime_epoch,created_at) VALUES (?,?,?,?,?,?)",
                    (file_id, page_no, aspect, size_bytes, mtime_epoch, now),
                )
                page_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            else:
                page_id = int(r["page_id"])
                self.conn.execute(
                    "UPDATE pages SET aspect=?, source_size_bytes=?, source_mtime_epoch=? WHERE page_id=?",
                    (aspect, size_bytes, mtime_epoch, page_id),
                )

            page_ids.append(page_id)

            for kind in (
                ArtifactKind.TEXT,
                ArtifactKind.THUMB,
                ArtifactKind.TEXT_VEC,
                ArtifactKind.IMG_VEC,
                ArtifactKind.BM25,
            ):
                self.conn.execute(
                    "INSERT OR IGNORE INTO artifacts(page_id,kind,status,updated_at,attempts) VALUES (?,?,?,?,0)",
                    (page_id, str(kind), ArtifactStatus.MISSING, now),
                )

        return page_ids

    def _artifact_status_map(self, page_id: int) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT kind, status FROM artifacts WHERE page_id=?",
            (page_id,),
        ).fetchall()
        return {str(r["kind"]): str(r["status"]) for r in rows}

    def _artifact_needs_refresh(self, status: str | None, changed: bool) -> bool:
        if changed:
            return True
        if status is None:
            return True
        return status not in {ArtifactStatus.READY, ArtifactStatus.SKIPPED}

    def _artifact_set(
        self, job_id: str, page_id: int, kind: ArtifactKind, status: ArtifactStatus, options: dict
    ) -> None:
        now = now_epoch()
        self.conn.execute(
            "UPDATE artifacts SET status=?, updated_at=?, params_json=? WHERE page_id=? AND kind=?",
            (status, now, json.dumps(options, ensure_ascii=False), page_id, str(kind)),
        )
        self.conn.execute(
            "INSERT INTO tasks(job_id,page_id,kind,status,priority) VALUES (?,?,?,?,?)",
            (job_id, page_id, str(kind), TaskStatus.QUEUED, 0),
        )

    def _enqueue_file_task_pdf(self, job_id: str, file_id: int, path: str, priority: int) -> None:
        self.conn.execute(
            "INSERT INTO tasks(job_id,file_id,kind,status,priority) VALUES (?,?,?,?,?)",
            (job_id, file_id, TaskKind.PDF, TaskStatus.QUEUED, priority),
        )

    async def _run_text_and_bm25(
        self, job_id: str, options: JobOptions, cancel: CancelToken, pause: PauseToken
    ) -> None:
        rows = self.conn.execute(
            "SELECT t.task_id, t.page_id, p.file_id, p.page_no, f.path "
            "FROM tasks t JOIN pages p ON p.page_id=t.page_id JOIN files f ON f.file_id=p.file_id "
            "WHERE t.job_id=? AND t.kind=? AND t.status=? ORDER BY f.file_id, p.page_no",
            (job_id, ArtifactKind.TEXT, TaskStatus.QUEUED),
        ).fetchall()

        processed = 0
        last_commit_ts = time.monotonic()
        for r in rows:
            await pause.wait_if_paused()
            await cancel.check()
            task_id = int(r["task_id"])
            page_id = int(r["page_id"])
            pptx_path = str(r["path"])
            page_no = int(r["page_no"])

            self._task_start(task_id)
            try:
                raw, norm, sig = await asyncio.to_thread(
                    extract_page_text, pptx_path, page_no
                )
                now = now_epoch()
                self.conn.execute(
                    "INSERT INTO page_text(page_id,raw_text,norm_text,text_sig,updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(page_id) DO UPDATE SET raw_text=excluded.raw_text, norm_text=excluded.norm_text, text_sig=excluded.text_sig, updated_at=excluded.updated_at",
                    (page_id, raw, norm, sig, now),
                )
                self.conn.execute(
                    "UPDATE artifacts SET status=?, updated_at=?, attempts=attempts+1 WHERE page_id=? AND kind=?",
                    (ArtifactStatus.READY, now, page_id, ArtifactKind.TEXT),
                )
                if options.enable_bm25:
                    upsert_fts_page(self.conn, page_id, norm)
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=? WHERE page_id=? AND kind=?",
                        (ArtifactStatus.READY, now, page_id, ArtifactKind.BM25),
                    )
                self._task_finish_ok(task_id)

                processed += 1
                if processed % options.commit_every_pages == 0 or (
                    time.monotonic() - last_commit_ts
                ) >= options.commit_every_sec:
                    self.conn.commit()
                    last_commit_ts = time.monotonic()

                await self.bus.publish(
                    job_id,
                    "artifact_state_changed",
                    {
                        "page_id": page_id,
                        "kind": "text",
                        "status": "ready",
                        "file": pptx_path,
                        "page_no": page_no,
                    },
                    ts=now_epoch(),
                )
            except Exception as exc:
                now = now_epoch()
                logger.exception("text extract failed: %s", exc)
                self.conn.execute(
                    "UPDATE artifacts SET status=?, updated_at=?, error_code=?, error_message=?, attempts=attempts+1 WHERE page_id=? AND kind=?",
                    (
                        ArtifactStatus.ERROR,
                        now,
                        "TEXT_EXTRACT_FAIL",
                        str(exc)[:500],
                        page_id,
                        ArtifactKind.TEXT,
                    ),
                )
                self._task_finish_err(task_id, "TEXT_EXTRACT_FAIL", str(exc))
                self.conn.commit()
                continue

        self.conn.commit()

    async def _run_pdf_and_thumbs(
        self, job_id: str, root: Path, options: JobOptions, cancel: CancelToken, pause: PauseToken
    ) -> None:
        if not (options.enable_thumb and options.thumb.enabled and options.pdf.enabled):
            return

        pdf_tasks = self.conn.execute(
            "SELECT task_id, file_id FROM tasks WHERE job_id=? AND kind=? AND status=? ORDER BY priority DESC, task_id ASC",
            (job_id, TaskKind.PDF, TaskStatus.QUEUED),
        ).fetchall()

        soffice = None
        if options.pdf.prefer in ("auto", "libreoffice"):
            soffice = which_soffice_windows() if is_windows() else "soffice"

        pdf_dir = root / ".slidemanager" / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        for t in pdf_tasks:
            await pause.wait_if_paused()
            await cancel.check()

            task_id = int(t["task_id"])
            file_id = int(t["file_id"])
            fr = self.conn.execute(
                "SELECT path, slide_aspect, slide_count FROM files WHERE file_id=?",
                (file_id,),
            ).fetchone()
            if fr is None:
                self._task_finish_err(task_id, "FILE_MISSING", "file row missing")
                continue

            pptx_path = Path(str(fr["path"]))
            aspect = str(fr["slide_aspect"] or "unknown")
            out_pdf = pdf_dir / f"{file_id}.pdf"

            self._task_start(task_id)
            try:
                await asyncio.to_thread(
                    convert_pptx_to_pdf_libreoffice,
                    pptx_path,
                    out_pdf,
                    soffice,
                    options.pdf.timeout_sec,
                )
                self._task_finish_ok(task_id)
                self.conn.commit()
            except Exception as exc:
                logger.exception("pdf convert failed: %s", exc)
                now = now_epoch()
                page_rows = self.conn.execute(
                    "SELECT page_id FROM pages WHERE file_id=?",
                    (file_id,),
                ).fetchall()
                for pr in page_rows:
                    pid = int(pr["page_id"])
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=?, error_code=?, error_message=? WHERE page_id=? AND kind=?",
                        (
                            ArtifactStatus.ERROR,
                            now,
                            "PDF_CONVERT_FAIL",
                            str(exc)[:500],
                            pid,
                            ArtifactKind.THUMB,
                        ),
                    )
                self._task_finish_err(task_id, "PDF_CONVERT_FAIL", str(exc))
                self.conn.commit()
                continue

            thumb_tasks = self.conn.execute(
                "SELECT t.task_id, t.page_id, p.page_no, p.aspect FROM tasks t JOIN pages p ON p.page_id=t.page_id "
                "WHERE t.job_id=? AND t.kind=? AND t.status=? AND p.file_id=? ORDER BY p.page_no",
                (job_id, ArtifactKind.THUMB, TaskStatus.QUEUED, file_id),
            ).fetchall()

            thumb_root = root / ".slidemanager" / "thumbs" / str(file_id)
            for tr in thumb_tasks:
                await pause.wait_if_paused()
                await cancel.check()
                tt_id = int(tr["task_id"])
                page_id = int(tr["page_id"])
                page_no = int(tr["page_no"])
                p_aspect = str(tr["aspect"] or aspect)

                w, h = thumb_size(
                    p_aspect if p_aspect in ("4:3", "16:9") else "unknown",
                    options.thumb.width,
                    options.thumb.height_4_3,
                    options.thumb.height_16_9,
                )
                out_img = thumb_root / f"{page_no}_{p_aspect}_{w}x{h}.jpg"

                self._task_start(tt_id)
                try:
                    await asyncio.to_thread(
                        render_pdf_page_to_thumb,
                        out_pdf,
                        page_no - 1,
                        out_img,
                        w,
                        h,
                    )
                    now2 = now_epoch()
                    self.conn.execute(
                        "INSERT OR REPLACE INTO thumbnails(page_id,aspect,width,height,image_path,updated_at) VALUES (?,?,?,?,?,?)",
                        (page_id, p_aspect, w, h, str(out_img), now2),
                    )
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=?, attempts=attempts+1 WHERE page_id=? AND kind=?",
                        (ArtifactStatus.READY, now2, page_id, ArtifactKind.THUMB),
                    )
                    self._task_finish_ok(tt_id)
                    self.conn.commit()

                    await self.bus.publish(
                        job_id,
                        "artifact_state_changed",
                        {
                            "page_id": page_id,
                            "kind": "thumb",
                            "status": "ready",
                            "file": str(pptx_path),
                            "page_no": page_no,
                        },
                        ts=now_epoch(),
                    )
                except Exception as exc:
                    logger.exception("thumb render failed: %s", exc)
                    now2 = now_epoch()
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=?, error_code=?, error_message=?, attempts=attempts+1 WHERE page_id=? AND kind=?",
                        (
                            ArtifactStatus.ERROR,
                            now2,
                            "THUMB_FAIL",
                            str(exc)[:500],
                            page_id,
                            ArtifactKind.THUMB,
                        ),
                    )
                    self._task_finish_err(tt_id, "THUMB_FAIL", str(exc))
                    self.conn.commit()
                    continue

    async def _run_text_embeddings(
        self, job_id: str, options: JobOptions, cancel: CancelToken, pause: PauseToken
    ) -> None:
        if not (options.enable_text_vec and options.embed.enabled_text):
            return

        limiter = DualTokenBucket(options.embed.req_per_min, options.embed.tok_per_min)

        rows = self.conn.execute(
            "SELECT t.task_id, t.page_id, p.page_no, f.path, pt.norm_text, pt.text_sig "
            "FROM tasks t "
            "JOIN pages p ON p.page_id=t.page_id "
            "JOIN files f ON f.file_id=p.file_id "
            "LEFT JOIN page_text pt ON pt.page_id=p.page_id "
            "WHERE t.job_id=? AND t.kind=? AND t.status=? "
            "ORDER BY t.task_id ASC",
            (job_id, ArtifactKind.TEXT_VEC, TaskStatus.QUEUED),
        ).fetchall()

        needs: List[Tuple[int, int, str, str, str]] = []
        for r in rows:
            task_id = int(r["task_id"])
            page_id = int(r["page_id"])
            pptx_path = str(r["path"])
            page_no = int(r["page_no"])
            norm = str(r["norm_text"] or "")
            sig = str(r["text_sig"] or "")

            await pause.wait_if_paused()
            await cancel.check()

            self._task_start(task_id)

            if not norm.strip():
                dim = 3072
                vb = zero_vector(dim)
                now = now_epoch()
                self._upsert_text_vec_cache_and_link(
                    page_id,
                    options.embed.model_text,
                    sig="",
                    dim=dim,
                    vector_blob=vb,
                    now=now,
                    is_cache_key=False,
                )
                self.conn.execute(
                    "UPDATE artifacts SET status=?, updated_at=? WHERE page_id=? AND kind=?",
                    (ArtifactStatus.READY, now, page_id, ArtifactKind.TEXT_VEC),
                )
                self._task_finish_ok(task_id)
                self.conn.commit()
                continue

            if sig:
                hit = self.conn.execute(
                    "SELECT dim, vector_blob FROM embedding_cache_text WHERE model=? AND text_sig=?",
                    (options.embed.model_text, sig),
                ).fetchone()
                if hit is not None:
                    now = now_epoch()
                    self.conn.execute(
                        "INSERT OR REPLACE INTO page_text_embedding(page_id,model,text_sig,updated_at) VALUES (?,?,?,?)",
                        (page_id, options.embed.model_text, sig, now),
                    )
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=? WHERE page_id=? AND kind=?",
                        (ArtifactStatus.READY, now, page_id, ArtifactKind.TEXT_VEC),
                    )
                    self._task_finish_ok(task_id)
                    self.conn.commit()
                    continue

            needs.append((task_id, page_id, pptx_path, norm, sig))

        i = 0
        while i < len(needs):
            await pause.wait_if_paused()
            await cancel.check()

            batch = needs[i : i + options.embed.batch_size]
            texts = [b[3] for b in batch]
            try:
                vecs = await embed_text_batch_openai(
                    texts,
                    options.embed.model_text,
                    limiter,
                    options.embed.max_retries,
                )
            except Exception as exc:
                logger.exception("embedding failed: %s", exc)
                now = now_epoch()
                for (task_id, page_id, _pptx, _norm, _sig) in batch:
                    self.conn.execute(
                        "UPDATE artifacts SET status=?, updated_at=?, error_code=?, error_message=? WHERE page_id=? AND kind=?",
                        (
                            ArtifactStatus.ERROR,
                            now,
                            "EMBED_FAIL",
                            str(exc)[:500],
                            page_id,
                            ArtifactKind.TEXT_VEC,
                        ),
                    )
                    self._task_finish_err(task_id, "EMBED_FAIL", str(exc))
                self.conn.commit()
                i += len(batch)
                continue

            now = now_epoch()
            for (task_id, page_id, _pptx, _norm, sig), vec in zip(batch, vecs):
                dim = len(vec)
                vb = pack_f32(vec)
                if sig:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO embedding_cache_text(model,text_sig,dim,vector_blob,created_at) VALUES (?,?,?,?,?)",
                        (options.embed.model_text, sig, dim, vb, now),
                    )
                    self.conn.execute(
                        "INSERT OR REPLACE INTO page_text_embedding(page_id,model,text_sig,updated_at) VALUES (?,?,?,?)",
                        (page_id, options.embed.model_text, sig, now),
                    )
                else:
                    tmp_sig = f"__nosig__:{page_id}:{now}"
                    self.conn.execute(
                        "INSERT OR REPLACE INTO embedding_cache_text(model,text_sig,dim,vector_blob,created_at) VALUES (?,?,?,?,?)",
                        (options.embed.model_text, tmp_sig, dim, vb, now),
                    )
                    self.conn.execute(
                        "INSERT OR REPLACE INTO page_text_embedding(page_id,model,text_sig,updated_at) VALUES (?,?,?,?)",
                        (page_id, options.embed.model_text, tmp_sig, now),
                    )

                self.conn.execute(
                    "UPDATE artifacts SET status=?, updated_at=? WHERE page_id=? AND kind=?",
                    (ArtifactStatus.READY, now, page_id, ArtifactKind.TEXT_VEC),
                )
                self._task_finish_ok(task_id)

            self.conn.commit()
            i += len(batch)

    def _upsert_text_vec_cache_and_link(
        self,
        page_id: int,
        model: str,
        sig: str,
        dim: int,
        vector_blob: bytes,
        now: int,
        is_cache_key: bool,
    ) -> None:
        if is_cache_key and sig:
            self.conn.execute(
                "INSERT OR REPLACE INTO embedding_cache_text(model,text_sig,dim,vector_blob,created_at) VALUES (?,?,?,?,?)",
                (model, sig, dim, vector_blob, now),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO page_text_embedding(page_id,model,text_sig,updated_at) VALUES (?,?,?,?)",
                (page_id, model, sig, now),
            )
        else:
            tmp_sig = f"__zero__:{page_id}:{now}"
            self.conn.execute(
                "INSERT OR REPLACE INTO embedding_cache_text(model,text_sig,dim,vector_blob,created_at) VALUES (?,?,?,?,?)",
                (model, tmp_sig, dim, vector_blob, now),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO page_text_embedding(page_id,model,text_sig,updated_at) VALUES (?,?,?,?)",
                (page_id, model, tmp_sig, now),
            )

    def _task_start(self, task_id: int) -> None:
        now = now_epoch()
        self.conn.execute(
            "UPDATE tasks SET status=?, started_at=COALESCE(started_at,?), heartbeat_at=?, message=? WHERE task_id=?",
            (TaskStatus.RUNNING, now, now, "start", task_id),
        )
        self.conn.commit()

    def _task_finish_ok(self, task_id: int) -> None:
        now = now_epoch()
        self.conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, heartbeat_at=?, progress=?, message=? WHERE task_id=?",
            (TaskStatus.SUCCEEDED, now, now, 1.0, "ok", task_id),
        )

    def _task_finish_err(self, task_id: int, code: str, msg: str) -> None:
        now = now_epoch()
        self.conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, heartbeat_at=?, error_code=?, error_message=? WHERE task_id=?",
            (TaskStatus.ERROR, now, now, code, msg[:500], task_id),
        )

    def _finalize_cancel(self, job_id: str) -> None:
        now = now_epoch()
        self.conn.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE job_id=?",
            (JobStatus.CANCELLED, now, job_id),
        )
        self.conn.execute(
            "UPDATE tasks SET status=?, finished_at=? WHERE job_id=? AND status IN (?,?)",
            (TaskStatus.CANCELLED, now, job_id, TaskStatus.QUEUED, TaskStatus.RUNNING),
        )
        self.conn.execute(
            "UPDATE artifacts SET status=?, updated_at=? WHERE status IN (?,?)",
            (ArtifactStatus.CANCELLED, now, ArtifactStatus.QUEUED, ArtifactStatus.RUNNING),
        )
        self.conn.commit()


def params_for_text(options: JobOptions) -> dict:
    return {"v": 1}


def params_for_thumb(options: JobOptions, aspect: str) -> dict:
    return {
        "v": 1,
        "w": options.thumb.width,
        "h43": options.thumb.height_4_3,
        "h169": options.thumb.height_16_9,
        "aspect": aspect,
    }


def params_for_bm25(options: JobOptions) -> dict:
    return {"v": 1}


def params_for_text_vec(options: JobOptions) -> dict:
    return {"v": 1, "model": options.embed.model_text}


def params_for_img_vec(options: JobOptions) -> dict:
    return {"v": 1, "model": options.embed.model_image}
