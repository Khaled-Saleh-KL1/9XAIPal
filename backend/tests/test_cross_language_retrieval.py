from unittest.mock import AsyncMock

import pytest

from app.services import retrieval


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


def _hit(chunk_id, document_id, rank):
    return {
        "id": chunk_id,
        "document_id": document_id,
        "sequence_id": rank,
        "markdown": chunk_id,
        "plain_text": chunk_id,
        "page_start": rank,
        "page_end": rank,
        "chunk_type": "text",
        "similarity": 1.0 - rank / 10,
    }


def _configure_search(monkeypatch, query_hits):
    embedding_queries = []
    fulltext_queries = []
    vector_scopes = []

    async def embed(query):
        embedding_queries.append(query)
        return [float(len(embedding_queries))]

    async def vector_search(_session, embedding, **kwargs):
        vector_scopes.append((int(embedding[0]), kwargs))
        return query_hits[embedding[0]]["vector"]

    async def fulltext_search(_session, query, **kwargs):
        fulltext_queries.append((query, kwargs))
        return query_hits[float(embedding_queries.index(query) + 1)]["fulltext"]

    monkeypatch.setattr(retrieval, "get_query_embedding", embed)
    monkeypatch.setattr(retrieval.emb_repo, "search_embeddings", vector_search)
    monkeypatch.setattr(retrieval, "search_chunks_fulltext", fulltext_search)
    return embedding_queries, fulltext_queries, vector_scopes


@pytest.mark.parametrize(
    ("query", "doc", "translation", "target"),
    [
        (
            "ما آلية الانتباه؟",
            {"id": "english-doc", "text_direction": "ltr", "detected_language": "english"},
            "attention mechanism",
            "english",
        ),
        (
            "ما آلية الانتباه؟",
            {"id": "unknown-doc", "text_direction": None, "detected_language": None},
            "attention mechanism",
            "english",
        ),
        (
            "How does attention work?",
            {"id": "arabic-doc", "text_direction": "rtl", "detected_language": "arabic"},
            "كيف تعمل آلية الانتباه؟",
            "arabic",
        ),
    ],
)
async def test_opposite_language_retrieval_searches_and_fuses_both_queries(
    monkeypatch, query, doc, translation, target,
):
    doc_id = doc["id"]
    query_hits = {
        1.0: {
            "vector": [_hit("a", doc_id, 1), _hit("b", doc_id, 2)],
            "fulltext": [_hit("a", doc_id, 1), _hit("b", doc_id, 2)],
        },
        2.0: {
            "vector": [_hit("b", doc_id, 1), _hit("c", doc_id, 2)],
            "fulltext": [_hit("b", doc_id, 1), _hit("c", doc_id, 2)],
        },
    }
    embedding_queries, fulltext_queries, _ = _configure_search(monkeypatch, query_hits)
    translate = AsyncMock(return_value=translation)
    monkeypatch.setattr(retrieval, "translated_query", translate, raising=False)

    rows = await retrieval.search_chunks(LanguageSession([doc]), query, limit=3, document_id=doc_id)

    translate.assert_awaited_once_with(query, target)
    assert embedding_queries == [query, translation]
    assert [q for q, _ in fulltext_queries] == [query, translation]
    assert [row["id"] for row in rows] == ["b", "a", "c"]
    assert rows[0]["rrf_score"] > rows[1]["rrf_score"] > rows[2]["rrf_score"]


async def test_same_language_retrieval_uses_only_original_query_and_keeps_result(monkeypatch):
    doc_id = "english-doc"
    original = _hit("original", doc_id, 1)
    query_hits = {
        1.0: {"vector": [original], "fulltext": [original]},
    }
    embedding_queries, fulltext_queries, _ = _configure_search(monkeypatch, query_hits)
    translate = AsyncMock(return_value="translated")
    monkeypatch.setattr(retrieval, "translated_query", translate, raising=False)

    rows = await retrieval.search_chunks(
        LanguageSession([{"id": doc_id, "text_direction": "ltr", "detected_language": "english"}]),
        "Explain attention",
        document_id=doc_id,
    )

    translate.assert_not_awaited()
    assert embedding_queries == ["Explain attention"]
    assert [q for q, _ in fulltext_queries] == ["Explain attention"]
    assert [row["id"] for row in rows] == ["original"]
    assert rows[0]["rrf_score"] == pytest.approx(2 / 61)


async def test_arabic_question_on_arabic_document_does_not_translate(monkeypatch):
    doc_id = "arabic-doc"
    query = "ما آلية الانتباه؟"
    original = _hit("original", doc_id, 1)
    query_hits = {1.0: {"vector": [original], "fulltext": [original]}}
    embedding_queries, fulltext_queries, _ = _configure_search(monkeypatch, query_hits)
    translate = AsyncMock(return_value="attention mechanism")
    monkeypatch.setattr(retrieval, "translated_query", translate, raising=False)

    rows = await retrieval.search_chunks(
        LanguageSession([{"id": doc_id, "text_direction": "rtl", "detected_language": "english"}]),
        query,
        document_id=doc_id,
    )

    translate.assert_not_awaited()
    assert embedding_queries == [query]
    assert [q for q, _ in fulltext_queries] == [query]
    assert [row["id"] for row in rows] == ["original"]


async def test_translation_failure_keeps_original_retrieval_results(monkeypatch):
    doc_id = "english-doc"
    original = _hit("original", doc_id, 1)
    query_hits = {1.0: {"vector": [original], "fulltext": [original]}}
    embedding_queries, fulltext_queries, _ = _configure_search(monkeypatch, query_hits)
    monkeypatch.setattr(retrieval, "translated_query", AsyncMock(return_value=None), raising=False)

    rows = await retrieval.search_chunks(
        LanguageSession([{"id": doc_id, "text_direction": "ltr", "detected_language": "english"}]),
        "ما آلية الانتباه؟",
        document_id=doc_id,
    )

    assert embedding_queries == ["ما آلية الانتباه؟"]
    assert [q for q, _ in fulltext_queries] == ["ما آلية الانتباه؟"]
    assert [row["id"] for row in rows] == ["original"]


async def test_study_retrieval_translates_only_for_opposite_language_documents(monkeypatch):
    english_doc = {"id": "english", "text_direction": "ltr", "detected_language": "english"}
    arabic_doc = {"id": "arabic", "text_direction": None, "detected_language": "mixed"}
    query_hits = {
        1.0: {"vector": [], "fulltext": []},
        2.0: {"vector": [], "fulltext": []},
    }
    embedding_queries, fulltext_queries, vector_scopes = _configure_search(monkeypatch, query_hits)
    translate = AsyncMock(return_value="كيف تعمل آلية الانتباه؟")
    monkeypatch.setattr(retrieval, "translated_query", translate, raising=False)

    await retrieval.search_chunks(
        LanguageSession([english_doc, arabic_doc]),
        "How does attention work?",
        document_ids=["english", "arabic"],
    )

    translate.assert_awaited_once_with("How does attention work?", "arabic")
    assert embedding_queries == ["How does attention work?", "كيف تعمل آلية الانتباه؟"]
    assert vector_scopes[0][1]["document_ids"] == ["english", "arabic"]
    assert vector_scopes[1][1]["document_ids"] == ["arabic"]
    assert fulltext_queries[0][1]["document_ids"] == ["english", "arabic"]
    assert fulltext_queries[1][1]["document_ids"] == ["arabic"]
