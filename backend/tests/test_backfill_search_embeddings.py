"""Backfill dispatches missing document search vectors to Celery."""

from contextlib import contextmanager
from pathlib import Path
from runpy import run_path
from unittest.mock import Mock
from uuid import uuid4

from sqlalchemy import text

from app.core.config import settings


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "backfill_search_embeddings.py"


def _load_script():
    assert SCRIPT_PATH.is_file(), "backfill_search_embeddings.py must be created"
    return run_path(str(SCRIPT_PATH))


def _add_document(session, *, has_vector=False, has_chunk=False):
    document_id = uuid4()
    session.execute(
        text("""
            INSERT INTO documents (id, filename, original_filename, status)
            VALUES (:id, :filename, :filename, 'complete')
        """),
        {"id": document_id, "filename": f"{document_id}.pdf"},
    )
    if has_vector:
        vector = [0.0] * settings.vector_dimension
        vector[0] = 1.0
        literal = "[" + ",".join(map(str, vector)) + "]"
        session.execute(
            text("""
                UPDATE documents
                SET search_embedding = CAST(:embedding AS vector)
                WHERE id = :id
            """),
            {"embedding": literal, "id": document_id},
        )
    if has_chunk:
        session.execute(
            text("""
                INSERT INTO chunks
                    (document_id, sequence_id, chunk_type, markdown, plain_text)
                VALUES (:id, 1, 'text', :body, :body)
            """),
            {"id": document_id, "body": "A chunk that makes this document eligible."},
        )
    return document_id


def test_backfill_only_enqueues_documents_missing_vectors_with_chunks(db_session_sync, monkeypatch):
    enqueue = _load_script().get("enqueue_missing_search_embeddings")
    assert callable(enqueue), "script must expose enqueue_missing_search_embeddings"

    eligible_id = _add_document(db_session_sync, has_chunk=True)
    _add_document(db_session_sync, has_chunk=False)
    _add_document(db_session_sync, has_vector=True, has_chunk=True)
    db_session_sync.commit()

    @contextmanager
    def session_factory():
        yield db_session_sync

    delay = Mock()
    task = type("Task", (), {"delay": delay})()

    from app.embeddings import service_sync

    embed_inline = Mock(side_effect=AssertionError("backfill must only enqueue work"))
    monkeypatch.setattr(service_sync, "get_embeddings_batch_sync", embed_inline)

    count = enqueue(session_factory=session_factory, task=task)

    assert count == 1
    delay.assert_called_once_with(str(eligible_id))
    embed_inline.assert_not_called()
