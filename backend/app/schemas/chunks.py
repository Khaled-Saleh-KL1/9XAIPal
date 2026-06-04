"""Chunk schemas."""

from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class ChunkAsset(BaseModel):
    id: UUID
    asset_type: str
    file_path: str
    mime_type: Optional[str] = None
    caption: Optional[str] = None


class ChunkResponse(BaseModel):
    id: UUID
    document_id: UUID
    sequence_id: int
    chunk_type: str
    heading_path: Optional[list[str]] = None
    markdown: str
    plain_text: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    token_count: Optional[int] = None
    assets: list[ChunkAsset] = []
    previous_chunk_id: Optional[UUID] = None
    next_chunk_id: Optional[UUID] = None


class ChunkListResponse(BaseModel):
    chunks: list[ChunkResponse]
    document_id: UUID
    total: int

