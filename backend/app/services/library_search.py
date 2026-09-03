"""Semantic search over a user's own library: find a document by what it's
about, not just a title substring — the library's own "Search" box, distinct
from app.services.retrieval (search *inside* an already-open document).

A document's search_embedding (title + a short lead excerpt) is computed
lazily, on its first appearance in a search, rather than at ingestion — most
documents are fast-ingested with no chunk embeddings at all (see
extraction/pipeline_sync.py::_is_fast_ingest), and this is cheap enough
(one short embedding per document, not per chunk) that piggybacking on a
search beats a separate backfill job or a new ingestion step.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.pgvector import search_documents_semantic, set_document_search_embedding
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import documents as doc_repo
from app.embeddings.model import get_embeddings_batch, get_query_embedding

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
    query_embedding = await get_query_embedding(query)
    results = await search_documents_semantic(session, user_id, query_embedding, limit=limit)
    return [r for r in results if r["similarity"] >= _MIN_SIMILARITY]
