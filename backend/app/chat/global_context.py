"""Global context builder: vector retrieval across stored chunks."""

from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.retrieval import search_chunks
from app.database.repositories import assets as asset_repo


async def build_global_context(
    session: AsyncSession,
    *,
    query: str,
    document_id: Optional[UUID] = None,
    limit: int = 5,
) -> dict:
    """Build context using vector retrieval. Also surfaces any image assets
    attached to the returned chunks so the orchestrator can pass them to the
    multimodal model (and offer them as embeddable inline images)."""
    results = await search_chunks(
        session, query, limit=limit, document_id=document_id
    )

    chunk_ids = [r["id"] for r in results if r.get("id")]
    assets = await asset_repo.get_assets_for_chunks(session, chunk_ids) if chunk_ids else []

    return {
        "chunks": results,
        "assets": assets,
        "query": query,
    }

