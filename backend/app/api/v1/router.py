"""API v1 router: combines all endpoint groups."""

from fastapi import APIRouter

from app.api.v1.endpoints import health, documents, chunks, ask, search

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(documents.router, prefix="/papers", tags=["papers"])
api_router.include_router(chunks.router, prefix="/papers", tags=["chunks"])
api_router.include_router(ask.router, prefix="/papers", tags=["ask"])
api_router.include_router(search.router, prefix="/search", tags=["search"])

