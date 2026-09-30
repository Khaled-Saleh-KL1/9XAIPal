import asyncio
import hashlib
import importlib
import json
from unittest.mock import AsyncMock, call

import pytest

from app.services import library_search, retrieval
from app.core.config import settings


_QUESTION = "مين الطلاب اللي بيستخدموا الشبكات العصبية؟"
_UNDERSTANDING = {
    "msa": "ما الطلاب الذين يستخدمون الشبكات العصبية؟",
    "keywords": ["طالب", "طلاب", "شبكة عصبية", "شبكات عصبية"],
    "english": "Which students use neural networks?",
}


def _understanding_api():
    try:
        module = importlib.import_module("app.services.arabic_query_understanding")
    except ModuleNotFoundError:
        pytest.fail("Arabic query understanding service is missing")
    assert callable(getattr(module, "understand_arabic_query", None)), (
        "understand_arabic_query must be available"
    )
    return module


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.ttls[key] = ex


class MappingResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class LanguageSession:
    def __init__(self, documents):
        self.documents = documents
        self.statements = []

    async def execute(self, statement, params):
        self.statements.append((str(statement), params))
        ids = params.get("document_ids") or [params.get("document_id")]
        return MappingResult([doc for doc in self.documents if doc["id"] in ids])


def _hit(chunk_id, document_id):
    return {
        "id": chunk_id,
        "document_id": document_id,
        "sequence_id": 1,
        "markdown": chunk_id,
        "plain_text": chunk_id,
        "page_start": 1,
        "page_end": 1,
        "chunk_type": "text",
        "similarity": 0.7,
    }


def test_arabic_query_understanding_is_enabled_by_default():
    assert settings.arabic_query_understanding_enabled is True


async def test_query_understanding_calls_chat_once_and_caches_for_one_day(monkeypatch):
    module = _understanding_api()
    cache = MemoryRedis()
    monkeypatch.setattr(module, "get_redis", lambda: cache)
    chat = AsyncMock(return_value={"content": json.dumps(_UNDERSTANDING, ensure_ascii=False)})
    monkeypatch.setattr(module.llm_client, "chat", chat)

    first = await module.understand_arabic_query(_QUESTION)
    second = await module.understand_arabic_query(_QUESTION)

    key = f"arabic-query-understanding:{hashlib.sha1(_QUESTION.encode('utf-8')).hexdigest()}"
    assert first == second == _UNDERSTANDING
    assert chat.await_count == 1
    assert cache.values[key] == json.dumps(_UNDERSTANDING, ensure_ascii=False)
    assert cache.ttls[key] == 24 * 60 * 60
    prompt = chat.await_args.args[0][0]["content"].lower()
    assert "preserve the user's intent exactly" in prompt
    assert "do not answer" in prompt
    assert "do not add facts" in prompt
    assert "modern standard arabic" in prompt


async def test_query_understanding_rejects_invalid_json_and_keyword_shapes(monkeypatch):
    module = _understanding_api()
    monkeypatch.setattr(module, "get_redis", lambda: MemoryRedis())
    for content in (
        "not json",
        json.dumps({**_UNDERSTANDING, "keywords": ["طالب", "طلاب"]}, ensure_ascii=False),
        json.dumps({**_UNDERSTANDING, "keywords": ["student", "learner", "person"]}),
        json.dumps({**_UNDERSTANDING, "english": "  "}, ensure_ascii=False),
    ):
        monkeypatch.setattr(module.llm_client, "chat", AsyncMock(return_value={"content": content}))
        assert await module.understand_arabic_query(_QUESTION) is None


async def test_query_understanding_timeout_returns_none_without_raising(monkeypatch):
    module = _understanding_api()
    monkeypatch.setattr(module, "get_redis", lambda: MemoryRedis())
    monkeypatch.setattr(module, "_UNDERSTANDING_TIMEOUT_SECONDS", 0.01)

    async def slow_chat(*_args, **_kwargs):
        await asyncio.Event().wait()

    chat = AsyncMock(side_effect=slow_chat)
    monkeypatch.setattr(module.llm_client, "chat", chat)

    assert await module.understand_arabic_query(_QUESTION) is None
    chat.assert_awaited_once()


async def test_query_understanding_redis_failure_does_not_block_chat(monkeypatch):
    module = _understanding_api()

    class BrokenRedis:
        async def get(self, _key):
            raise ConnectionError("redis unavailable")

        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(module, "get_redis", lambda: BrokenRedis())
    chat = AsyncMock(return_value={"content": json.dumps(_UNDERSTANDING, ensure_ascii=False)})
    monkeypatch.setattr(module.llm_client, "chat", chat)

    assert await module.understand_arabic_query(_QUESTION) == _UNDERSTANDING
    chat.assert_awaited_once()


