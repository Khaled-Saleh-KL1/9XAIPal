"""Background creation of the library-level document search vector."""

from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.embeddings import service_sync


def _add_document(session, *, title=None, filename="paper.pdf"):
    document_id = uuid4()
    session.execute(
        text("""
            INSERT INTO documents (id, filename, original_filename, title, status)
            VALUES (:id, :filename, :filename, :title, 'complete')
        """),
        {"id": document_id, "filename": filename, "title": title},
    )
    return document_id


def _add_chunk(session, document_id, sequence_id, body, chunk_type="text"):
    session.execute(
        text("""
            INSERT INTO chunks
                (document_id, sequence_id, chunk_type, markdown, plain_text)
            VALUES (:document_id, :sequence_id, :chunk_type, :body, :body)
        """),
        {
            "document_id": document_id,
            "sequence_id": sequence_id,
            "chunk_type": chunk_type,
            "body": body,
        },
    )


def _search_embedding_helper(name):
    helper = getattr(service_sync, name, None)
    assert callable(helper), f"service_sync.{name} must be implemented"
    return helper


def test_search_vector_text_uses_title_and_first_three_long_text_chunks(db_session_sync):
    build_text = _search_embedding_helper("build_document_search_text_sync")
    document_id = _add_document(db_session_sync, title="A useful paper")
    first = "First substantive paragraph, long enough to qualify as leading prose."
    second = "Second substantive paragraph, also longer than forty characters."
    third = "Third substantive paragraph is included in the search text."
    _add_chunk(db_session_sync, document_id, 1, "too short")
    _add_chunk(
        db_session_sync, document_id, 2,
        "A long table value that is not prose and must not enter the search text.",
        chunk_type="table",
    )
    for sequence_id, body in enumerate((first, second, third), start=3):
        _add_chunk(db_session_sync, document_id, sequence_id, body)
    _add_chunk(
        db_session_sync, document_id, 6,
        "Fourth substantive paragraph is beyond the three chunk limit.",
    )
    db_session_sync.commit()

    result = build_text(db_session_sync, document_id)

    assert result == f"A useful paper\n\n{first}\n\n{second}\n\n{third}"


def test_search_vector_text_falls_back_to_filename_and_caps_at_2000(db_session_sync):
    build_text = _search_embedding_helper("build_document_search_text_sync")
    document_id = _add_document(db_session_sync, title=None, filename="fallback.pdf")
    _add_chunk(db_session_sync, document_id, 1, "A" * 2300)
    db_session_sync.commit()

    result = build_text(db_session_sync, document_id)

    assert result.startswith("fallback.pdf\n\n")
    assert len(result) == 2000


def test_search_embedding_is_written_under_the_bulk_permit(db_session_sync, monkeypatch):
    embed_search = _search_embedding_helper("embed_document_search_vector_sync")
    document_id = _add_document(db_session_sync, title="Title for vector")
    lead = "A leading paragraph that is long enough to enter the search vector."
    _add_chunk(db_session_sync, document_id, 1, lead)
    db_session_sync.commit()
    vector = [0.25] * settings.vector_dimension
    events = []

    @contextmanager
    def recording_permit():
        events.append("permit-enter")
        try:
            yield
        finally:
            events.append("permit-exit")

    def batch(texts):
        events.append(("embed", texts))
        return [vector]

    monkeypatch.setattr(service_sync, "bulk_embedding_permit", recording_permit)
    monkeypatch.setattr(service_sync, "get_embeddings_batch_sync", batch)

    created = embed_search(db_session_sync, document_id)

    assert created is True
    assert events == [
        "permit-enter",
        ("embed", [f"Title for vector\n\n{lead}"]),
        "permit-exit",
    ]
    assert db_session_sync.execute(
        text("SELECT search_embedding IS NOT NULL FROM documents WHERE id = :id"),
        {"id": document_id},
    ).scalar_one()


def test_existing_search_embedding_is_never_overwritten(db_session_sync, monkeypatch):
    embed_search = _search_embedding_helper("embed_document_search_vector_sync")
    document_id = _add_document(db_session_sync, title="Existing vector")
    existing = [1.0] + [0.0] * (settings.vector_dimension - 1)
    existing_literal = "[" + ",".join(map(str, existing)) + "]"
    db_session_sync.execute(
        text("UPDATE documents SET search_embedding = CAST(:embedding AS vector) WHERE id = :id"),
        {"embedding": existing_literal, "id": document_id},
    )
    db_session_sync.commit()
    batch = Mock(side_effect=AssertionError("existing vectors must not be re-embedded"))
    permit = Mock(side_effect=AssertionError("existing vectors must not acquire a permit"))
    monkeypatch.setattr(service_sync, "get_embeddings_batch_sync", batch)
    monkeypatch.setattr(service_sync, "bulk_embedding_permit", permit)

    created = embed_search(db_session_sync, document_id)

    assert created is False
    batch.assert_not_called()
    permit.assert_not_called()
    distance = db_session_sync.execute(
        text("""
            SELECT search_embedding <=> CAST(:embedding AS vector)
            FROM documents WHERE id = :id
        """),
        {"embedding": existing_literal, "id": document_id},
    ).scalar_one()
    assert distance == 0


def test_search_vector_failure_does_not_fail_chunk_embedding_task(monkeypatch):
    from app.workers import tasks

    document_id = str(uuid4())
    sessions = []

    @contextmanager
    def fake_session():
        sessions.append("opened")
        yield object()

    def fail_search_embedding(_session, _document_id):
        raise RuntimeError("search-vector backend unavailable")

    summaries = Mock()
    monkeypatch.setattr(tasks.sync_engine, "dispose", lambda: None)
    monkeypatch.setattr(tasks, "sync_session", fake_session)
    monkeypatch.setattr(tasks, "embed_document_chunks_sync", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(
        tasks, "embed_document_search_vector_sync", fail_search_embedding, raising=False
    )
    monkeypatch.setattr(tasks.generate_section_summaries, "delay", summaries)

    result = tasks.embed_document.apply(args=(document_id,), throw=True).get()

    assert result == {"document_id": document_id, "embedded": 3}
    assert sessions == ["opened", "opened"]
    summaries.assert_called_once_with(document_id)
