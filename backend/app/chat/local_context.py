"""Local context builder: current chunk and nearby sequential context."""

from uuid import UUID
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import chunks as chunk_repo
from app.database.repositories import assets as asset_repo
from app.core.config import settings


async def build_local_context(
    session: AsyncSession,
    *,
    document_id: UUID,
    current_chunk_id: UUID,
    window_size: Optional[int] = None,
) -> dict:
    """Build context from the current chunk and nearby chunks.
    
    Uses a larger default window (settings.local_context_window) so the model
    can better "see" surrounding text, tables, and figures when the user is
    looking at a specific part of the paper.
    """
    window_size = window_size or settings.local_context_window
    current = await chunk_repo.get_chunk(session, current_chunk_id)
    if not current:
        return {"chunks": [], "assets": []}

    # Get window of chunks
    chunks = await chunk_repo.get_chunk_window(
        session, document_id, current["sequence_id"], window_size
    )

    # Get assets for these chunks
    chunk_ids = [c["id"] for c in chunks]
    assets = await asset_repo.get_assets_for_chunks(session, chunk_ids)

    return {
        "chunks": chunks,
        "assets": assets,
        "current_chunk": current,
    }

