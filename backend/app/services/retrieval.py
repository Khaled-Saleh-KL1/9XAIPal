"""Retrieval service: hybrid (vector + full-text) chunk search."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core import tracing
from app.core.config import settings
from app.core.language import is_primarily_arabic
from app.chat.tracing_hooks import retrieval_output
from app.database.repositories import embeddings as emb_repo
from app.database.repositories import assets as asset_repo
from app.database.pgvector import search_chunks_fulltext
from app.embeddings.model import get_query_embedding
from app.services import arabic_query_understanding, retrieval_reranking
from app.services.query_translation import translated_query

logger = get_logger(__name__)

# Reciprocal-rank-fusion constant. 60 is the standard from the original RRF
# paper; it keeps top ranks dominant without letting either leg drown the other.
_RRF_K = 60


def reciprocal_rank_fusion(ranked_lists: list[list[dict]], limit: int) -> list[dict]:
    """Merge ranked result lists while preserving each row's first-seen data."""
    scores: dict = {}
    by_id: dict = {}
    for rows in ranked_lists:
        for rank, row in enumerate(rows):
            row_id = row["id"]
            scores[row_id] = scores.get(row_id, 0.0) + 1.0 / (_RRF_K + rank + 1)
            by_id.setdefault(row_id, dict(row))

    fused = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    results = []
    for row_id, score in fused:
        row = by_id[row_id]
        row.setdefault("similarity", 0.0)
        row["rrf_score"] = score
        results.append(row)
    return results


def _document_language(row: dict) -> str:
    direction = str(row.get("text_direction") or "").strip().lower()
    detected = str(row.get("detected_language") or "").strip().lower()
    return "arabic" if direction == "rtl" or detected in {"arabic", "mixed"} else "english"


async def _get_document_languages(
    session: AsyncSession,
    document_id: Optional[UUID],
    document_ids: Optional[list[UUID]],
) -> dict:
    if document_ids:
        statement = text(
            "SELECT id, text_direction, detected_language FROM documents "
            "WHERE id = ANY(:document_ids)"
        )
        params = {"document_ids": list(document_ids)}
    elif document_id:
        statement = text(
            "SELECT id, text_direction, detected_language FROM documents WHERE id = :document_id"
        )
        params = {"document_id": document_id}
    else:
        return {}

    try:
        result = await session.execute(statement, params)
        return {row["id"]: _document_language(row) for row in result.mappings().all()}
    except Exception:
        logger.warning("document language lookup failed; using original query only", exc_info=True)
        return {}


async def _search_chunks_for_query(
    session: AsyncSession,
    query: str,
    limit: int,
    document_id: Optional[UUID],
    document_ids: Optional[list[UUID]],
    max_sequence_id: Optional[int],
) -> list[dict]:
    """Run the existing vector + English/Arabic FTS hybrid for one query."""
    fetch_n = max(limit * 3, 15)

    vec_hits: list[dict] = []
    try:
        query_embedding = await get_query_embedding(query)
        vec_hits = await emb_repo.search_embeddings(
            session, query_embedding, limit=fetch_n, document_id=document_id,
            document_ids=document_ids, max_sequence_id=max_sequence_id,
        )
    except Exception:
        logger.warning(
            "vector search unavailable (non-fatal); using full-text only", exc_info=True
        )

    try:
        fts_hits = await search_chunks_fulltext(
            session, query, limit=fetch_n, document_id=document_id,
            document_ids=document_ids, max_sequence_id=max_sequence_id,
        )
    except Exception:
        logger.exception("full-text search failed (non-fatal); using vector-only results")
        fts_hits = []

    if not fts_hits:
        return vec_hits[:limit]
    if not vec_hits:
        return fts_hits[:limit]
    return reciprocal_rank_fusion([vec_hits, fts_hits], limit)


def _reranking_enabled(query_language: str, has_arabic_target: bool) -> bool:
    if query_language == "arabic" or has_arabic_target:
        return settings.rerank_arabic_enabled
    return settings.rerank_english_enabled


