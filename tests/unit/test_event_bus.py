from __future__ import annotations

import asyncio
import json
import unittest


from tests.helpers import ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.event_bus import Event, EventBus, sse_format


class TestEventBus(unittest.IsolatedAsyncioTestCase):
    async def test_seq_increments_per_job(self) -> None:
        bus = EventBus()
        ev1 = await bus.publish("job1", "t1", {}, ts=1)
        ev2 = await bus.publish("job1", "t2", {}, ts=2)
        ev3 = await bus.publish("job1", "t3", {}, ts=3)
        self.assertEqual([ev1.seq, ev2.seq, ev3.seq], [1, 2, 3])

    async def test_queue_drops_oldest_when_full(self) -> None:
        bus = EventBus()
        await bus.ensure_job("job1")
        bus._queues["job1"] = asyncio.Queue(maxsize=3)
        for i in range(10):
            await bus.publish("job1", "t", {"i": i}, ts=i)
        q = await bus.subscribe("job1")
        seqs = []
        while not q.empty():
            seqs.append((await q.get()).seq)
        self.assertEqual(seqs, [8, 9, 10])

    def test_sse_format(self) -> None:
        ev = Event(ts=1, seq=2, job_id="job", type="hello", payload={"k": "v"})
        out = sse_format(ev)
        self.assertTrue(out.startswith("data: "))
        self.assertTrue(out.endswith("\n\n"))
        payload = json.loads(out[len("data: ") :].strip())
        self.assertEqual(payload["job_id"], "job")
        self.assertEqual(payload["type"], "hello")


if __name__ == "__main__":
    unittest.main()
