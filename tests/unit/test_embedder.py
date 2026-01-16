from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


from tests.helpers import ensure_src_path

ROOT = ensure_src_path()

from app.backend_daemon.embedder import estimate_tokens, zero_vector, embed_text_batch_openai


class TestEmbedder(unittest.IsolatedAsyncioTestCase):
    def test_estimate_tokens_minimum(self) -> None:
        self.assertGreaterEqual(estimate_tokens(""), 1)
        self.assertGreaterEqual(estimate_tokens("hello"), 1)

    def test_zero_vector_length(self) -> None:
        dim = 3072
        blob = zero_vector(dim)
        self.assertEqual(len(blob), dim * 4)

    async def test_embed_text_batch_openai_success(self) -> None:
        limiter = SimpleNamespace(acquire=AsyncMock())

        class FakeEmbeddings:
            def create(self, model: str, input: list[str]):
                return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2]) for _ in input])

        class FakeClient:
            embeddings = FakeEmbeddings()

        with patch("app.backend_daemon.embedder.OpenAI", return_value=FakeClient()):
            vecs = await embed_text_batch_openai(["a", "b"], "m", limiter, max_retries=1)

        self.assertEqual(len(vecs), 2)
        self.assertEqual(vecs[0], [0.1, 0.2])
        limiter.acquire.assert_awaited_once()

    async def test_embed_text_batch_openai_retries(self) -> None:
        limiter = SimpleNamespace(acquire=AsyncMock())
        calls = {"count": 0}

        class FakeEmbeddings:
            def create(self, model: str, input: list[str]):
                calls["count"] += 1
                if calls["count"] < 3:
                    raise RuntimeError("429")
                return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1]) for _ in input])

        class FakeClient:
            embeddings = FakeEmbeddings()

        with patch("app.backend_daemon.embedder.OpenAI", return_value=FakeClient()), patch(
            "app.backend_daemon.embedder.asyncio.sleep", new=AsyncMock()
        ):
            vecs = await embed_text_batch_openai(["a"], "m", limiter, max_retries=5)

        self.assertEqual(calls["count"], 3)
        self.assertEqual(vecs[0], [0.1])

    async def test_embed_text_batch_openai_max_retries_raises(self) -> None:
        limiter = SimpleNamespace(acquire=AsyncMock())
        calls = {"count": 0}

        class FakeEmbeddings:
            def create(self, model: str, input: list[str]):
                calls["count"] += 1
                raise RuntimeError("429")

        class FakeClient:
            embeddings = FakeEmbeddings()

        with patch("app.backend_daemon.embedder.OpenAI", return_value=FakeClient()), patch(
            "app.backend_daemon.embedder.asyncio.sleep", new=AsyncMock()
        ):
            with self.assertRaises(RuntimeError):
                await embed_text_batch_openai(["a"], "m", limiter, max_retries=2)

        self.assertEqual(calls["count"], 3)


if __name__ == "__main__":
    unittest.main()
