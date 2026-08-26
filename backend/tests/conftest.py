import os

import pytest
from sqlalchemy import text
from app.core.config import settings
from app.database.connection import engine, async_session_factory
from app.database.migrations import apply_migrations


def _guard_destructive_db() -> None:
    """Refuse to run against a database that is not a throwaway.

    Every test truncates `documents CASCADE`, which takes chunks, assets,
    conversations, and notes with it. Pointed at the development database that
    silently destroys the user's whole library — uploaded papers vanish from
    the UI and only the PDFs on disk survive. This has happened.

    The suite therefore only runs when POSTGRES_DB names a test database, or
    when the operator has explicitly said they accept the wipe:

        POSTGRES_DB=scholarflow_test pytest
        ALLOW_DESTRUCTIVE_TESTS=1 pytest      # I know, do it anyway
    """
    if os.getenv("ALLOW_DESTRUCTIVE_TESTS") == "1":
        return
    db = settings.postgres_db
    if "test" in db.lower():
        return
    pytest.exit(
        f"\n\nRefusing to run: POSTGRES_DB={db!r} does not look like a test "
        "database, and every test truncates `documents CASCADE` — running "
        "here would delete your library.\n\n"
        "  Use a scratch database:   POSTGRES_DB=scholarflow_test pytest\n"
        "  Or accept the wipe:       ALLOW_DESTRUCTIVE_TESTS=1 pytest\n",
        returncode=2,
    )


@pytest.fixture(autouse=True)
async def setup_and_clean_db():
    _guard_destructive_db()

    # Apply migrations to ensure tables exist
    await apply_migrations()

    # Truncate tables for a clean slate
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE documents CASCADE"))
        
    yield
    
    # Optional cleanup after test
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE documents CASCADE"))
        
    # Dispose of engine connection pool to prevent "attached to a different loop" errors
    await engine.dispose()

@pytest.fixture
async def db_session():
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def db_session_sync():
    from app.database.connection import sync_session_factory
    session = sync_session_factory()
    try:
        yield session
    finally:
        session.close()

