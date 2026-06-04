"""pgvector operations: insert, search, and index management."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _vector_literal(embedding: list[float]) -> str:
    """Encode a Python list as a pgvector text literal: '[v1,v2,...]'.

    asyncpg has no native adapter for pgvector, so casting `:param AS vector`
    requires the string form. Passing a Python list errors with
    `expected str, got list`.
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def insert_embedding(
    session: AsyncSession,
    chunk_id: UUID,
    embedding: list[float],
    model_name: str,
) -> None:
    """Insert or update a chunk embedding."""
    await session.execute(
        text("""
            INSERT INTO chunk_embeddings (chunk_id, embedding, embedding_model)
            VALUES (:chunk_id, CAST(:embedding AS vector), :model)
            ON CONFLICT (chunk_id) DO UPDATE
            SET embedding = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model,
                created_at = NOW()
        """),
        {
            "chunk_id": chunk_id,
            "embedding": _vector_literal(embedding),
            "model": model_name,
        },
    )


async def search_similar_chunks(
    session: AsyncSession,
    query_embedding: list[float],
    limit: int = 10,
    document_id: Optional[UUID] = None,
) -> list[dict]:
    """Find the most similar chunks by cosine distance."""
    filters = ""
    params: dict = {
        "embedding": _vector_literal(query_embedding),
        "limit": limit,
    }

    if document_id:
        filters = "AND c.document_id = :document_id"
        params["document_id"] = document_id

    result = await session.execute(
        text(f"""
            SELECT c.id, c.document_id, c.sequence_id, c.markdown, c.plain_text,
                   c.page_start, c.page_end, c.chunk_type,
                   1 - (ce.embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM chunk_embeddings ce
            JOIN chunks c ON c.id = ce.chunk_id
            WHERE 1=1 {filters}
            ORDER BY ce.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """),
        params,
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def create_vector_index(session: AsyncSession) -> None:
    """Create an HNSW index on chunk_embeddings if it doesn't exist.

    We use HNSW rather than IVFFlat: IVFFlat with a fixed ``lists`` and the
    default ``ivfflat.probes = 1`` silently drops relevant rows (it only scans
    one of ``lists`` partitions), which on small/medium corpora returns 0 hits
    for queries that clearly match. HNSW gives high recall out of the box with
    no per-query tuning and no dependency on row count.
    """
    # Remove the legacy IVFFlat index if a previous build created it, so the
    # HNSW index below actually takes effect (CREATE ... IF NOT EXISTS would
    # otherwise no-op on the shared index name).
    await session.execute(text("DROP INDEX IF EXISTS idx_chunk_embeddings_vector"))
    await session.execute(
        text("""
            CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
            ON chunk_embeddings
            USING hnsw (embedding vector_cosine_ops)
        """)
    )
    logger.info("Vector index (HNSW) created/verified")
