"""Session helper for background workers.

This intentionally does NOT use ``session.begin()`` because callers
(e.g. the ingestion pipeline) drive their own ``session.commit()`` calls
between steps so that external observers can see status transitions
(``extracting → chunking → embedding``) in real time. Wrapping in
``session.begin()`` would end the outer transaction the first time the
caller commits, breaking every subsequent step.

Behavior:
- yields an open AsyncSession
- on uncaught exception inside the ``with`` body, rolls back any
  in-flight statements before propagating
- always closes the session on exit
"""

from typing import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import async_session_factory


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
