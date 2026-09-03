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
    document_ids: Optional[list[UUID]] = None,
    max_sequence_id: Optional[int] = None,
) -> list[dict]:
    """Find the most similar chunks by cosine distance, scoped to one document
    (``document_id``) or several (``document_ids``, which wins).

    ``max_sequence_id`` is the reader's progress ceiling: chunks after it are
    excluded entirely. A reader part-way through a book must not have the
    ending retrieved and explained back to them, which is exactly what an
    unbounded search over an already-ingested document does.

    ⚠ Neither given returns ``[]`` rather than scanning every document —
    there is currently no legitimate caller of a fully unscoped vector search
    (every real path already has a single owned document or an
    already-ownership-filtered paper list), so this is a deliberate guardrail
    against a future caller accidentally reintroducing a cross-tenant search,
    not a behavior change for any existing one.
    """
    if not document_id and not document_ids:
        return []

    params: dict = {
        "embedding": _vector_literal(query_embedding),
        "limit": limit,
    }
    if document_ids:
        # ANY(:ids), not IN (...) — see the identical note in
        # search_chunks_fulltext below.
        filters = "AND c.document_id = ANY(:document_ids)"
        params["document_ids"] = list(document_ids)
    else:
        filters = "AND c.document_id = :document_id"
        params["document_id"] = document_id

    if max_sequence_id is not None:
        filters += " AND c.sequence_id <= :max_sequence_id"
        params["max_sequence_id"] = max_sequence_id

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
    document_ids: Optional[list[UUID]] = None,
    max_sequence_id: Optional[int] = None,
) -> list[dict]:
    """Keyword search over chunks via Postgres full-text search.

    ``max_sequence_id`` is the reader's progress ceiling: chunks after it are
    excluded entirely. A reader part-way through a book must not have the
    ending retrieved and explained back to them, which is exactly what an
    unbounded search over an already-ingested document does.

    Scope is one document (``document_id``) or several (``document_ids``,
    which wins). Neither given returns ``[]`` — every real caller already has
    a single owned document or an already-ownership-filtered paper list; an
    unscoped "whole library" search was previously reachable here but had no
    live caller relying on it, and left the door open for a future one to
    accidentally scan every user's chunks. See the identical note on
    search_similar_chunks above.

    Complements vector search: exact terms (equation numbers, acronyms, author
    names, dataset names) that embeddings blur are matched literally here.
    ``websearch_to_tsquery`` safely parses arbitrary user input (no tsquery
    syntax errors). The expression matches the GIN index created at startup.
    """
    if not document_id and not document_ids:
        return []

    filters = ""
    params: dict = {"q": query, "limit": limit}
    if document_ids:
        # ⚠ ANY(:ids), not IN (...). The study agent searches a list whose
        # length changes per request, and interpolating it would build a new
        # query string — and a new prepared-statement plan — every time.
        filters = "AND c.document_id = ANY(:document_ids)"
        params["document_ids"] = list(document_ids)
    elif document_id:
        filters = "AND c.document_id = :document_id"
        params["document_id"] = document_id

    if max_sequence_id is not None:
        filters += " AND c.sequence_id <= :max_sequence_id"
        params["max_sequence_id"] = max_sequence_id

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


# ─────────────────────────────────────────────────────────────────────────────
# Library-level document search (see services/library_search.py)
# ─────────────────────────────────────────────────────────────────────────────

async def set_document_search_embedding(
    session: AsyncSession, document_id: UUID, embedding: list[float],
) -> None:
    """Store (or replace) a document's title+excerpt embedding."""
    await session.execute(
        text("UPDATE documents SET search_embedding = CAST(:embedding AS vector) WHERE id = :id"),
        {"id": document_id, "embedding": _vector_literal(embedding)},
    )


async def search_documents_semantic(
    session: AsyncSession,
    user_id: UUID,
    query_embedding: list[float],
    limit: int = 20,
) -> list[dict]:
    """``[{id, similarity}]`` for this user's documents that already have a
    search_embedding, ranked closest first. No index (an ordinary personal
    library is at most hundreds of rows — a sequential scan over one small
    vector column per row costs nothing close to what would justify an HNSW
    index, unlike chunk_embeddings which can hold tens of thousands of rows).
    """
    result = await session.execute(
        text("""
            SELECT id, 1 - (search_embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM documents
            WHERE user_id = :user_id AND search_embedding IS NOT NULL
            ORDER BY search_embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """),
        {"user_id": user_id, "embedding": _vector_literal(query_embedding), "limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]


# ─────────────────────────────────────────────────────────────────────────────
# Agent memory (see chat/memory.py)
# ─────────────────────────────────────────────────────────────────────────────

async def insert_memory(
    session: AsyncSession,
    user_id: UUID,
    document_id: Optional[UUID],
    body: str,
    source: str,
    embedding: list[float],
    model_name: str,
) -> UUID:
    """Store one durable memory and return its id."""
    result = await session.execute(
        text("""
            INSERT INTO agent_memories (user_id, document_id, body, source, embedding, embedding_model)
            VALUES (:user_id, :document_id, :body, :source, CAST(:embedding AS vector), :model)
            RETURNING id
        """),
        {
            "user_id": user_id,
            "document_id": document_id,
            "body": body,
            "source": source,
            "embedding": _vector_literal(embedding),
            "model": model_name,
        },
    )
    return result.scalar_one()


async def search_memories(
    session: AsyncSession,
    user_id: UUID,
    query_embedding: list[float],
    limit: int,
    document_id: Optional[UUID],
    min_similarity: float,
) -> list[dict]:
    """The reader's most relevant memories for a question, by cosine similarity.

    Global memories (document_id IS NULL) are always included alongside ones
    scoped to the given document — a preference stated while reading one book
    is still true in the next.
    """
    params: dict = {
        "user_id": user_id,
        "embedding": _vector_literal(query_embedding),
        "limit": limit,
    }
    if document_id:
        scope = "AND (document_id = :document_id OR document_id IS NULL)"
        params["document_id"] = document_id
    else:
        scope = "AND document_id IS NULL"

    result = await session.execute(
        text(f"""
            SELECT id, document_id, body, source, created_at,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM agent_memories
            WHERE user_id = :user_id {scope}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """),
        params,
    )
    rows = [dict(r) for r in result.mappings().all()]
    return [r for r in rows if r["similarity"] >= min_similarity]


async def find_similar_memory(
    session: AsyncSession,
    user_id: UUID,
    document_id: Optional[UUID],
    query_embedding: list[float],
    min_similarity: float,
) -> Optional[dict]:
    """The closest existing memory in EXACTLY this scope, for write-time dedup.

    ⚠ IS NOT DISTINCT FROM, not =: document_id is often NULL (global scope),
    and NULL = NULL is NULL in SQL — a plain equality would silently exclude
    every global memory from its own dedup check.
    """
    result = await session.execute(
        text("""
            SELECT id, body, 1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM agent_memories
            WHERE user_id = :user_id AND document_id IS NOT DISTINCT FROM :document_id
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT 1
        """),
        {
            "user_id": user_id,
            "document_id": document_id,
            "embedding": _vector_literal(query_embedding),
        },
    )
    row = result.mappings().first()
    if row and row["similarity"] >= min_similarity:
        return dict(row)
    return None


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
