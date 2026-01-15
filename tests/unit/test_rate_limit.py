from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers import ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.rate_limit import DualTokenBucket, backoff_delay

pytestmark = pytest.mark.unit


class TestRateLimit(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_immediate(self) -> None:
        limiter = DualTokenBucket(req_per_min=600, tok_per_min=6000)
        with patch("app.backend_daemon.rate_limit.asyncio.sleep", new=AsyncMock()) as sleeper:
            await limiter.acquire(req_cost=1.0, tok_cost=1.0)
            sleeper.assert_not_called()

    async def test_acquire_waits_without_busy_loop(self) -> None:
        limiter = DualTokenBucket(req_per_min=1, tok_per_min=1)
        current = 0.0

        def fake_monotonic() -> float:
            return current

        async def fake_sleep(_delay: float) -> None:
            nonlocal current
            current += 120.0

        with patch("app.backend_daemon.rate_limit.time.monotonic", new=fake_monotonic), patch(
            "app.backend_daemon.rate_limit.asyncio.sleep", new=fake_sleep
        ):
            await limiter.acquire(req_cost=1.0, tok_cost=1.0)
            await limiter.acquire(req_cost=1.0, tok_cost=1.0)

    def test_backoff_delay_has_jitter(self) -> None:
        delays = [backoff_delay(i) for i in range(4)]
        self.assertTrue(delays[1] > delays[0])
        self.assertTrue(delays[2] >= delays[1])
        self.assertTrue(delays[3] <= 20.0)
        self.assertNotEqual(backoff_delay(1), backoff_delay(1))


if __name__ == "__main__":
    unittest.main()
