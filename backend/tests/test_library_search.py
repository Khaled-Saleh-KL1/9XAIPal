"""Unit tests for services/library_search.py: the library's own semantic
search, distinct from app.services.retrieval (search inside an already-open
document). No DB, no network — every dependency is mocked, matching
test_web_search_cascade.py's approach for the same reason.
"""

from unittest.mock import AsyncMock

import pytest

from app.services import library_search


@pytest.fixture(autouse=True)
def mocked_deps(monkeypatch):
    monkeypatch.setattr(
        library_search.doc_repo, "get_documents_missing_search_embedding", AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(library_search.chunk_repo, "get_lead_text", AsyncMock(return_value=""))
    monkeypatch.setattr(library_search, "get_embeddings_batch", AsyncMock(return_value=[]))
    monkeypatch.setattr(library_search, "set_document_search_embedding", AsyncMock())
    monkeypatch.setattr(library_search, "get_query_embedding", AsyncMock(return_value=[0.1, 0.2]))
    monkeypatch.setattr(library_search, "search_documents_semantic", AsyncMock(return_value=[]))
    monkeypatch.setattr(library_search, "translated_query", AsyncMock(return_value=None), raising=False)
    yield


async def test_backfills_every_document_missing_an_embedding(monkeypatch):
    missing = [
        {"id": "doc-1", "title": "Attention Is All You Need", "original_filename": "1706.03762.pdf"},
        {"id": "doc-2", "title": None, "original_filename": "untitled.pdf"},
    ]
    monkeypatch.setattr(
        library_search.doc_repo, "get_documents_missing_search_embedding", AsyncMock(return_value=missing),
    )
    monkeypatch.setattr(
        library_search.chunk_repo, "get_lead_text",
        AsyncMock(side_effect=["Transformer architecture excerpt.", ""]),
    )
    batch = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])
    monkeypatch.setattr(library_search, "get_embeddings_batch", batch)
    set_embedding = AsyncMock()
    monkeypatch.setattr(library_search, "set_document_search_embedding", set_embedding)

    await library_search.semantic_search_documents(object(), "user-1", "transformers")

    texts = batch.await_args.args[0]
    # Real title used when present, filename as the fallback when it isn't.
    assert texts[0].startswith("Attention Is All You Need\n\nTransformer architecture excerpt.")
    assert texts[1].startswith("untitled.pdf\n\n")
    assert set_embedding.await_count == 2
    assert set_embedding.await_args_list[0].args[1] == "doc-1"
    assert set_embedding.await_args_list[0].args[2] == [0.1, 0.2]
    assert set_embedding.await_args_list[1].args[1] == "doc-2"
    assert set_embedding.await_args_list[1].args[2] == [0.3, 0.4]


async def test_no_backfill_when_nothing_is_missing(monkeypatch):
    batch = AsyncMock(return_value=[])
    monkeypatch.setattr(library_search, "get_embeddings_batch", batch)

    await library_search.semantic_search_documents(object(), "user-1", "transformers")

    batch.assert_not_awaited()


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
    keyword = AsyncMock(return_value=[
        {"id": "doc-d", "fts_rank": 0.2},
        {"id": "doc-b", "fts_rank": 0.1},
    ])
    monkeypatch.setattr(library_search, "search_documents_fulltext", keyword, raising=False)
    session = object()

    results = await library_search.semantic_search_documents(
        session, "user-1", "How does attention work?", limit=4
    )

    translate.assert_awaited_once_with("How does attention work?", "arabic")
    assert query_embed.await_args_list[0].args == ("How does attention work?",)
    assert query_embed.await_args_list[1].args == (translation,)
    assert semantic.await_count == 2
    keyword.assert_awaited_once_with(session, "user-1", translation, limit=15)
    assert [row["id"] for row in results] == ["doc-b", "doc-d", "doc-a", "doc-c"]
    assert results[0] == {"id": "doc-b", "similarity": 0.83}
    assert results[1] == {"id": "doc-d", "similarity": 0.0}


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
    keyword.assert_not_awaited()
    assert fallback == [{"id": "original-doc", "similarity": 0.6}]
