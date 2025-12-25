from __future__ import annotations

import asyncio
import importlib
import importlib.util
import struct
from typing import List

if importlib.util.find_spec("openai") is None:
    class OpenAI:  # type: ignore[override]
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ModuleNotFoundError("openai is required for embeddings")
else:
    OpenAI = importlib.import_module("openai").OpenAI

from app.backend_daemon.rate_limit import DualTokenBucket, backoff_delay


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 4 * 1.2))


def pack_f32(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def zero_vector(dim: int) -> bytes:
    return pack_f32([0.0] * dim)


async def embed_text_batch_openai(
    texts: List[str],
    model: str,
    limiter: DualTokenBucket,
    max_retries: int,
) -> List[List[float]]:
    client = OpenAI()

    tok_cost = sum(estimate_tokens(t) for t in texts)
    await limiter.acquire(req_cost=1.0, tok_cost=float(tok_cost))

    attempt = 0
    while True:
        try:
            resp = client.embeddings.create(model=model, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as exc:
            if attempt >= max_retries:
                raise
            await asyncio.sleep(backoff_delay(attempt))
            attempt += 1
