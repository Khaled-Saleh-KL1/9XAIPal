"""Search schemas."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class VectorSearchResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    sequence_id: int
    similarity: float
    plain_text: str
    page_start: Optional[int] = None


class ExternalSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_engine: Optional[str] = None
    score: Optional[float] = None


class SearchResponse(BaseModel):
    results: list[VectorSearchResult] | list[ExternalSearchResult]
    query: str
    total: int

