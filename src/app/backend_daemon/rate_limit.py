from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass


@dataclass
class RateState:
    req_tokens: float
    tok_tokens: float
    last_ts: float


class DualTokenBucket:
    def __init__(self, req_per_min: int, tok_per_min: int) -> None:
        self.req_rate = req_per_min / 60.0
        self.tok_rate = tok_per_min / 60.0
        self.req_capacity = float(req_per_min)
        self.tok_capacity = float(tok_per_min)
        now = time.monotonic()
        self.state = RateState(
            req_tokens=self.req_capacity, tok_tokens=self.tok_capacity, last_ts=now
        )
        self._lock = asyncio.Lock()

    async def acquire(self, req_cost: float, tok_cost: float) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.state.last_ts
                self.state.last_ts = now
                self.state.req_tokens = min(
                    self.req_capacity, self.state.req_tokens + elapsed * self.req_rate
                )
                self.state.tok_tokens = min(
                    self.tok_capacity, self.state.tok_tokens + elapsed * self.tok_rate
                )

                if (
                    self.state.req_tokens >= req_cost
                    and self.state.tok_tokens >= tok_cost
                ):
                    self.state.req_tokens -= req_cost
                    self.state.tok_tokens -= tok_cost
                    return

                need_req = max(0.0, req_cost - self.state.req_tokens)
                need_tok = max(0.0, tok_cost - self.state.tok_tokens)
                wait_req = need_req / self.req_rate if self.req_rate > 0 else 0.5
                wait_tok = need_tok / self.tok_rate if self.tok_rate > 0 else 0.5
                wait = max(wait_req, wait_tok, 0.05)

            await asyncio.sleep(min(wait, 2.0))


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 20.0) -> float:
    exp = min(cap, base * (2**attempt))
    return exp * (0.5 + random.random() * 0.5)
