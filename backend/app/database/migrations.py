"""Schema migration runner."""

import re
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.database.connection import engine
from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def apply_migrations() -> None:
    """Apply schema.sql idempotently.

    We execute each statement in its *own* small transaction so that a failure
    in one statement (e.g. a COMMENT on a column that doesn't exist yet) does
    not abort the entire migration and leave later columns (table_json,
    reading_order_*, etc.) unapplied.
    """
    schema_sql = SCHEMA_PATH.read_text()
    # Fresh installs must create the embedding column at the configured
    # dimension (existing DBs are re-typed by ensure_vector_dimension).
    schema_sql = re.sub(r"vector\(\d+\)", f"vector({settings.vector_dimension})", schema_sql)
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]

    for i, stmt in enumerate(statements, 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as e:
            logger.warning(f"Migration statement {i} failed (continuing): {str(e)[:200]}")
            logger.debug(f"Failing statement was: {stmt[:300]}...")

    logger.info("Database migrations applied (best-effort)")

    # Safety net: ensure columns from later schema versions exist even if the
    # main schema.sql run had partial failures in the past. This is what
    # prevents the exact "column table_json does not exist" crash the user saw.
    await _ensure_recent_columns()


async def _ensure_recent_columns() -> None:
    """Make sure columns added after the initial schema exist.

    This is a recovery mechanism for cases where the main migration run
    partially failed due to the fragile split-on-; runner.
    """
    critical_alters = [
        # From the rich extraction / quality phase
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS table_json JSONB",
        # Reading order LLM correction (two-column papers)
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order JSONB",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order_model TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order_updated_at TIMESTAMPTZ",
        # Extractor provenance ("mineru" / "pymupdf_fallback") shown in the UI.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor TEXT",
        # Book vs. research-paper reading mode (chosen at upload).
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_kind TEXT NOT NULL DEFAULT 'paper'",
        # Nested sub-threads for tangents (paper-free focus mode inside threads).
        # Main chat turns keep parent_turn_id = NULL. Sub-thread turns point to their parent.
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS parent_turn_id UUID REFERENCES conversation_turns(id) ON DELETE CASCADE",
    ]

    async with engine.begin() as conn:
        for sql in critical_alters:
            try:
                await conn.execute(text(sql))
                logger.info(f"Ensured column: {sql.split('ADD COLUMN IF NOT EXISTS ')[-1].split()[0]}")
            except Exception as e:
                # Not fatal — the column may already exist or the DB is in a weird state.
                logger.debug(f"Ensure column skipped: {sql} -> {e}")

