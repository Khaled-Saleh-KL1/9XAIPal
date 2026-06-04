"""Health check endpoints."""

from fastapi import APIRouter

from app.schemas.common import HealthResponse
from app.llm.ollama_client import is_available as ollama_available
from app.search.searxng_client import is_available as searxng_available
from app.database.connection import verify_connection

router = APIRouter()


@router.get("", response_model=HealthResponse)
async def health_check():
    """Check health of all services."""
    # Database
    db_status = "ok"
    try:
        await verify_connection()
    except Exception:
        db_status = "unavailable"

    # Ollama
    ollama_status = "ok" if await ollama_available() else "unavailable"

    # SearXNG
    searxng_status = "ok" if await searxng_available() else "unavailable"

    overall = "ok" if db_status == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        database=db_status,
        ollama=ollama_status,
        searxng=searxng_status,
    )

