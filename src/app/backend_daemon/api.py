from __future__ import annotations

import json

import os
from pathlib import Path

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.backend_daemon.config import JobOptions
from app.backend_daemon.event_bus import EventBus, sse_format
from app.backend_daemon.job_manager import JobManager

router = APIRouter()


def get_bus(request) -> EventBus:
    return request.app.state.bus


def get_mgr(request) -> JobManager:
    return request.app.state.mgr


def _build_path_filter(library_root: str | None) -> tuple[str, list[object]]:
    if not library_root:
        return "", []
    root = str(Path(library_root).resolve())
    if not root.endswith(("/", "\\")):
        root = root + os.sep
    return "WHERE f.path LIKE ?", [f"{root}%"]


@router.get("/health")
async def health():
    return {"ok": True}


@router.post("/jobs/index")
async def start_job(
    request,
    library_root: str = Body(...),
    options: JobOptions = Body(default_factory=JobOptions),
):
    root_path = Path(library_root)
    if not root_path.exists() or not root_path.is_dir():
        return JSONResponse(
            status_code=400,
            content={"message": "library_root_not_found"},
        )
    mgr = get_mgr(request)
    job_id = await mgr.create_job(library_root, options)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job(request, job_id: str):
    mgr = get_mgr(request)
    row = mgr.conn.execute(
        "SELECT job_id, library_root, created_at, started_at, finished_at, status, options_json "
        "FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return {"ok": False, "message": "job_not_found"}

    options = {}
    try:
        options = json.loads(row["options_json"] or "{}")
    except Exception:
        options = {}

    stats_rows = mgr.conn.execute(
        "SELECT a.kind, a.status, COUNT(*) AS cnt "
        "FROM artifacts a "
        "WHERE a.page_id IN (SELECT DISTINCT page_id FROM tasks WHERE job_id=? AND page_id IS NOT NULL) "
        "GROUP BY a.kind, a.status",
        (job_id,),
    ).fetchall()
    stats: dict[str, dict[str, int]] = {}
    for r in stats_rows:
        kind = str(r["kind"])
        status = str(r["status"])
        stats.setdefault(kind, {})[status] = int(r["cnt"])

    running = mgr.conn.execute(
        "SELECT t.task_id, t.kind, t.message, t.progress, t.page_id, t.file_id, "
        "p.page_no, f.path "
        "FROM tasks t "
        "LEFT JOIN pages p ON p.page_id = t.page_id "
        "LEFT JOIN files f ON f.file_id = COALESCE(t.file_id, p.file_id) "
        "WHERE t.job_id=? AND t.status=? "
        "ORDER BY t.started_at DESC LIMIT 1",
        (job_id, "running"),
    ).fetchone()
    now_running = None
    if running is not None:
        now_running = {
            "task_id": int(running["task_id"]),
            "kind": running["kind"],
            "message": running["message"],
            "progress": running["progress"],
            "page_id": running["page_id"],
            "file_id": running["file_id"],
            "page_no": running["page_no"],
            "file_path": running["path"],
        }

    return {
        "ok": True,
        "job_id": row["job_id"],
        "status": row["status"],
        "library_root": row["library_root"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "options": options,
        "stats": stats,
        "now_running": now_running,
    }


@router.post("/jobs/{job_id}/pause")
async def pause_job(request, job_id: str):
    mgr = get_mgr(request)
    await mgr.pause_job(job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/resume")
async def resume_job(request, job_id: str):
    mgr = get_mgr(request)
    await mgr.resume_job(job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request, job_id: str):
    mgr = get_mgr(request)
    await mgr.cancel_job(job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/events")
async def job_events(request, job_id: str):
    bus = get_bus(request)
    q = await bus.subscribe(job_id)

    async def gen():
        yield f"data: {json.dumps({'type':'hello','job_id':job_id}, ensure_ascii=False)}\n\n"
        while True:
            ev = await q.get()
            yield sse_format(ev)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/library/summary")
async def library_summary(
    request,
    library_root: str | None = Query(default=None),
):
    mgr = get_mgr(request)
    filter_sql, params = _build_path_filter(library_root)
    files_row = mgr.conn.execute(
        f"SELECT COUNT(*) AS cnt FROM files f {filter_sql}",
        params,
    ).fetchone()
    pages_row = mgr.conn.execute(
        f"SELECT COUNT(*) AS cnt FROM pages p JOIN files f ON f.file_id=p.file_id {filter_sql}",
        params,
    ).fetchone()
    artifacts_rows = mgr.conn.execute(
        "SELECT a.kind, a.status, COUNT(*) AS cnt "
        "FROM artifacts a JOIN pages p ON p.page_id=a.page_id "
        "JOIN files f ON f.file_id=p.file_id "
        f"{filter_sql} "
        "GROUP BY a.kind, a.status",
        params,
    ).fetchall()
    artifacts: dict[str, dict[str, int]] = {}
    for r in artifacts_rows:
        kind = str(r["kind"])
        status = str(r["status"])
        artifacts.setdefault(kind, {})[status] = int(r["cnt"])
    return {
        "ok": True,
        "files": int(files_row["cnt"]) if files_row else 0,
        "pages": int(pages_row["cnt"]) if pages_row else 0,
        "artifacts": artifacts,
    }


@router.get("/library/files")
async def library_files(
    request,
    library_root: str | None = Query(default=None),
):
    mgr = get_mgr(request)
    filter_sql, params = _build_path_filter(library_root)
    files_rows = mgr.conn.execute(
        "SELECT f.file_id, f.path, f.size_bytes, f.mtime_epoch, f.slide_count, f.slide_aspect, "
        "f.last_scanned_at, f.scan_error, COUNT(p.page_id) AS page_count "
        "FROM files f LEFT JOIN pages p ON p.file_id=f.file_id "
        f"{filter_sql} "
        "GROUP BY f.file_id ORDER BY f.path",
        params,
    ).fetchall()
    stat_rows = mgr.conn.execute(
        "SELECT f.file_id, a.kind, a.status, COUNT(*) AS cnt "
        "FROM artifacts a JOIN pages p ON p.page_id=a.page_id "
        "JOIN files f ON f.file_id=p.file_id "
        f"{filter_sql} "
        "GROUP BY f.file_id, a.kind, a.status",
        params,
    ).fetchall()
    stats_by_file: dict[int, dict[str, dict[str, int]]] = {}
    for r in stat_rows:
        file_id = int(r["file_id"])
        kind = str(r["kind"])
        status = str(r["status"])
        stats_by_file.setdefault(file_id, {}).setdefault(kind, {})[status] = int(r["cnt"])

    files = []
    for r in files_rows:
        fid = int(r["file_id"])
        files.append(
            {
                "file_id": fid,
                "path": r["path"],
                "size_bytes": r["size_bytes"],
                "mtime_epoch": r["mtime_epoch"],
                "slide_count": r["slide_count"],
                "slide_aspect": r["slide_aspect"],
                "last_scanned_at": r["last_scanned_at"],
                "scan_error": r["scan_error"],
                "page_count": r["page_count"],
                "artifact_stats": stats_by_file.get(fid, {}),
            }
        )
    return {"ok": True, "files": files}


@router.get("/library/files/{file_id}/pages")
async def library_file_pages(request, file_id: int):
    mgr = get_mgr(request)
    pages = mgr.conn.execute(
        "SELECT page_id, page_no, aspect FROM pages WHERE file_id=? ORDER BY page_no",
        (file_id,),
    ).fetchall()
    page_ids = [int(r["page_id"]) for r in pages]
    if not page_ids:
        return {"ok": True, "pages": []}

    placeholders = ",".join("?" for _ in page_ids)
    artifacts_rows = mgr.conn.execute(
        f"SELECT page_id, kind, status FROM artifacts WHERE page_id IN ({placeholders})",
        page_ids,
    ).fetchall()
    texts_rows = mgr.conn.execute(
        f"SELECT page_id, substr(norm_text, 1, 140) AS text_excerpt FROM page_text WHERE page_id IN ({placeholders})",
        page_ids,
    ).fetchall()
    thumb_rows = mgr.conn.execute(
        f"SELECT page_id, image_path, updated_at FROM thumbnails WHERE page_id IN ({placeholders}) "
        "ORDER BY updated_at DESC",
        page_ids,
    ).fetchall()

    artifacts_map: dict[int, dict[str, str]] = {}
    for r in artifacts_rows:
        artifacts_map.setdefault(int(r["page_id"]), {})[str(r["kind"])] = str(r["status"])
    text_map = {int(r["page_id"]): r["text_excerpt"] for r in texts_rows}
    thumb_map: dict[int, str] = {}
    for r in thumb_rows:
        pid = int(r["page_id"])
        if pid not in thumb_map:
            thumb_map[pid] = r["image_path"]

    out = []
    for r in pages:
        pid = int(r["page_id"])
        out.append(
            {
                "page_id": pid,
                "page_no": r["page_no"],
                "aspect": r["aspect"],
                "artifact_status": artifacts_map.get(pid, {}),
                "text_excerpt": text_map.get(pid, ""),
                "thumb_path": thumb_map.get(pid),
            }
        )
    return {"ok": True, "pages": out}


@router.get("/library/pages/{page_id}")
async def library_page(request, page_id: int):
    mgr = get_mgr(request)
    page_row = mgr.conn.execute(
        "SELECT p.page_id, p.file_id, p.page_no, p.aspect, f.path "
        "FROM pages p JOIN files f ON f.file_id=p.file_id WHERE p.page_id=?",
        (page_id,),
    ).fetchone()
    if page_row is None:
        return {"ok": False, "message": "page_not_found"}

    text_row = mgr.conn.execute(
        "SELECT raw_text, norm_text FROM page_text WHERE page_id=?",
        (page_id,),
    ).fetchone()
    artifacts_rows = mgr.conn.execute(
        "SELECT kind, status, error_code, error_message FROM artifacts WHERE page_id=?",
        (page_id,),
    ).fetchall()
    thumb_row = mgr.conn.execute(
        "SELECT image_path FROM thumbnails WHERE page_id=? ORDER BY updated_at DESC LIMIT 1",
        (page_id,),
    ).fetchone()

    artifacts = []
    for r in artifacts_rows:
        artifacts.append(
            {
                "kind": r["kind"],
                "status": r["status"],
                "error_code": r["error_code"],
                "error_message": r["error_message"],
            }
        )

    return {
        "ok": True,
        "page": {
            "page_id": page_row["page_id"],
            "file_id": page_row["file_id"],
            "file_path": page_row["path"],
            "page_no": page_row["page_no"],
            "aspect": page_row["aspect"],
            "raw_text": text_row["raw_text"] if text_row else "",
            "norm_text": text_row["norm_text"] if text_row else "",
            "artifacts": artifacts,
            "thumb_path": thumb_row["image_path"] if thumb_row else None,
        },
    }
