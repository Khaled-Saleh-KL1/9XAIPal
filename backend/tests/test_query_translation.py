import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex


async def test_translation_uses_chat_and_is_cached_for_one_day(monkeypatch):
    from app.services import query_translation

    cache = MemoryRedis()
    monkeypatch.setattr(query_translation, "get_redis", lambda: cache)
    chat = AsyncMock(return_value={"content": "attention mechanism"})
    monkeypatch.setattr(query_translation.llm_client, "chat", chat)

    first = await query_translation.translated_query("آلية الانتباه", "english")
    second = await query_translation.translated_query("آلية الانتباه", "english")

    key = f"query-translation:{hashlib.sha1('آلية الانتباه'.encode()).hexdigest()}:english"
    assert first == second == "attention mechanism"
    chat.assert_awaited_once()
    assert cache.values[key] == "attention mechanism"
    assert cache.ttls[key] == 24 * 60 * 60
    messages = chat.await_args.args[0]
    assert "English" in messages[0]["content"]
    assert "technical terms" in messages[0]["content"]
    assert messages[1]["content"] == "آلية الانتباه"


async def test_translation_timeout_returns_none_without_raising(monkeypatch):
    from app.services import query_translation

    monkeypatch.setattr(query_translation, "get_redis", lambda: MemoryRedis())
    monkeypatch.setattr(
        query_translation.llm_client,
        "chat",
        AsyncMock(side_effect=asyncio.TimeoutError),
    )

    assert await query_translation.translated_query("attention", "arabic") is None


async def test_stalled_chat_call_returns_none_at_the_translation_deadline(monkeypatch):
    from app.services import query_translation

    monkeypatch.setattr(query_translation, "get_redis", lambda: MemoryRedis())
    monkeypatch.setattr(query_translation, "_TRANSLATION_TIMEOUT_SECONDS", 0.02)

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    chat = AsyncMock(side_effect=never_finishes)
    monkeypatch.setattr(query_translation.llm_client, "chat", chat)

    result = await asyncio.wait_for(
        query_translation.translated_query("attention", "arabic"), timeout=0.2
    )

    assert result is None
    chat.assert_awaited_once()


async def test_translation_cache_failure_does_not_block_chat(monkeypatch):
    from app.services import query_translation

    class BrokenRedis:
        async def get(self, _key):
            raise ConnectionError("redis unavailable")

        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(query_translation, "get_redis", lambda: BrokenRedis())
    chat = AsyncMock(return_value={"content": "attention"})
    monkeypatch.setattr(query_translation.llm_client, "chat", chat)

    assert await query_translation.translated_query("انتباه", "english") == "attention"
    chat.assert_awaited_once()


@pytest.mark.parametrize("stalled_operation", ["get", "set"])
async def test_stalled_redis_operations_obey_the_translation_deadline(
    monkeypatch, stalled_operation,
):
    from app.services import query_translation

    class StalledRedis:
        async def get(self, _key):
            if stalled_operation == "get":
                await asyncio.Event().wait()
            return None

        async def set(self, *_args, **_kwargs):
            if stalled_operation == "set":
                await asyncio.Event().wait()

    monkeypatch.setattr(query_translation, "get_redis", lambda: StalledRedis())
    monkeypatch.setattr(query_translation, "_TRANSLATION_TIMEOUT_SECONDS", 0.02)
    chat = AsyncMock(return_value={"content": "attention"})
    monkeypatch.setattr(query_translation.llm_client, "chat", chat)

    result = await asyncio.wait_for(
        query_translation.translated_query("انتباه", "english"), timeout=0.2
    )

    assert result is None
    if stalled_operation == "get":
        chat.assert_not_awaited()
    else:
        chat.assert_awaited_once()
