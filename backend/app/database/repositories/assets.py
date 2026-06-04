"""Asset repository: metadata for extracted images, figures, tables."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_asset(
    session: AsyncSession,
    *,
    chunk_id: UUID,
    asset_type: str,
    file_path: str,
    mime_type: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    caption: Optional[str] = None,
) -> dict:
    """Insert a chunk asset record."""
    result = await session.execute(
        text("""
            INSERT INTO chunk_assets (chunk_id, asset_type, file_path, mime_type, width, height, caption)
            VALUES (:chunk_id, :asset_type, :file_path, :mime_type, :width, :height, :caption)
            RETURNING id, chunk_id, asset_type, file_path, created_at
        """),
        {
            "chunk_id": chunk_id,
            "asset_type": asset_type,
            "file_path": file_path,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "caption": caption,
        },
    )
    return dict(result.mappings().one())


async def get_assets_for_chunk(session: AsyncSession, chunk_id: UUID) -> list[dict]:
    """Fetch all assets for a chunk."""
    result = await session.execute(
        text("SELECT * FROM chunk_assets WHERE chunk_id = :chunk_id ORDER BY created_at"),
        {"chunk_id": chunk_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_assets_for_chunks(session: AsyncSession, chunk_ids: list[UUID]) -> list[dict]:
    """Fetch assets for multiple chunks."""
    if not chunk_ids:
        return []
    result = await session.execute(
        text("SELECT * FROM chunk_assets WHERE chunk_id = ANY(:ids) ORDER BY created_at"),
        {"ids": chunk_ids},
    )
    return [dict(r) for r in result.mappings().all()]

