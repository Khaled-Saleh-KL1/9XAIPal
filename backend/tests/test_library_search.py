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
