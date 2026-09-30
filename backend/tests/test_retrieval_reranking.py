import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services import retrieval


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex


def _candidates(count=3):
    return [
        {
            "id": f"chunk-{index}",
            "plain_text": f"body {index} " + ("x" * 700),
            "heading_path": f"section {index}",
        }
        for index in range(1, count + 1)
    ]


def test_reranking_defaults_are_scoped_to_arabic():
    assert settings.rerank_arabic_enabled is True
    assert settings.rerank_english_enabled is False
    assert settings.rerank_candidates == 30


async def test_reranker_orders_and_caches_numbered_candidates(monkeypatch):
    from app.services import retrieval_reranking as reranker

    redis = MemoryRedis()
    monkeypatch.setattr(reranker, "get_redis", lambda: redis)
    chat = AsyncMock(return_value={"content": "[2, 1, 3]"})
    monkeypatch.setattr(reranker.llm_client, "chat", chat)
    candidates = _candidates()

    first = await reranker.rerank_chunks(
        object(), "ما هي الشبكات؟", candidates, 2,
        is_arabic_query=True, has_arabic_target=True,
    )
    second = await reranker.rerank_chunks(
        object(), "ما هي الشبكات؟", candidates, 2,
        is_arabic_query=True, has_arabic_target=True,
    )

    digest = hashlib.sha1(
        "ما هي الشبكات؟\0chunk-1\0chunk-2\0chunk-3".encode("utf-8")
    ).hexdigest()
    key = f"retrieval-rerank:{digest}"
    assert [row["id"] for row in first] == ["chunk-2", "chunk-1"]
    assert [row["id"] for row in second] == ["chunk-2", "chunk-1"]
    assert chat.await_count == 1
    assert redis.ttls[key] == 60 * 60
    system_prompt, candidate_prompt = [
        message["content"] for message in chat.await_args.args[0]
    ]
    assert "section 1" in candidate_prompt
    assert "body 1 " in candidate_prompt
    assert "return only a json list" in system_prompt.lower()
    assert "only if the list still contains" in system_prompt.lower()
    assert all(
        len(line.split("Text: ", 1)[1]) <= 600
        for line in candidate_prompt.splitlines()
        if "Text: " in line
    )


@pytest.mark.parametrize("content", ["not json", "[1, 1]", "[0, 2]", "[2]"])
async def test_invalid_or_too_short_reranking_keeps_fused_order(monkeypatch, content):
    from app.services import retrieval_reranking as reranker

    monkeypatch.setattr(reranker, "get_redis", lambda: MemoryRedis())
    monkeypatch.setattr(
        reranker.llm_client, "chat", AsyncMock(return_value={"content": content})
    )
    candidates = _candidates()

    result = await reranker.rerank_chunks(
        object(), "الشبكات", candidates, 2,
        is_arabic_query=True, has_arabic_target=True,
    )

    assert [row["id"] for row in result] == ["chunk-1", "chunk-2"]


async def test_reranker_timeout_and_english_default_skip_llm(monkeypatch):
    from app.services import retrieval_reranking as reranker

    monkeypatch.setattr(reranker, "get_redis", lambda: MemoryRedis())
    monkeypatch.setattr(reranker, "_RERANK_TIMEOUT_SECONDS", 0.01)

    async def slow_chat(*_args, **_kwargs):
        await asyncio.Event().wait()

    chat = AsyncMock(side_effect=slow_chat)
    monkeypatch.setattr(reranker.llm_client, "chat", chat)
    candidates = _candidates()
    timed_out = await reranker.rerank_chunks(
        object(), "سؤال عربي", candidates, 2,
        is_arabic_query=True, has_arabic_target=False,
    )
    english = await reranker.rerank_chunks(
        object(), "English question", candidates, 2,
        is_arabic_query=False, has_arabic_target=False,
    )

    assert timed_out == candidates[:2]
    assert english == candidates[:2]
    chat.assert_awaited_once()


async def test_retrieval_requests_rerank_candidate_pool_but_english_path_stays_untouched(
    monkeypatch,
):
    from app.services import retrieval_reranking as reranker

    arabic_rows = _candidates(30)
    monkeypatch.setattr(
        retrieval.arabic_query_understanding,
        "understand_arabic_query",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(retrieval, "_search_chunks_for_query", AsyncMock(return_value=arabic_rows))
    rerank = AsyncMock(side_effect=lambda _session, _query, rows, limit, **_kwargs: rows[:limit])
    monkeypatch.setattr(reranker, "rerank_chunks", rerank)
    arabic_session = type("Session", (), {
        "execute": AsyncMock(return_value=type("Result", (), {
            "mappings": lambda self: self,
            "all": lambda self: [{
                "id": "doc-ar", "text_direction": "rtl", "detected_language": "arabic"
            }],
        })())
    })()

    rows = await retrieval.search_chunks(arabic_session, "سؤال عربي", limit=5, document_id="doc-ar")

    assert len(rows) == 5
    assert rerank.await_args.args[3] == 5
    assert len(rerank.await_args.args[2]) >= 30
    rerank.reset_mock()
    english_session = type("Session", (), {
        "execute": AsyncMock(return_value=type("Result", (), {
            "mappings": lambda self: self,
            "all": lambda self: [{
                "id": "doc-en", "text_direction": "ltr", "detected_language": "english"
            }],
        })())
    })()
    monkeypatch.setattr(retrieval, "_search_chunks_for_query", AsyncMock(return_value=arabic_rows))

    await retrieval.search_chunks(
        english_session, "English question", limit=5, document_id="doc-en"
    )

    rerank.assert_not_awaited()


async def test_english_question_for_arabic_document_uses_arabic_reranking(monkeypatch):
    from app.services import retrieval_reranking as reranker

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [{
                "id": "doc-ar", "text_direction": "rtl", "detected_language": "arabic"
            }]

    class Session:
        async def execute(self, *_args):
            return Result()

    candidates = _candidates(30)
    search = AsyncMock(return_value=candidates)
    monkeypatch.setattr(retrieval, "_search_chunks_for_query", search)
    monkeypatch.setattr(retrieval, "translated_query", AsyncMock(return_value="سؤال عربي"))
    rerank = AsyncMock(side_effect=lambda _session, _query, rows, limit, **_kwargs: rows[:limit])
    monkeypatch.setattr(reranker, "rerank_chunks", rerank)

    results = await retrieval.search_chunks(
        Session(), "English question", limit=5, document_id="doc-ar"
    )

    assert len(results) == 5
    assert rerank.await_count == 1
    assert rerank.await_args.kwargs == {
        "is_arabic_query": False,
        "has_arabic_target": True,
    }
