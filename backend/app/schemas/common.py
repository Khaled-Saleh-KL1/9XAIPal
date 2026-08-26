"""Shared response schemas."""

from typing import Optional
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None


class PaginationMeta(BaseModel):
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str
    database: str
    ollama: Optional[str] = None
    # Health of whichever web-search provider is active, and its name.
    web_search: Optional[str] = None
    web_search_provider: Optional[str] = None
    # ⚠ Deprecated alias for `web_search`, kept so older clients keep parsing.
    # It carries the ACTIVE provider's status, not SearXNG's specifically.
    searxng: Optional[str] = None

