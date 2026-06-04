"""Chat and /ask schemas."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AskRequest(BaseModel):
    prompt: str
    document_id: Optional[UUID] = None
    current_chunk_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None


class Citation(BaseModel):
    chunk_id: Optional[UUID] = None
    sequence_id: Optional[int] = None
    page: Optional[int] = None
    text_snippet: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    context_type: str  # LOCAL | GLOBAL | OVERVIEW | EXTERNAL | OUT_OF_SCOPE | RESEARCH
    router_reason: str
    citations: list[Citation] = []
    model: str
    conversation_id: Optional[UUID] = None
    # When the model performed live research, this contains a short human-readable status
    research_performed: bool = False
    research_summary: Optional[str] = None  # e.g. "Studied 12 sources across 3 iterations"

