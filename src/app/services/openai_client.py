# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class OpenAIConfig:
    responses_model: str = "gpt-4.1"
    embeddings_model: str = "text-embedding-3-small"
    temperature: float = 0.4
    max_output_tokens: int = 2048
    timeout: float = 60.0


class _RateLimiter:
    def __init__(self, rpm: int):
        self._rpm = max(int(rpm), 1)
        self._min_interval = 60.0 / float(self._rpm)
        self._lock = threading.Lock()
        self._last_time = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                wait_for = (self._last_time + self._min_interval) - now
                if wait_for <= 0:
                    self._last_time = now
                    return
            time.sleep(min(wait_for, self._min_interval))


class OpenAIClient:
    def __init__(self, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        rpm = int(os.getenv("OPENAI_RPM", "50") or 50)
        self._rate_limiter = _RateLimiter(rpm) if rpm > 0 else None

    def embed_texts(self, texts: List[str], model: str) -> List[List[float]]:
        """同步 embeddings（逐筆查詢）。"""
        out: List[List[float]] = []
        for text in texts:
            if self._rate_limiter:
                self._rate_limiter.wait()
            resp = self._client.embeddings.create(
                model=model,
                input=text,
            )
            item = (getattr(resp, "data", []) or [None])[0]
            emb = getattr(item, "embedding", None) if item else None
            if emb is None:
                continue
            out.append(emb)
        return out

    async def stream_responses(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> AsyncGenerator[str, None]:
        """Responses API streaming（async generator 包裝）。"""

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Optional[str]] = asyncio.Queue()
        STOP = None

        def worker():
            try:
                if self._rate_limiter:
                    self._rate_limiter.wait()
                stream = self._client.responses.create(
                    model=model,
                    input=messages,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    stream=True,
                    timeout=timeout,
                )
                for event in stream:
                    if cancel_event and cancel_event.is_set():
                        break
                    etype = getattr(event, "type", "") or ""
                    # 依官方文件概念：output_text delta
                    if "output_text" in etype and "delta" in etype:
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            loop.call_soon_threadsafe(q.put_nowait, delta)
            except Exception as e:
                log.error("[OPENAI_ERROR] OpenAI streaming 失敗：%s", e)
                loop.call_soon_threadsafe(q.put_nowait, f"\n[串流錯誤] {e}\n")
            finally:
                loop.call_soon_threadsafe(q.put_nowait, STOP)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await q.get()
            if item is STOP:
                break
            yield item
