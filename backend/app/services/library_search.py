"""Semantic search over a user's own library: find a document by what it's
about, not just a title substring — the library's own "Search" box, distinct
from app.services.retrieval (search *inside* an already-open document).

A document's search_embedding (title + a short lead excerpt) is computed
lazily, on its first appearance in a search, rather than at ingestion — most
documents are fast-ingested with no whole-document chunk embeddings (see
extraction/pipeline_sync.py::_is_fast_ingest), and this is cheap enough
(one short embedding per document, not per chunk) that piggybacking on a
search beats a separate backfill job or a new ingestion step.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.language import is_primarily_arabic
from app.core.logging import get_logger
from app.database.pgvector import (
    search_documents_fulltext,
    search_documents_semantic,
    set_document_search_embedding,
)
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import documents as doc_repo
from app.embeddings.model import get_embeddings_batch, get_query_embedding
from app.services import arabic_query_understanding
from app.services.query_translation import translated_query
from app.services.retrieval import (
    _document_language,
    _document_supports_english_search,
    reciprocal_rank_fusion,
)

logger = get_logger(__name__)

# A cosine similarity below this is "not what you typed" more often than it
# is a real hit — found empirically against a handful of real queries, not
# derived. The rest of the library (below this line) is still reachable by
# the existing keyword filter, so this only trims the semantic side's own
# tail rather than hiding anything outright.
_MIN_SIMILARITY = 0.3

# Plenty for "title + a paragraph or two" to separate one paper from another;
# well under any embedding model's context limit, so no model-specific
# token counting is needed here.
_MAX_EMBED_CHARS = 2000


async def _lead_text(session: AsyncSession, doc: dict) -> str:
    title = (doc.get("title") or doc.get("original_filename") or "").strip()
    excerpt = await chunk_repo.get_lead_text(session, doc["id"])
    return f"{title}\n\n{excerpt}"[:_MAX_EMBED_CHARS]


async def _backfill_missing_embeddings(session: AsyncSession, user_id: UUID) -> None:
    missing = await doc_repo.get_documents_missing_search_embedding(session, user_id)
    if not missing:
        return
    texts = [await _lead_text(session, doc) for doc in missing]
    embeddings = await get_embeddings_batch(texts)
    for doc, embedding in zip(missing, embeddings):
        await set_document_search_embedding(session, doc["id"], embedding)


async def semantic_search_documents(
    session: AsyncSession, user_id: UUID, query: str, limit: int = 20,
) -> list[dict]:
    """``[{id, similarity}]`` for this user's documents, closest first —
    only those clearing _MIN_SIMILARITY, so a caller can treat every
    returned id as a genuine match rather than re-filtering itself.
    """
    await _backfill_missing_embeddings(session, user_id)
    query_language = "arabic" if is_primarily_arabic(query) else "english"

    if query_language == "arabic" and settings.arabic_query_understanding_enabled:
        understanding = await arabic_query_understanding.understand_arabic_query(query)
        if understanding:
            result = await session.execute(
                text(
                    "SELECT id, text_direction, detected_language FROM documents "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )
            document_rows = result.mappings().all()
            english_ids = [
                row["id"] for row in document_rows
                if _document_supports_english_search(row)
            ]
            search_limit = max(limit * 3, 15)
            query_variants = [query]
            msa = understanding["msa"].strip()
            if msa and msa != query.strip():
                query_variants.append(msa)
            semantic_lists: list[list[dict]] = []
            for variant in query_variants:
                embedding = await get_query_embedding(variant)
                semantic_lists.append(await search_documents_semantic(
                    session, user_id, embedding, limit=search_limit
                ))
            english_query = understanding["english"].strip()
            if english_ids and english_query:
                embedding = await get_query_embedding(english_query)
                semantic_lists.append(await search_documents_semantic(
                    session, user_id, embedding, limit=search_limit,
                    document_ids=english_ids,
                ))

            keyword_results: list[dict] = []
            keywords = " ".join(understanding["keywords"])
            if keywords:
                try:
                    keyword_results = await search_documents_fulltext(
                        session, user_id, keywords, limit=search_limit
                    )
                except Exception:
                    logger.warning("library Arabic keyword search failed", exc_info=True)

            similarity_by_id: dict = {}
            for rows in semantic_lists:
                for row in rows:
                    doc_id = row["id"]
                    similarity_by_id[doc_id] = max(
                        similarity_by_id.get(doc_id, float("-inf")),
                        float(row.get("similarity") or 0.0),
                    )
            semantic_results = reciprocal_rank_fusion(semantic_lists, search_limit)
            semantic_results = [
                {**row, "similarity": similarity_by_id[row["id"]]}
                for row in semantic_results
                if similarity_by_id.get(row["id"], 0.0) >= _MIN_SIMILARITY
            ]
            if semantic_results and keyword_results:
                results = reciprocal_rank_fusion(
                    [semantic_results, keyword_results], limit
                )
            elif semantic_results:
                results = semantic_results[:limit]
            else:
                results = keyword_results[:limit]
            return [
                {
                    "id": row["id"],
                    "similarity": similarity_by_id.get(
                        row["id"], float(row.get("similarity") or 0.0)
                    ),
                }
                for row in results
            ]

    target_language = "english" if query_language == "arabic" else "arabic"
    translated = await translated_query(query, target_language)
    if translated and not translated.strip():
        translated = None

    search_limit = max(limit * 3, 15) if translated else limit
    original_embedding = await get_query_embedding(query)
    original_results = await search_documents_semantic(
        session, user_id, original_embedding, limit=search_limit
    )
    semantic_lists = [original_results]
    if translated:
        try:
            translated_embedding = await get_query_embedding(translated)
            translated_results = await search_documents_semantic(
                session, user_id, translated_embedding, limit=search_limit
            )
            semantic_lists.append(translated_results)
        except Exception:
            logger.warning(
                "translated library semantic search failed; keeping original results",
                exc_info=True,
            )

    if len(semantic_lists) == 1:
        semantic_results = original_results
    else:
        semantic_results = reciprocal_rank_fusion(semantic_lists, search_limit)

    similarity_by_id: dict = {}
    for rows in semantic_lists:
        for row in rows:
            doc_id = row["id"]
            similarity_by_id[doc_id] = max(
                similarity_by_id.get(doc_id, float("-inf")),
                float(row.get("similarity") or 0.0),
            )
    semantic_results = [
        {**row, "similarity": similarity_by_id[row["id"]]}
        for row in semantic_results
        if similarity_by_id.get(row["id"], 0.0) >= _MIN_SIMILARITY
    ]

    keyword_query = query if query_language == "arabic" else translated
    keyword_results: list[dict] = []
    if keyword_query and is_primarily_arabic(keyword_query):
        try:
            keyword_results = await search_documents_fulltext(
                session, user_id, keyword_query, limit=search_limit
            )
        except Exception:
            logger.warning("library Arabic full-text search failed", exc_info=True)

    if semantic_results and keyword_results:
        results = reciprocal_rank_fusion(
            [semantic_results, keyword_results], limit
        )
    elif semantic_results:
        results = semantic_results[:limit]
    else:
        results = keyword_results[:limit]

    return [
        {
            "id": row["id"],
            "similarity": similarity_by_id.get(
                row["id"], float(row.get("similarity") or 0.0)
            ),
        }
        for row in results
    ]