async def _rerank_chunks_if_enabled(
    session: AsyncSession,
    query: str,
    candidates: list[dict],
    limit: int,
    query_language: str,
    has_arabic_target: bool,
) -> list[dict]:
    if not _reranking_enabled(query_language, has_arabic_target):
        return candidates[:limit]
    try:
        reranked = await retrieval_reranking.rerank_chunks(
            session, query, candidates, limit,
            is_arabic_query=query_language == "arabic",
            has_arabic_target=has_arabic_target,
        )
        return reranked[:limit]
    except Exception:
        logger.warning("retrieval reranking failed; retaining fused order", exc_info=True)
        return candidates[:limit]


@tracing.traced("retrieve", tracing.RETRIEVER, output=retrieval_output)
async def search_chunks(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    document_id: Optional[UUID] = None,
    document_ids: Optional[list[UUID]] = None,
    max_sequence_id: Optional[int] = None,
) -> list[dict]:
    """Hybrid retrieval: pgvector cosine search + Postgres full-text search,
    fused with reciprocal-rank fusion.

    Vector search captures paraphrases and semantics; full-text captures exact
    terms embeddings blur (equation numbers, acronyms, author/dataset names).
    A chunk found by both legs ranks above a chunk found by only one.

    Scope is one document (``document_id``) or several (``document_ids``) —
    the latter is the desk, where a question is asked of a whole study at
    once. Both legs already accepted a list underneath; only this entry point
    did not, which is why the desk's agent had no semantic search at all.
    """
    language_by_id = await _get_document_languages(session, document_id, document_ids)
    query_language = "arabic" if is_primarily_arabic(query) else "english"
    if document_ids:
        opposite_ids = [
            doc_id for doc_id in document_ids
            if language_by_id.get(doc_id, "english") != query_language
        ]
    elif document_id and language_by_id.get(document_id, "english") != query_language:
        opposite_ids = [document_id]
    else:
        opposite_ids = []
    has_arabic_target = any(language == "arabic" for language in language_by_id.values())
    rerank_enabled = _reranking_enabled(query_language, has_arabic_target)
    candidate_limit = max(limit, settings.rerank_candidates) if rerank_enabled else limit

    if query_language == "arabic" and settings.arabic_query_understanding_enabled:
        understanding = await arabic_query_understanding.understand_arabic_query(query)
        if understanding:
            fetch_n = (
                candidate_limit if rerank_enabled else max(limit * 3, 15)
            )
            ranked_lists = [await _search_chunks_for_query(
                session, query, fetch_n, document_id, document_ids, max_sequence_id
            )]

            msa = understanding["msa"].strip()
            if msa and msa != query.strip():
                ranked_lists.append(await _search_chunks_for_query(
                    session, msa, fetch_n, document_id, document_ids, max_sequence_id
                ))

            keywords = " ".join(understanding["keywords"])
            if keywords:
                try:
                    keyword_hits = await search_chunks_fulltext(
                        session, keywords, limit=fetch_n, document_id=document_id,
                        document_ids=document_ids, max_sequence_id=max_sequence_id,
                    )
                except Exception:
                    logger.warning("Arabic keyword search failed; keeping other query legs", exc_info=True)
                    keyword_hits = []
                ranked_lists.append(keyword_hits)

            english_target_ids = opposite_ids
            if english_target_ids:
                english_hits = await _search_chunks_for_query(
                    session, understanding["english"], fetch_n,
                    None if document_ids else english_target_ids[0],
                    english_target_ids if document_ids else None,
                    max_sequence_id,
                )
                ranked_lists.append(english_hits)

            candidates = reciprocal_rank_fusion(ranked_lists, candidate_limit)
            return await _rerank_chunks_if_enabled(
                session, query, candidates, limit, query_language, has_arabic_target
            )

    if not opposite_ids:
        if not rerank_enabled:
            return await _search_chunks_for_query(
                session, query, limit, document_id, document_ids, max_sequence_id
            )
        candidates = await _search_chunks_for_query(
            session, query, candidate_limit, document_id, document_ids, max_sequence_id
        )
        return await _rerank_chunks_if_enabled(
            session, query, candidates, limit, query_language, has_arabic_target
        )

    target_language = "english" if query_language == "arabic" else "arabic"
    translated = await translated_query(query, target_language)
    if not translated or not translated.strip():
        candidates = await _search_chunks_for_query(
            session, query, candidate_limit, document_id, document_ids, max_sequence_id
        )
        return await _rerank_chunks_if_enabled(
            session, query, candidates, limit, query_language, has_arabic_target
        )

    # Each language-specific list gets enough candidates for the final RRF
    # pass to combine matches that were not in the first few positions.
    fetch_n = candidate_limit if rerank_enabled else max(limit * 3, 15)
    original_hits = await _search_chunks_for_query(
        session, query, fetch_n, document_id, document_ids, max_sequence_id
    )
    translated_hits = await _search_chunks_for_query(
        session, translated, fetch_n,
        None if document_ids else opposite_ids[0],
        opposite_ids if document_ids else None,
        max_sequence_id,
    )
    if not original_hits:
        return await _rerank_chunks_if_enabled(
            session, query, translated_hits, limit, query_language, has_arabic_target
        )
    if not translated_hits:
        return await _rerank_chunks_if_enabled(
            session, query, original_hits, limit, query_language, has_arabic_target
        )
    candidates = reciprocal_rank_fusion([original_hits, translated_hits], candidate_limit)
    return await _rerank_chunks_if_enabled(
        session, query, candidates, limit, query_language, has_arabic_target
    )


