from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class Event:
    ts: int
    seq: int
    job_id: str
    type: str
    payload: Dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Event]] = {}
        self._seq: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def ensure_job(self, job_id: str) -> None:
        async with self._lock:
            if job_id not in self._queues:
                self._queues[job_id] = asyncio.Queue(maxsize=5000)
                self._seq[job_id] = 0

    async def publish(
        self, job_id: str, type: str, payload: Dict[str, Any], ts: int
    ) -> Event:
        await self.ensure_job(job_id)
        async with self._lock:
            self._seq[job_id] += 1
            seq = self._seq[job_id]
        ev = Event(ts=ts, seq=seq, job_id=job_id, type=type, payload=payload)
        q = self._queues[job_id]
        if q.full():
            try:
                _ = q.get_nowait()
            except Exception:
                pass
        await q.put(ev)
        return ev

    async def subscribe(self, job_id: str) -> asyncio.Queue[Event]:
        await self.ensure_job(job_id)
        return self._queues[job_id]


def sse_format(ev: Event) -> str:
    data = {
        "ts": ev.ts,
        "seq": ev.seq,
        "job_id": ev.job_id,
        "type": ev.type,
        "payload": ev.payload,
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
