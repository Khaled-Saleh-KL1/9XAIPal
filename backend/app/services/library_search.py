"""Semantic search over a user's own library: find a document by what it's
about, not just a title substring — the library's own "Search" box, distinct
from app.services.retrieval (search *inside* an already-open document).

Document search embeddings are generated in background ingestion work, so
library search stays independent of bulk document embedding.
"""

import asyncio
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.language import is_primarily_arabic
from app.core.logging import get_logger
from app.database.pgvector import (
    search_documents_fulltext,
    search_documents_semantic,
)
from app.embeddings.model import get_query_embedding
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


async def _embed_query(query: str) -> list[float]:
    """Bound only the model call; vector and full-text database work is separate."""
    async with asyncio.timeout(settings.query_embedding_timeout_s):
        return await get_query_embedding(query)


async def _fulltext_fallback(
    session: AsyncSession,
    user_id: UUID,
    query_terms: list[str],
    limit: int,
    prior_ranked_lists: list[list[dict]] | None = None,
) -> list[dict]:
    """Fuse keyword lists for all query forms available before an embed failed."""
    terms = list(dict.fromkeys(
        term.strip() for term in query_terms if isinstance(term, str) and term.strip()
    ))
    fetch_limit = max(limit * 3, 15) if len(terms) > 1 else limit
    ranked_lists = [rows for rows in (prior_ranked_lists or []) if rows]
    for term in terms:
        try:
            rows = await search_documents_fulltext(
                session, user_id, term, limit=fetch_limit
            )
        except Exception:
            logger.warning(
                "library full-text fallback failed for one query form", exc_info=True
            )
            continue
        if rows:
            ranked_lists.append(rows)

    if not ranked_lists:
        return []

    similarity_by_id: dict = {}
    for rows in ranked_lists:
        for row in rows:
            if "similarity" in row:
                similarity_by_id[row["id"]] = max(
                    similarity_by_id.get(row["id"], float("-inf")),
                    float(row.get("similarity") or 0.0),
                )
    results = (
        reciprocal_rank_fusion(ranked_lists, limit)
        if len(ranked_lists) > 1
        else ranked_lists[0][:limit]
    )
    return [
        {
            "id": row["id"],
            "similarity": similarity_by_id.get(
                row["id"], float(row.get("similarity") or 0.0)
            ),
        }
        for row in results
    ]


async def _degraded_search(
    session: AsyncSession,
    user_id: UUID,
    query_terms: list[str],
    limit: int,
    error: Exception,
    prior_ranked_lists: list[list[dict]] | None = None,
) -> list[dict]:
    logger.warning(
        "library query embedding failed; degrading to full-text (%s)",
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )
    return await _fulltext_fallback(
        session, user_id, query_terms, limit, prior_ranked_lists
    )


async def semantic_search_documents(
    session: AsyncSession, user_id: UUID, query: str, limit: int = 20,
) -> list[dict]:
    """``[{id, similarity}]`` for this user's documents, closest first —
    only those clearing _MIN_SIMILARITY, so a caller can treat every
    returned id as a genuine match rather than re-filtering itself.
    """
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
            english_query = understanding["english"].strip()
            keywords = " ".join(understanding["keywords"])
            for variant in query_variants:
                try:
                    embedding = await _embed_query(variant)
                except Exception as exc:
                    return await _degraded_search(
                        session, user_id, [query, msa, english_query, keywords],
                        limit, exc, semantic_lists,
                    )
                semantic_lists.append(await search_documents_semantic(
                    session, user_id, embedding, limit=search_limit
                ))
            if english_ids and english_query:
                try:
                    embedding = await _embed_query(english_query)
                except Exception as exc:
                    return await _degraded_search(
                        session, user_id, [query, msa, english_query, keywords],
                        limit, exc, semantic_lists,
                    )
                semantic_lists.append(await search_documents_semantic(
                    session, user_id, embedding, limit=search_limit,
                    document_ids=english_ids,
                ))

            keyword_results: list[dict] = []
            if keywords:
                try:
                    keyword_results = await search_documents_fulltext(
                        session, user_id, keywords, limit=search_limit
                    )
                except Exception:
                    logger.warning("library Arabic keyword search failed", exc_info=True)

            original_fts_results: list[dict] = []
            try:
                original_fts_results = await search_documents_fulltext(
                    session, user_id, query, limit=search_limit,
                    missing_vectors_only=True,
                )
            except Exception:
                logger.warning(
                    "library Arabic original-query full-text search failed",
                    exc_info=True,
                )

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
            keyword_ids = {row["id"] for row in keyword_results}
            original_fts_results = [
                row for row in original_fts_results if row["id"] not in keyword_ids
            ]
            fts_lists = [
                rows for rows in (original_fts_results, keyword_results) if rows
            ]
            if semantic_results and fts_lists:
                results = reciprocal_rank_fusion(
                    [semantic_results, *fts_lists], limit
                )
            elif semantic_results:
                results = semantic_results[:limit]
            elif fts_lists:
                results = reciprocal_rank_fusion(fts_lists, limit)
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
    try:
        original_embedding = await _embed_query(query)
    except Exception as exc:
        return await _degraded_search(
            session, user_id, [query, translated], limit, exc
        )
    original_results = await search_documents_semantic(
        session, user_id, original_embedding, limit=search_limit
    )
    semantic_lists = [original_results]
    if translated:
        try:
            translated_embedding = await _embed_query(translated)
        except Exception as exc:
            return await _degraded_search(
                session, user_id, [query, translated], limit, exc,
                prior_ranked_lists=semantic_lists,
            )
        try:
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

    vectorless_fts_results: list[dict] = []
    if query_language == "english":
        try:
            vectorless_fts_results = await search_documents_fulltext(
                session, user_id, query, limit=search_limit,
                missing_vectors_only=True,
            )
        except Exception:
            logger.warning(
                "library original-query full-text search failed", exc_info=True
            )

    keyword_query = query if query_language == "arabic" else translated
    keyword_results: list[dict] = []
    if keyword_query and is_primarily_arabic(keyword_query):
        try:
            keyword_results = await search_documents_fulltext(
                session, user_id, keyword_query, limit=search_limit
            )
        except Exception:
            logger.warning("library Arabic full-text search failed", exc_info=True)

    keyword_ids = {row["id"] for row in keyword_results}
    vectorless_fts_results = [
        row for row in vectorless_fts_results if row["id"] not in keyword_ids
    ]
    fts_lists = [
        rows for rows in (vectorless_fts_results, keyword_results) if rows
    ]
    if semantic_results and fts_lists:
        results = reciprocal_rank_fusion([semantic_results, *fts_lists], limit)
    elif semantic_results:
        results = semantic_results[:limit]
    elif fts_lists:
        results = reciprocal_rank_fusion(fts_lists, limit)
    else:
        results = []

    return [
        {
            "id": row["id"],
            "similarity": similarity_by_id.get(
                row["id"], float(row.get("similarity") or 0.0)
            ),
        }
        for row in results
    ]
