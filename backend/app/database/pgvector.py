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


async def search_chunks_fulltext(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    document_id: Optional[UUID] = None,
) -> list[dict]:
    """Keyword search over chunks via Postgres full-text search.

    Complements vector search: exact terms (equation numbers, acronyms, author
    names, dataset names) that embeddings blur are matched literally here.
    ``websearch_to_tsquery`` safely parses arbitrary user input (no tsquery
    syntax errors). The expression matches the GIN index created at startup.
    """
    filters = ""
    params: dict = {"q": query, "limit": limit}
    if document_id:
        filters = "AND c.document_id = :document_id"
        params["document_id"] = document_id

    result = await session.execute(
        text(f"""
            SELECT c.id, c.document_id, c.sequence_id, c.markdown, c.plain_text,
                   c.page_start, c.page_end, c.chunk_type,
                   ts_rank(
                       to_tsvector('english', coalesce(c.plain_text, '')),
                       websearch_to_tsquery('english', :q)
                   ) AS fts_rank
            FROM chunks c
            WHERE to_tsvector('english', coalesce(c.plain_text, ''))
                  @@ websearch_to_tsquery('english', :q)
            {filters}
            ORDER BY fts_rank DESC
            LIMIT :limit
        """),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


async def ensure_vector_dimension(session: AsyncSession) -> bool:
    """Sync the chunk_embeddings column to settings.vector_dimension.

    Returns True when a migration happened: existing embeddings (computed at a
    different dimension) are dropped and the column is re-typed; the caller is
    then responsible for re-queueing embedding jobs. Section summaries and
    figure descriptions are cached by prompt-hash, so a re-embed does NOT
    re-run any expensive summarization.
    """
    try:
        result = await session.execute(
            text("""
                SELECT atttypmod FROM pg_attribute
                WHERE attrelid = 'chunk_embeddings'::regclass
                  AND attname = 'embedding' AND NOT attisdropped
            """)
        )
        current = result.scalar_one_or_none()
    except Exception:
        # Table doesn't exist yet (fresh DB before migrations) — nothing to sync.
        return False

    target = settings.vector_dimension
    # pgvector stores the dimension directly as the type modifier (-1 = unconstrained).
    if current is None or current <= 0 or current == target:
        return False

    count = (await session.execute(text("SELECT COUNT(*) FROM chunk_embeddings"))).scalar_one()
    logger.warning(
        "Embedding column is vector(%d) but VECTOR_DIMENSION=%d. Dropping %d stored "
        "embeddings, re-typing the column, and re-queueing embedding jobs. "
        "(Summaries/figure descriptions are cached and will not re-run.)",
        current, target, count,
    )
    await session.execute(text("DROP INDEX IF EXISTS idx_chunk_embeddings_hnsw"))
    await session.execute(text("DELETE FROM chunk_embeddings"))
    await session.execute(
        text(f"ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE vector({target})")
    )
    return True


async def create_vector_index(session: AsyncSession) -> None:
    """Create the HNSW vector index and the full-text GIN index if missing.

    We use HNSW rather than IVFFlat: IVFFlat with a fixed ``lists`` and the
    default ``ivfflat.probes = 1`` silently drops relevant rows (it only scans
    one of ``lists`` partitions), which on small/medium corpora returns 0 hits
    for queries that clearly match. HNSW gives high recall out of the box with
    no per-query tuning and no dependency on row count.

    pgvector's HNSW implementation has a hard 2000-dimension limit. Embeddings
    are truncated/renormalized to settings.vector_dimension (MRL), so as long
    as that stays ≤ 2000 the index applies; beyond it we fall back to exact
    brute-force search with a loud warning.
    """
    # Remove the legacy IVFFlat index if a previous build created it, so the
    # HNSW index below actually takes effect (CREATE ... IF NOT EXISTS would
    # otherwise no-op on the shared index name).
    await session.execute(text("DROP INDEX IF EXISTS idx_chunk_embeddings_vector"))

    # Full-text GIN index for the hybrid-retrieval keyword leg. The expression
    # must match search_chunks_fulltext exactly for the planner to use it.
    await session.execute(
        text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_fts
            ON chunks
            USING gin (to_tsvector('english', coalesce(plain_text, '')))
        """)
    )

    if settings.vector_dimension > 2000:
        logger.warning(
            "Vector dimension %d exceeds pgvector HNSW limit (2000). "
            "Skipping HNSW index — exact brute-force search will be used. "
            "Set VECTOR_DIMENSION to 1024 (or ≤ 2000) to enable the index.",
            settings.vector_dimension,
        )
        return

    await session.execute(
        text("""
            CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
            ON chunk_embeddings
            USING hnsw (embedding vector_cosine_ops)
        """)
    )
    logger.info("Search indexes (HNSW + full-text GIN) created/verified")
