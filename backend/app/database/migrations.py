"""Schema migration runner."""

import re
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.database.connection import engine
from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _strip_line_comments(sql: str) -> str:
    """Remove `-- ...` line comments before the file is split on `;`.

    ⚠ This is not optional. A semicolon written inside a plain prose comment
    (nothing exotic — just an ordinary sentence that happens to need one) has
    twice now desynced the naive split-on-`;` below: the comment becomes a
    fragment of the PRECEDING statement's "end", and everything after it
    becomes the START of the NEXT statement — which is no longer valid SQL,
    so that CREATE TABLE silently fails, and every later statement that
    references the table it was supposed to create fails right behind it.
    schema.sql has no string literals containing `--`, so a straight
    per-line truncation is safe here without a real SQL tokenizer.
    """
    return "\n".join(line[: line.find("--")] if "--" in line else line for line in sql.split("\n"))


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
    schema_sql = _strip_line_comments(schema_sql)
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
        # Multi-user support. users must be created (and its email index)
        # before any of the ALTER ... REFERENCES users(id) statements below —
        # order in this list matters, they run in one transaction in order.
        """CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users (LOWER(email))",
        # Owner columns, nullable at the DB level — see the comment beside
        # documents.user_id in schema.sql for why. Application code requires
        # user_id as a non-Optional argument on every create_* path.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
        "ALTER TABLE studies ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
        "ALTER TABLE sticky_notes ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
        "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_studies_user_id ON studies(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sticky_notes_user_id ON sticky_notes(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversation_turns_user_id ON conversation_turns(user_id)",
        # From the rich extraction / quality phase
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS table_json JSONB",
        # Reading order LLM correction (two-column papers)
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order JSONB",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order_model TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS reading_order_updated_at TIMESTAMPTZ",
        # Extractor provenance ("mineru" / "pymupdf_fallback") shown in the UI.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor TEXT",
        # Book vs. research-paper reading mode (chosen at upload). 'article' is
        # a third value, for an imported web page.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_kind TEXT NOT NULL DEFAULT 'paper'",
        # The page a doc_kind='article' row was imported from; NULL otherwise.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT",
        # Paper-only mode: was the embedding pass run, or skipped because the
        # document fits whole in the chat model's context? Defaults to
        # 'embedded' so existing rows keep their behaviour on upgrade.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_mode TEXT NOT NULL DEFAULT 'embedded'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_skip_reason TEXT",
        # Which margin a note card sits in ('left' | 'right'). Added after the
        # notes table shipped, so existing notes default to the right gutter.
        "ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS margin_side TEXT NOT NULL DEFAULT 'right'",
        # The model the reader picked, as distinct from the one the provider
        # reported. Follow-ups read this so they stay on the original model.
        "ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS requested_model TEXT",
        # The trail of tool calls that produced a note's answer (SECTION /
        # SEARCH / READ / WEB, each with what it asked for and what came back).
        # Persisted so a note reopened later still shows how it was grounded,
        # not just what it concluded.
        "ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS agent_steps JSONB",
        # Which surface a note belongs to: 'anchor' (a margin card beside a
        # passage) or 'document' (asked about the whole paper from the panel).
        # Defaults to 'anchor' so every pre-existing note stays in the margin.
        "ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'anchor'",
        # A reader-chosen display name for a paper, overriding the uploaded
        # filename. NULL means "no override" — the filename still shows.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS title TEXT",
        # The desk. Created here as well as in schema.sql because the
        # split-on-semicolon runner can leave a CREATE TABLE unapplied, and
        # every ALTER below would then fail against a table that never existed.
        """CREATE TABLE IF NOT EXISTS studies (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS study_papers (
            study_id UUID NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (study_id, document_id)
        )""",
        """CREATE TABLE IF NOT EXISTS sticky_notes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT 'yellow',
            pinned BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS sticky_note_papers (
            sticky_id UUID NOT NULL REFERENCES sticky_notes(id) ON DELETE CASCADE,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            PRIMARY KEY (sticky_id, document_id)
        )""",
        # Sticky notes gained a board, a scope, and an author.
        "ALTER TABLE sticky_notes ADD COLUMN IF NOT EXISTS board TEXT NOT NULL DEFAULT 'universal'",
        "ALTER TABLE sticky_notes ADD COLUMN IF NOT EXISTS study_id UUID REFERENCES studies(id) ON DELETE SET NULL",
        "ALTER TABLE sticky_notes ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'user'",
        "ALTER TABLE sticky_notes ADD COLUMN IF NOT EXISTS author_model TEXT",
        "CREATE INDEX IF NOT EXISTS idx_sticky_notes_board ON sticky_notes (board, study_id)",
        # Which study's chat a turn belongs to. NULL = the library-wide chat,
        # which is a real scope rather than a missing value.
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS study_id UUID REFERENCES studies(id) ON DELETE CASCADE",
        # The tool trail behind an assistant turn, same shape as paper_notes.
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS agent_steps JSONB",
        # Nested sub-threads for tangents (paper-free focus mode inside threads).
        # Main chat turns keep parent_turn_id = NULL. Sub-thread turns point to their parent.
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS parent_turn_id UUID REFERENCES conversation_turns(id) ON DELETE CASCADE",
        # Fraction (0-1) of progress within the current job status, e.g. pages
        # extracted so far / total pages while status='extracting'.
        "ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS progress_fraction REAL",
        # Raw HTML snapshot status for doc_kind='article' rows (see
        # raw_snapshot_pages below). Never affects `status` — a queued/failed
        # snapshot crawl doesn't block reading or chatting with the article.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS raw_snapshot_status TEXT NOT NULL DEFAULT 'none'",
        # Created here too, same reasoning as studies/study_papers above: a
        # brand-new table is exactly as exposed to a partial split-on-`;` run
        # as they were, and this is the recovery path for that.
        """CREATE TABLE IF NOT EXISTS raw_snapshot_pages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            title TEXT,
            depth INT NOT NULL DEFAULT 0,
            storage_filename TEXT NOT NULL,
            byte_size INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_raw_snapshot_pages_document ON raw_snapshot_pages(document_id, depth, created_at)",
        # Library semantic search (title + lead excerpt), computed lazily per
        # search rather than at ingestion — see the column's own comment in
        # schema.sql. Not run through the same vector(\d+) regex as schema.sql
        # (this file's statements are plain strings, not that one), so the
        # dimension is substituted here directly.
        f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS search_embedding vector({settings.vector_dimension})",
        # See the column's own comment in schema.sql for what this gates.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS strict_scope BOOLEAN NOT NULL DEFAULT TRUE",
        # Books and articles are always scoped (2026-09-12): the switch is
        # research-papers-only now and the endpoint refuses the others, so any
        # book/article flipped open while the switch still existed is put
        # back. Idempotent: matches nothing once applied.
        "UPDATE documents SET strict_scope = TRUE WHERE doc_kind IN ('book', 'article') AND strict_scope = FALSE",
        # The library's "Done reading" shelf and its folders — see the columns'
        # own comments in schema.sql.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS done_at TIMESTAMPTZ",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS done_folder TEXT",
        # A paper's own bibliography, parsed once from its References section
        # (see services/references.py) and resolved lazily per entry against
        # Semantic Scholar (search/semantic_scholar_client.py) — the backing
        # store for clickable in-body citations like "[12]". One row per
        # citation number per paper; UNIQUE lets the /references endpoint
        # insert on first parse and be a no-op on every read after.
        """CREATE TABLE IF NOT EXISTS paper_references (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ref_number INT NOT NULL,
            raw_text TEXT NOT NULL,
            resolved_title TEXT,
            resolved_authors TEXT,
            resolved_year INT,
            resolved_pdf_url TEXT,
            external_ids JSONB,
            resolve_status TEXT NOT NULL DEFAULT 'pending',
            added_document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (document_id, ref_number)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_paper_references_document ON paper_references(document_id, ref_number)",
        # A paper resolving its OWN metadata (for BibTeX/CSV export,
        # services/export.py) is the same operation paper_references.resolve_
        # status already models for a CITATION's metadata — same
        # match_reference call, just queried with the paper's own title
        # instead of a bibliography entry's raw text. Columns rather than a
        # parallel one-row-per-document table: there is exactly one
        # resolution per document, unlike references which are many-per-
        # document.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS resolved_authors TEXT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS resolved_year INTEGER",
        # pending | resolved | no_match | unavailable — identical semantics
        # to paper_references.resolve_status: resolved/no_match are final,
        # unavailable (no key, rate-limited, network error) is retried on
        # the next export rather than cached as a dead end.
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS self_resolve_status TEXT NOT NULL DEFAULT 'pending'",
        # The evidence check behind an AI answer (chat/grounding.py): one
        # verdict per claim — supported / partial / unsupported / uncited —
        # each pointing at the passage it was judged against. On paper_notes
        # for the reader's margin Q&A, on conversation_turns for book chat and
        # the desk (both persist there). NULL = not checked (feature off, or a
        # note from before this existed); the JSON's own `status` says
        # "verified" vs "unavailable" (judge failed) — never conflated.
        "ALTER TABLE paper_notes ADD COLUMN IF NOT EXISTS grounding JSONB",
        "ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS grounding JSONB",
    ]

    for sql in critical_alters:
        try:
            # PostgreSQL aborts the whole transaction after one failed
            # statement. Keep recovery statements isolated so a missing
            # prerequisite does not prevent later independent repairs.
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            logger.info(f"Ensured column: {sql.split('ADD COLUMN IF NOT EXISTS ')[-1].split()[0]}")
        except Exception as e:
            # Not fatal — the column may already exist or the DB is in a weird state.
            logger.debug(f"Ensure column skipped: {sql} -> {e}")
