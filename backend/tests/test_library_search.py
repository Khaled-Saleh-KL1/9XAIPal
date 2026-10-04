"""Unit tests for services/library_search.py: the library's own semantic
search, distinct from app.services.retrieval (search inside an already-open
document). No DB, no network — every dependency is mocked, matching
test_web_search_cascade.py's approach for the same reason.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import httpx
import pytest

from app.database.repositories import chunks as chunk_repo
from app.database.repositories import documents as doc_repo
from app.services import library_search


@pytest.fixture(autouse=True)
def mocked_deps(monkeypatch):
    monkeypatch.setattr(library_search, "get_query_embedding", AsyncMock(return_value=[0.1, 0.2]))
    monkeypatch.setattr(library_search, "search_documents_semantic", AsyncMock(return_value=[]))
    monkeypatch.setattr(library_search, "translated_query", AsyncMock(return_value=None), raising=False)
    yield


async def test_library_search_never_generates_document_embeddings_inline(monkeypatch):
    missing = [{"id": "doc-1", "title": "Attention Is All You Need"}]
    find_missing = AsyncMock(return_value=missing)
    monkeypatch.setattr(doc_repo, "get_documents_missing_search_embedding", find_missing)
    monkeypatch.setattr(chunk_repo, "get_lead_text", AsyncMock(return_value="lead"))
    monkeypatch.setattr(
        library_search, "set_document_search_embedding", AsyncMock(), raising=False
    )
    batch = AsyncMock(return_value=[[0.1, 0.2]])
    monkeypatch.setattr(library_search, "get_embeddings_batch", batch, raising=False)
    await library_search.semantic_search_documents(object(), "user-1", "transformers")
    batch.assert_not_awaited()
    find_missing.assert_not_awaited()


async def test_query_is_embedded_and_passed_to_the_vector_search(monkeypatch):
    query_embed = AsyncMock(return_value=[0.5, 0.6])
    monkeypatch.setattr(library_search, "get_query_embedding", query_embed)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(library_search, "search_documents_semantic", search)

    await library_search.semantic_search_documents(object(), "user-1", "diffusion models", limit=7)

    query_embed.assert_awaited_once_with("diffusion models")
    assert search.await_args.args[2] == [0.5, 0.6]
    assert search.await_args.kwargs.get("limit") == 7


async def test_similarity_threshold_drops_weak_matches(monkeypatch):
    monkeypatch.setattr(
        library_search, "search_documents_semantic",
        AsyncMock(return_value=[
            {"id": "doc-1", "similarity": 0.62},
            {"id": "doc-2", "similarity": library_search._MIN_SIMILARITY},   # boundary: kept
            {"id": "doc-3", "similarity": library_search._MIN_SIMILARITY - 0.01},  # just under: dropped
        ]),
    )

    results = await library_search.semantic_search_documents(object(), "user-1", "transformers")

    assert [r["id"] for r in results] == ["doc-1", "doc-2"]


async def test_library_search_translates_and_fuses_semantic_and_arabic_keyword_hits(monkeypatch):
    translation = "آلية الانتباه"
    translate = AsyncMock(return_value=translation)
    monkeypatch.setattr(library_search, "translated_query", translate, raising=False)
    query_embed = AsyncMock(side_effect=[[0.1], [0.2]])
    monkeypatch.setattr(library_search, "get_query_embedding", query_embed)
    semantic = AsyncMock(side_effect=[
        [
            {"id": "doc-a", "similarity": 0.52},
            {"id": "doc-b", "similarity": 0.41},
        ],
        [
            {"id": "doc-b", "similarity": 0.83},
            {"id": "doc-c", "similarity": 0.61},
        ],
    ])
    monkeypatch.setattr(library_search, "search_documents_semantic", semantic)
    keyword_rows = [
        {"id": "doc-d", "fts_rank": 0.2},
        {"id": "doc-b", "fts_rank": 0.1},
    ]
    keyword = AsyncMock(side_effect=[[], keyword_rows])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword, raising=False)
    session = object()

    results = await library_search.semantic_search_documents(
        session, "user-1", "How does attention work?", limit=4
    )

    translate.assert_awaited_once_with("How does attention work?", "arabic")
    assert query_embed.await_args_list[0].args == ("How does attention work?",)
    assert query_embed.await_args_list[1].args == (translation,)
    assert semantic.await_count == 2
    keyword.assert_has_awaits([
        call(
            session, "user-1", "How does attention work?", limit=15,
            missing_vectors_only=True,
        ),
        call(session, "user-1", translation, limit=15),
    ])
    assert [row["id"] for row in results] == ["doc-b", "doc-d", "doc-a", "doc-c"]
    assert results == [
        {"id": "doc-b", "similarity": 0.83},
        {"id": "doc-d", "similarity": 0.0},
        {"id": "doc-a", "similarity": 0.52},
        {"id": "doc-c", "similarity": 0.61},
    ]


async def test_healthy_english_search_merges_fts_hits_from_documents_without_vectors(
    monkeypatch,
):
    query = "neural attention"
    monkeypatch.setattr(library_search, "translated_query", AsyncMock(return_value=None))
    monkeypatch.setattr(
        library_search,
        "search_documents_semantic",
        AsyncMock(return_value=[{"id": "vector-doc", "similarity": 0.72}]),
    )
    calls = []

    async def fulltext(_session, _user_id, term, limit, *, missing_vectors_only=False):
        calls.append((term, limit, missing_vectors_only))
        if term == query and missing_vectors_only:
            return [{"id": "fts-only-doc", "fts_rank": 0.8}]
        return []

    monkeypatch.setattr(library_search, "search_documents_fulltext", fulltext)

    results = await library_search.semantic_search_documents(object(), "user-1", query)

    assert calls == [(query, 20, True)]
    assert [row["id"] for row in results] == ["vector-doc", "fts-only-doc"]
    assert results[0] == {"id": "vector-doc", "similarity": 0.72}
    assert results[1] == {"id": "fts-only-doc", "similarity": 0.0}


async def test_healthy_arabic_understanding_keeps_original_language_fts_for_vectorless_docs(
    monkeypatch,
):
    from app.core.config import settings

    query = "التعلم العميق"
    monkeypatch.setattr(settings, "arabic_query_understanding_enabled", True)
    monkeypatch.setattr(
        library_search.arabic_query_understanding,
        "understand_arabic_query",
        AsyncMock(return_value={
            "msa": "التعلّم العميق",
            "english": "deep learning",
            "keywords": ["تعلم", "عميق"],
        }),
    )

    class EmptyRows:
        def mappings(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, *_args, **_kwargs):
            return EmptyRows()

    monkeypatch.setattr(
        library_search, "get_query_embedding", AsyncMock(return_value=[0.1, 0.2])
    )
    monkeypatch.setattr(
        library_search, "search_documents_semantic", AsyncMock(return_value=[])
    )
    calls = []

    async def fulltext(_session, _user_id, term, limit, *, missing_vectors_only=False):
        calls.append((term, missing_vectors_only))
        if term == query and missing_vectors_only:
            return [{"id": "arabic-fts-only", "fts_rank": 0.9}]
        return []

    monkeypatch.setattr(library_search, "search_documents_fulltext", fulltext)

    results = await library_search.semantic_search_documents(Session(), "user-1", query)

    assert (query, True) in calls
    assert results == [{"id": "arabic-fts-only", "similarity": 0.0}]


async def test_arabic_library_search_uses_original_for_keyword_and_falls_back_on_translation_failure(
    monkeypatch,
):
    query = "الشبكات العصبية"
    translate = AsyncMock(return_value="neural networks")
    monkeypatch.setattr(library_search, "translated_query", translate, raising=False)
    query_embed = AsyncMock(side_effect=[[0.1], [0.2]])
    monkeypatch.setattr(library_search, "get_query_embedding", query_embed)
    monkeypatch.setattr(
        library_search,
        "search_documents_semantic",
        AsyncMock(side_effect=[[], [{"id": "translated-doc", "similarity": 0.7}]]),
    )
    keyword = AsyncMock(return_value=[{"id": "keyword-doc", "fts_rank": 0.2}])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword, raising=False)
    session = object()

    results = await library_search.semantic_search_documents(session, "user-2", query)

    translate.assert_awaited_once_with(query, "english")
    keyword.assert_awaited_once_with(session, "user-2", query, limit=60)
    assert {row["id"] for row in results} == {"translated-doc", "keyword-doc"}

    translate_failure = AsyncMock(return_value=None)
    monkeypatch.setattr(library_search, "translated_query", translate_failure, raising=False)
    query_embed = AsyncMock(return_value=[0.3])
    monkeypatch.setattr(library_search, "get_query_embedding", query_embed)
    semantic = AsyncMock(return_value=[{"id": "original-doc", "similarity": 0.6}])
    monkeypatch.setattr(library_search, "search_documents_semantic", semantic)
    keyword = AsyncMock(return_value=[])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword, raising=False)

    fallback = await library_search.semantic_search_documents(session, "user-2", "attention")

    translate_failure.assert_awaited_once_with("attention", "arabic")
    query_embed.assert_awaited_once_with("attention")
    semantic.assert_awaited_once()
    keyword.assert_awaited_once_with(
        session, "user-2", "attention", limit=20, missing_vectors_only=True
    )
    assert fallback == [{"id": "original-doc", "similarity": 0.6}]


@pytest.mark.parametrize("failure_stage", ["embedding", "semantic-search"])
async def test_translated_library_leg_failure_preserves_original_results(
    monkeypatch, failure_stage,
):
    monkeypatch.setattr(
        library_search,
        "translated_query",
        AsyncMock(return_value="آلية الانتباه"),
        raising=False,
    )
    if failure_stage == "embedding":
        query_embed = AsyncMock(side_effect=[[0.1], RuntimeError("embedding unavailable")])
        semantic = AsyncMock(return_value=[{"id": "original-doc", "similarity": 0.6}])
    else:
        query_embed = AsyncMock(side_effect=[[0.1], [0.2]])
        semantic = AsyncMock(side_effect=[
            [{"id": "original-doc", "similarity": 0.6}],
            RuntimeError("translated search unavailable"),
        ])
    monkeypatch.setattr(library_search, "get_query_embedding", query_embed)
    monkeypatch.setattr(library_search, "search_documents_semantic", semantic)
    keyword = AsyncMock(return_value=[])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword, raising=False)

    results = await library_search.semantic_search_documents(object(), "user-3", "attention")

    assert results == [{"id": "original-doc", "similarity": 0.6}]


@pytest.mark.parametrize(
    ("query", "translation", "understanding_enabled", "expected_terms"),
    [
        ("transformer attention", "انتباه المحول", False,
         ["transformer attention", "انتباه المحول"]),
        ("شبكات عصبية", "neural networks", True,
         ["شبكات عصبية", "الشبكات العصبية", "neural networks"]),
    ],
)
async def test_embedding_failure_returns_merged_fts_results_over_http(
    monkeypatch, query, translation, understanding_enabled, expected_terms,
):
    from app.api.deps import get_current_user, get_db
    from app.api.errors import ModelUnavailable
    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(
        settings, "arabic_query_understanding_enabled", understanding_enabled
    )
    if understanding_enabled:
        monkeypatch.setattr(
            library_search.arabic_query_understanding,
            "understand_arabic_query",
            AsyncMock(return_value={
                "msa": "الشبكات العصبية",
                "english": translation,
                "keywords": ["شبكات", "عصبية"],
            }),
        )
    monkeypatch.setattr(library_search, "translated_query", AsyncMock(return_value=translation))
    monkeypatch.setattr(
        library_search, "get_query_embedding",
        AsyncMock(side_effect=ModelUnavailable("embedding unavailable")),
    )
    searched = []

    async def fulltext(_session, _user_id, term, limit):
        searched.append(term)
        return [{"id": f"hit-{len(searched)}", "fts_rank": 0.5}]

    monkeypatch.setattr(library_search, "search_documents_fulltext", fulltext)

    class EmptyRows:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeSession:
        async def execute(self, *_args, **_kwargs):
            return EmptyRows()

    async def fake_db():
        return FakeSession()

    async def fake_user():
        return {"id": "user-1"}

    monkeypatch.setitem(app.dependency_overrides, get_db, fake_db)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, fake_user)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/papers/search", params={"q": query})

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [
        f"hit-{index}" for index in range(1, len(expected_terms) + 1)
    ]
    assert searched == expected_terms


@pytest.mark.parametrize("query", ["transformer attention", "شبكات عصبية"])
async def test_query_embedding_timeout_cancels_work_and_uses_fts(monkeypatch, query):
    monkeypatch.setattr(
        library_search,
        "settings",
        SimpleNamespace(
            query_embedding_timeout_s=0.02,
            arabic_query_understanding_enabled=False,
        ),
    )
    cancelled = asyncio.Event()

    async def blocked_embedding(_query):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(library_search, "translated_query", AsyncMock(return_value=None))
    monkeypatch.setattr(library_search, "get_query_embedding", blocked_embedding)
    keyword = AsyncMock(return_value=[{"id": "fts-hit", "fts_rank": 0.4}])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword)
    batch = AsyncMock()
    monkeypatch.setattr(library_search, "get_embeddings_batch", batch, raising=False)

    started = asyncio.get_running_loop().time()
    try:
        results = await asyncio.wait_for(
            library_search.semantic_search_documents(
                object(), "user-1", query
            ),
            timeout=0.2,
        )
    except asyncio.TimeoutError:
        pytest.fail("search did not enforce query_embedding_timeout_s")

    assert asyncio.get_running_loop().time() - started < 0.1
    assert cancelled.is_set()
    assert results == [{"id": "fts-hit", "similarity": 0.0}]
    batch.assert_not_awaited()
    keyword.assert_awaited_once()


async def test_arabic_understanding_embedding_failure_uses_all_ready_terms(monkeypatch):
    from app.core.config import settings
    from app.api.errors import ModelUnavailable

    monkeypatch.setattr(settings, "arabic_query_understanding_enabled", True)
    understanding = {
        "msa": "الشبكات العصبية",
        "english": "neural networks",
        "keywords": ["شبكات", "عصبية"],
    }
    monkeypatch.setattr(
        library_search.arabic_query_understanding,
        "understand_arabic_query", AsyncMock(return_value=understanding),
    )

    class EmptyRows:
        def mappings(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, *_args, **_kwargs):
            return EmptyRows()

    monkeypatch.setattr(
        library_search, "get_query_embedding",
        AsyncMock(side_effect=ModelUnavailable("embedding unavailable")),
    )
    terms = []

    async def fulltext(_session, _user_id, term, limit):
        terms.append(term)
        return [{"id": term, "fts_rank": 1.0}]

    monkeypatch.setattr(library_search, "search_documents_fulltext", fulltext)

    results = await library_search.semantic_search_documents(
        Session(), "user-1", "شبكات عصبية"
    )

    assert terms == ["شبكات عصبية", "الشبكات العصبية", "neural networks"]
    assert results
