from __future__ import annotations

import json

from fastapi import APIRouter, Body
from fastapi.responses import StreamingResponse

from app.backend_daemon.config import JobOptions
from app.backend_daemon.event_bus import EventBus, sse_format
from app.backend_daemon.job_manager import JobManager

router = APIRouter()


def get_bus(request) -> EventBus:
    return request.app.state.bus


def get_mgr(request) -> JobManager:
    return request.app.state.mgr


@router.post("/jobs/index")
async def start_job(
    request,
    library_root: str = Body(...),
    options: JobOptions = Body(default_factory=JobOptions),
):
    mgr = get_mgr(request)
    job_id = await mgr.create_job(library_root, options)
    return {"job_id": job_id}


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
