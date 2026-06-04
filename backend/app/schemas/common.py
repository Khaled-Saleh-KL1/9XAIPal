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
    searxng: Optional[str] = None

