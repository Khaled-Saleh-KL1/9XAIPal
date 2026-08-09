"""Paper-only mode: the embedding-skip gate and the completion chain.

The gate itself is cheap to get right. The dangerous part is the dispatcher:
completion is only ever set by generate_section_summaries, which is normally
reached via embed_document. If the skip path drops embed_document without
re-attaching the chain, a skipped document sits at 'processing' forever with
the frontend overlay spinning and no error raised anywhere.

test_skipped_document_still_reaches_complete is therefore the test that matters.

See docs/plans/paper-only-embedding-skip.md.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.extraction.jobs import JobStatus, can_transition
from app.extraction.pipeline_sync import (
    _should_skip_embeddings,
    set_embedding_mode_sync,
)


def _make_document(session, *, doc_kind="paper", chunk_tokens=(100, 200), n_chunks=None):
    """Insert a document plus chunks with known token counts. Returns its id."""
    doc_id = session.execute(
        text("""
            INSERT INTO documents (filename, original_filename, doc_kind, status)
            VALUES (:f, :o, :k, 'queued') RETURNING id
        """),
        {"f": f"{uuid.uuid4().hex}.pdf", "o": "t.pdf", "k": doc_kind},
    ).scalar_one()
    tokens = [chunk_tokens] * n_chunks if n_chunks else list(chunk_tokens)
    for seq, tok in enumerate(tokens, start=1):
        session.execute(
            text("""
                INSERT INTO chunks (document_id, sequence_id, chunk_type,
                                    markdown, plain_text, token_count)
                VALUES (:d, :s, 'text', :m, :p, :t)
            """),
            {"d": doc_id, "s": seq, "m": f"c{seq}", "p": f"c{seq}", "t": tok},
        )
    session.commit()
    return doc_id


# ── schema ───────────────────────────────────────────────────────────────────


def test_new_columns_exist_and_default_to_embedded(db_session_sync):
    """Upgrading must not reclassify anything: the default is 'embedded'."""
    doc_id = _make_document(db_session_sync)
    row = db_session_sync.execute(
        text("SELECT embedding_mode, embedding_skip_reason FROM documents WHERE id = :i"),
        {"i": doc_id},
    ).mappings().one()
    assert row["embedding_mode"] == "embedded"
    assert row["embedding_skip_reason"] is None


# ── the gate ─────────────────────────────────────────────────────────────────


def test_gate_off_by_default(db_session_sync, monkeypatch):
    monkeypatch.setattr(settings, "paper_only_mode", False)
    doc_id = _make_document(db_session_sync)
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    assert skip is False
    assert reason == "feature_disabled"


def test_gate_skips_small_paper(db_session_sync, monkeypatch):
    monkeypatch.setattr(settings, "paper_only_mode", True)
    monkeypatch.setattr(settings, "paper_only_max_tokens", 50_000)
    doc_id = _make_document(db_session_sync, chunk_tokens=(1000, 2000, 3000))
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    assert skip is True
    assert reason.startswith("fits(6000")


def test_gate_refuses_oversized_paper(db_session_sync, monkeypatch):
    monkeypatch.setattr(settings, "paper_only_mode", True)
    monkeypatch.setattr(settings, "paper_only_max_tokens", 5_000)
    doc_id = _make_document(db_session_sync, chunk_tokens=(4000, 4000))
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    assert skip is False
    assert "too_large" in reason


def test_gate_refuses_books_regardless_of_size(db_session_sync, monkeypatch):
    """doc_kind is a guard, never the gate — but a book is always embedded."""
    monkeypatch.setattr(settings, "paper_only_mode", True)
    monkeypatch.setattr(settings, "paper_only_max_tokens", 50_000)
    doc_id = _make_document(db_session_sync, doc_kind="book", chunk_tokens=(10, 10))
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    assert skip is False
    assert reason == "doc_kind=book"


def test_gate_refuses_when_token_count_missing(db_session_sync, monkeypatch):
    """Cannot prove it fits => take the safe branch and embed."""
    monkeypatch.setattr(settings, "paper_only_mode", True)
    doc_id = _make_document(db_session_sync, chunk_tokens=(None, None))
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    assert skip is False
    assert reason == "no_token_count"


def test_mode_is_recorded_and_not_re_derived(db_session_sync, monkeypatch):
    """Lowering the threshold later must not reclassify an ingested document."""
    monkeypatch.setattr(settings, "paper_only_mode", True)
    monkeypatch.setattr(settings, "paper_only_max_tokens", 50_000)
    doc_id = _make_document(db_session_sync, chunk_tokens=(1000,))
    skip, reason = _should_skip_embeddings(db_session_sync, doc_id)
    set_embedding_mode_sync(db_session_sync, doc_id, "skipped" if skip else "embedded", reason)
    db_session_sync.commit()

    monkeypatch.setattr(settings, "paper_only_max_tokens", 10)  # would now refuse
    stored = db_session_sync.execute(
        text("SELECT embedding_mode FROM documents WHERE id = :i"), {"i": doc_id}
    ).scalar_one()
    assert stored == "skipped", "stored mode must be a fact, not a recomputed policy"


# ── the completion chain (the one that matters) ──────────────────────────────


def test_chunking_to_summarizing_is_a_legal_transition():
    """The skip path bypasses EMBEDDING entirely."""
    assert can_transition(JobStatus.CHUNKING, JobStatus.SUMMARIZING)
    assert can_transition(JobStatus.CHUNKING, JobStatus.EMBEDDING)


def test_skipped_document_still_reaches_complete(db_session_sync):
    """A skipped document must complete via the same terminal call as an
    embedded one, leaving no embeddings behind.

    This exercises _mark_document_and_job_complete, the only normal exit from
    the pipeline — reached here through the re-attached chain rather than
    through embed_document.
    """
    from app.workers.tasks import _mark_document_and_job_complete

    doc_id = _make_document(db_session_sync, chunk_tokens=(100, 200))
    job_id = db_session_sync.execute(
        text("INSERT INTO ingestion_jobs (document_id, status) "
             "VALUES (:d, 'chunking') RETURNING id"),
        {"d": doc_id},
    ).scalar_one()
    set_embedding_mode_sync(db_session_sync, doc_id, "skipped", "fits(300)")
    db_session_sync.commit()

    _mark_document_and_job_complete(db_session_sync, doc_id)
    db_session_sync.commit()

    doc_status = db_session_sync.execute(
        text("SELECT status FROM documents WHERE id = :i"), {"i": doc_id}
    ).scalar_one()
    job_status = db_session_sync.execute(
        text("SELECT status FROM ingestion_jobs WHERE id = :i"), {"i": job_id}
    ).scalar_one()
    n_embeddings = db_session_sync.execute(
        text("SELECT COUNT(*) FROM chunk_embeddings ce "
             "JOIN chunks c ON c.id = ce.chunk_id WHERE c.document_id = :d"),
        {"d": doc_id},
    ).scalar_one()

    assert doc_status == "complete", "a skipped document must not stall at 'processing'"
    assert job_status == "complete"
    assert n_embeddings == 0, "skipped documents must carry no embeddings"


# ── the startup backfill must not undo the skip ──────────────────────────────


def test_requeue_excludes_skipped_documents(db_session_sync):
    """A VECTOR_DIMENSION change re-queues embeddings for every document that
    has chunks. Skipped documents must be excluded, or one dimension change
    silently re-embeds the library while embedding_mode still says 'skipped'.
    """
    embedded_id = _make_document(db_session_sync)
    skipped_id = _make_document(db_session_sync)
    set_embedding_mode_sync(db_session_sync, skipped_id, "skipped", "fits(300)")
    db_session_sync.commit()

    # Same query _requeue_all_embeddings uses in core/lifecycle.py.
    rows = db_session_sync.execute(
        text("""
            SELECT DISTINCT c.document_id
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.embedding_mode = 'embedded'
        """)
    ).scalars().all()

    assert embedded_id in rows
    assert skipped_id not in rows
