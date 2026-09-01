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
    # Health of the web search cascade: `web_search` is "ok" if at least one
    # configured provider answered; `web_search_provider` names the first
    # provider that would be TRIED (not necessarily the one that would
    # answer any given query — see app.search.web.active_provider).
    web_search: Optional[str] = None
    web_search_provider: Optional[str] = None

