"""FastAPI startup and shutdown lifecycle hooks."""

from contextlib import asynccontextmanager
from typing import AsyncIterator
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.logging import get_logger, setup_logging
from app.core.paths import ensure_storage_dirs
from app.database.connection import engine, verify_connection, async_session_factory
from app.database.migrations import apply_migrations
from app.database.pgvector import create_vector_index

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown.

    Long-running work (PDF ingestion, embedding) is dispatched to Celery workers.
    """
    setup_logging()
    logger.info("Starting 9XAIPal backend")

    # Ensure storage directories exist
    ensure_storage_dirs()

    # Optional single-origin SPA serving for "your machine = server" mode.
    # Done here (in lifespan) rather than at pure import time so the volume
    # state is guaranteed stable when the decision is made (critical for
    # Docker named volumes + uvicorn workers).
    try:
        _frontend_dist = Path("/app/frontend/dist")
        _serve_frontend = os.getenv("SERVE_FRONTEND", "false").lower() in ("1", "true", "yes")
        _has_dist = _frontend_dist.exists() and (_frontend_dist / "index.html").exists()
        if _serve_frontend or _has_dist:
            if _has_dist:
                app.mount(
                    "/",
                    StaticFiles(directory=str(_frontend_dist), html=True, check_dir=False),
                    name="frontend-spa",
                )
                logger.info("SPA frontend mounted at / (single-port server mode active)")
    except Exception as e:
        logger.warning("Frontend SPA mount skipped (non-fatal): %s", e)

    # Verify database connectivity and apply migrations
    await verify_connection()
    await apply_migrations()

    # Ensure pgvector index exists (idempotent, cheap)
    async with async_session_factory() as session:
        await create_vector_index(session)
        await session.commit()

    logger.info("9XAIPal backend ready")
    yield

    # Shutdown
    logger.info("Shutting down 9XAIPal backend")
    await engine.dispose()