async def test_arabic_study_retrieval_uses_expansions_keyword_only_and_one_llm_call(monkeypatch):
    arabic_id, english_id = "arabic-paper", "english-paper"
    session = LanguageSession([
        {"id": arabic_id, "text_direction": "rtl", "detected_language": "arabic"},
        {"id": english_id, "text_direction": "ltr", "detected_language": "english"},
    ])
    module = _understanding_api()
    understand = AsyncMock(return_value=_UNDERSTANDING)
    monkeypatch.setattr(retrieval.arabic_query_understanding, "understand_arabic_query", understand)
    monkeypatch.setattr(retrieval, "translated_query", AsyncMock(side_effect=AssertionError("translation duplicates understanding")))
    query_hits = {
        _QUESTION: [_hit("original-hit", arabic_id)],
        _UNDERSTANDING["msa"]: [_hit("msa-hit", arabic_id)],
        _UNDERSTANDING["english"]: [_hit("english-hit", english_id)],
    }
    search = AsyncMock(side_effect=lambda _session, query, *_args: query_hits[query])
    monkeypatch.setattr(retrieval, "_search_chunks_for_query", search)
    keyword = AsyncMock(return_value=[_hit("keyword-hit", arabic_id)])
    monkeypatch.setattr(retrieval, "search_chunks_fulltext", keyword)

    rows = await retrieval.search_chunks(
        session, _QUESTION, limit=10, document_ids=[arabic_id, english_id]
    )

    understand.assert_awaited_once_with(_QUESTION)
    assert [args.args[1] for args in search.await_args_list] == [
        _QUESTION, _UNDERSTANDING["msa"], _UNDERSTANDING["english"]
    ]
    assert search.await_args_list[-1].args[4] == [english_id]
    keyword.assert_awaited_once()
    assert keyword.await_args.args[1] == "طالب طلاب شبكة عصبية شبكات عصبية"
    assert {row["id"] for row in rows} == {
        "original-hit", "msa-hit", "english-hit", "keyword-hit"
    }
    assert all("rrf_score" in row for row in rows)


async def test_english_retrieval_never_calls_query_understanding_or_translation(monkeypatch):
    module = _understanding_api()
    monkeypatch.setattr(
        module.llm_client,
        "chat",
        AsyncMock(side_effect=AssertionError("English/English must not call the LLM")),
    )
    original = _hit("english-hit", "english-paper")
    monkeypatch.setattr(
        retrieval,
        "_search_chunks_for_query",
        AsyncMock(return_value=[original]),
    )
    translate = AsyncMock(side_effect=AssertionError("same-language retrieval must not translate"))
    monkeypatch.setattr(retrieval, "translated_query", translate)

    rows = await retrieval.search_chunks(
        LanguageSession([{
            "id": "english-paper", "text_direction": "ltr", "detected_language": "english"
        }]),
        "Explain neural networks",
        document_id="english-paper",
    )

    assert [row["id"] for row in rows] == ["english-hit"]
    translate.assert_not_awaited()
    module.llm_client.chat.assert_not_awaited()


async def test_library_search_uses_msa_and_english_vectors_and_keyword_only_terms(monkeypatch):
    module = _understanding_api()
    monkeypatch.setattr(library_search, "_backfill_missing_embeddings", AsyncMock())
    understand = AsyncMock(return_value=_UNDERSTANDING)
    monkeypatch.setattr(library_search.arabic_query_understanding, "understand_arabic_query", understand)
    translate = AsyncMock(side_effect=AssertionError("successful understanding replaces translation"))
    monkeypatch.setattr(library_search, "translated_query", translate)

    class DocumentSession:
        async def execute(self, _statement, _params):
            return MappingResult([
                {"id": "arabic-paper", "text_direction": "rtl", "detected_language": "arabic"},
                {"id": "mixed-paper", "text_direction": "ltr", "detected_language": "mixed"},
                {"id": "english-paper", "text_direction": "ltr", "detected_language": "english"},
            ])

    embedding = AsyncMock(side_effect=[[0.1], [0.2], [0.3]])
    monkeypatch.setattr(library_search, "get_query_embedding", embedding)
    semantic = AsyncMock(side_effect=[
        [{"id": "arabic-paper", "similarity": 0.65}],
        [{"id": "mixed-paper", "similarity": 0.62}],
        [{"id": "english-paper", "similarity": 0.71}],
    ])
    monkeypatch.setattr(library_search, "search_documents_semantic", semantic)
    keyword = AsyncMock(return_value=[{"id": "keyword-paper", "fts_rank": 0.2}])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword)

    results = await library_search.semantic_search_documents(
        DocumentSession(), "user-1", _QUESTION, limit=10
    )

    understand.assert_awaited_once_with(_QUESTION)
    assert [item.args[0] for item in embedding.await_args_list] == [
        _QUESTION, _UNDERSTANDING["msa"], _UNDERSTANDING["english"]
    ]
    assert semantic.await_count == 3
    assert semantic.await_args_list[0].kwargs.get("document_ids") is None
    assert semantic.await_args_list[1].kwargs.get("document_ids") is None
    assert semantic.await_args_list[2].kwargs["document_ids"] == ["english-paper"]
    keyword.assert_awaited_once()
    assert keyword.await_args.args[2] == "طالب طلاب شبكة عصبية شبكات عصبية"
    translate.assert_not_awaited()
    assert {row["id"] for row in results} == {
        "arabic-paper", "mixed-paper", "english-paper", "keyword-paper"
    }
