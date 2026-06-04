import pytest
from sqlalchemy import text
from app.database.connection import engine, async_session_factory
from app.database.migrations import apply_migrations

@pytest.fixture(autouse=True)
async def setup_and_clean_db():
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