@tracing.traced("retrieve.figures", tracing.RETRIEVER, output=retrieval_output)
async def search_figure_chunks(
    session: AsyncSession,
    query: str,
    document_id: UUID,
    limit: int = 5,
) -> list[dict]:
    """Find chunks that have image assets and are semantically relevant to the query.

    Used when the user explicitly asks for a figure/picture. The standard
    vector search may return text-heavy chunks that happen to match the query
    semantically but contain no figures. This function filters to only chunks
    that actually have at least one image asset attached, so the model always
    has a figure to embed when the user asks "show me a picture".

    If the semantic search yields no figure-bearing chunks, we fall back to
    returning the first figure-bearing chunks in document order.

    Returns a list of dicts: {chunk: {...}, assets: [...]}.
    """
    query_embedding = await get_query_embedding(query)
    # Pull more candidates than needed, then filter to those with image assets.
    candidates = await emb_repo.search_embeddings(
        session, query_embedding, limit=limit * 4, document_id=document_id
    )
    chunk_ids = [c["id"] for c in candidates if c.get("id")]
    if chunk_ids:
        # Find which of these candidates actually have image assets.
        result = await session.execute(
            text("""
                SELECT DISTINCT chunk_id FROM chunk_assets
                WHERE chunk_id = ANY(:ids) AND asset_type = 'image'
            """),
            {"ids": chunk_ids},
        )
        chunks_with_images = {row[0] for row in result.fetchall()}
        if chunks_with_images:
            # Filter candidates to only those with images, keeping similarity order.
            filtered = [c for c in candidates if c["id"] in chunks_with_images]
            if filtered:
                filtered_ids = [c["id"] for c in filtered[:limit]]
                assets = await asset_repo.get_assets_for_chunks(session, filtered_ids)
                assets_by_chunk: dict = {}
                for a in assets:
                    assets_by_chunk.setdefault(a["chunk_id"], []).append(a)
                return [
                    {"chunk": c, "assets": assets_by_chunk.get(c["id"], [])}
                    for c in filtered[:limit]
                ]

    # ── Fallback: no semantically relevant figure chunks found ──
    # Just return the first figure-bearing chunks in document order
    result = await session.execute(
        text("""
            SELECT DISTINCT ON (c.sequence_id) c.id, c.document_id, c.sequence_id,
                c.markdown, c.plain_text, c.page_start, c.page_end, c.chunk_type,
                0.0 as similarity
            FROM chunks c
            JOIN chunk_assets ca ON ca.chunk_id = c.id
            WHERE c.document_id = :document_id AND ca.asset_type = 'image'
            ORDER BY c.sequence_id
            LIMIT :limit
        """),
        {"document_id": document_id, "limit": limit},
    )
    fallback_chunks = [dict(r) for r in result.mappings().all()]
    if not fallback_chunks:
        return []
    fb_ids = [c["id"] for c in fallback_chunks]
    assets = await asset_repo.get_assets_for_chunks(session, fb_ids)
    assets_by_chunk: dict = {}
    for a in assets:
        assets_by_chunk.setdefault(a["chunk_id"], []).append(a)
    return [
        {"chunk": c, "assets": assets_by_chunk.get(c["id"], [])}
        for c in fallback_chunks
    ]
