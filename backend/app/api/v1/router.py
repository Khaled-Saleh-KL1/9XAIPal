"""API v1 router: combines all endpoint groups."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    health,
    documents,
    chunks,
    ask,
    models,
    notes,
    personal,
    search,
    stickies,
    studies,
)

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(documents.router, prefix="/papers", tags=["papers"])
api_router.include_router(chunks.router, prefix="/papers", tags=["chunks"])
api_router.include_router(ask.router, prefix="/papers", tags=["ask"])
api_router.include_router(notes.router, prefix="/papers", tags=["notes"])
api_router.include_router(personal.router, prefix="/papers", tags=["personal"])
api_router.include_router(studies.router, prefix="/studies", tags=["studies"])
api_router.include_router(stickies.router, prefix="/stickies", tags=["stickies"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(models.router, prefix="/models", tags=["models"])

