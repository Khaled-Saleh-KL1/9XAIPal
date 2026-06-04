"""PostgreSQL async engine and session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session."""
    async with async_session_factory() as session:
        yield session


async def verify_connection() -> None:
    """Verify PostgreSQL connectivity and pgvector extension."""
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT 1"))
        result.scalar_one()
        logger.info("PostgreSQL connection verified")

        # Verify pgvector
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension verified")


# Synchronous Database Engine and Session Factory for Celery Workers
from collections.abc import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

sync_engine = create_engine(
    settings.database_url_sync,
    echo=settings.debug,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)

sync_session_factory = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
)


@contextmanager
def sync_session() -> Generator[Session, None, None]:
    """Yield a synchronous database session within a transaction."""
    session = sync_session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

