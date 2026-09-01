"""Health check endpoints."""

from fastapi import APIRouter

from app.schemas.common import HealthResponse
from app.llm.client import is_available as llm_available
from app.search.web import active_provider, is_available as web_search_available
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

    # LLM provider (Ollama or the configured cloud API)
    ollama_status = "ok" if await llm_available() else "unavailable"

    # Web search — the cascade's first-in-line provider, and whether ANY
    # configured provider in the cascade answered (see app/search/web.py).
    provider = active_provider()
    web_status = "ok" if await web_search_available() else "unavailable"

    overall = "ok" if db_status == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        database=db_status,
        ollama=ollama_status,
        web_search=web_status,
        web_search_provider=provider,
    )

