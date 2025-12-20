# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import threading
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


class OpenAIClient:
    def __init__(self, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def embed_texts(self, texts: List[str], model: str) -> List[List[float]]:
        """同步 embeddings。"""
        resp = self._client.embeddings.create(
            model=model,
            input=texts,
        )
        # openai python 回傳 resp.data 是 list，元素有 embedding
        out: List[List[float]] = []
        for item in getattr(resp, "data", []) or []:
            emb = getattr(item, "embedding", None)
            if emb is not None:
                out.append(list(emb))
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
