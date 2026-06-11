"""Retrieval service: hybrid (vector + full-text) chunk search."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.repositories import embeddings as emb_repo
from app.database.repositories import assets as asset_repo
from app.database.pgvector import search_chunks_fulltext
from app.embeddings.model import get_query_embedding

logger = get_logger(__name__)

# Reciprocal-rank-fusion constant. 60 is the standard from the original RRF
# paper; it keeps top ranks dominant without letting either leg drown the other.
_RRF_K = 60


async def search_chunks(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    document_id: Optional[UUID] = None,
) -> list[dict]:
    """Hybrid retrieval: pgvector cosine search + Postgres full-text search,
    fused with reciprocal-rank fusion.

    Vector search captures paraphrases and semantics; full-text captures exact
    terms embeddings blur (equation numbers, acronyms, author/dataset names).
    A chunk found by both legs ranks above a chunk found by only one.
    """
    query_embedding = await get_query_embedding(query)
    # Over-fetch each leg so fusion has real candidates to reorder.
    fetch_n = max(limit * 3, 15)
    vec_hits = await emb_repo.search_embeddings(
        session, query_embedding, limit=fetch_n, document_id=document_id
    )
    try:
        fts_hits = await search_chunks_fulltext(
            session, query, limit=fetch_n, document_id=document_id
        )
    except Exception:
        logger.exception("full-text search failed (non-fatal); using vector-only results")
        fts_hits = []

    if not fts_hits:
        return vec_hits[:limit]

    scores: dict = {}
    by_id: dict = {}
    for rank, row in enumerate(vec_hits):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (_RRF_K + rank + 1)
        by_id.setdefault(row["id"], dict(row))
    for rank, row in enumerate(fts_hits):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (_RRF_K + rank + 1)
        by_id.setdefault(row["id"], dict(row))

    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results = []
    for chunk_id, score in fused:
        row = by_id[chunk_id]
        # Chunks surfaced only by the keyword leg carry no cosine similarity.
        row.setdefault("similarity", 0.0)
        row["rrf_score"] = score
        results.append(row)
    return results


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

