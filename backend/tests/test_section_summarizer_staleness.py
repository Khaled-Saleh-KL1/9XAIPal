from uuid import uuid4

from sqlalchemy import text

import app.summarization.section_summarizer_sync as summarizer


def _insert_document(session):
    document_id = uuid4()
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'summary-stale.pdf', 'summary-stale.pdf', 'complete')"
        ),
        {"id": document_id},
    )
    session.commit()
    return document_id


def _insert_chunks(session, document_id, heading, body):
    heading_id, body_id = uuid4(), uuid4()
    session.execute(
        text(
            "INSERT INTO chunks "
            "(id, document_id, sequence_id, chunk_type, heading_path, markdown, plain_text) "
            "VALUES "
            "(:heading_id, :document_id, 1, 'heading', ARRAY[:heading], :heading_md, :heading), "
            "(:body_id, :document_id, 2, 'text', ARRAY[:heading], :body, :body)"
        ),
        {
            "heading_id": heading_id,
            "body_id": body_id,
            "document_id": document_id,
            "heading": heading,
            "heading_md": f"# {heading}",
            "body": body,
        },
    )
    session.commit()
    return [heading_id, body_id]


def test_deleted_summary_source_chunks_force_regeneration(db_session_sync, monkeypatch):
    document_id = _insert_document(db_session_sync)
    old_chunk_ids = _insert_chunks(
        db_session_sync, document_id, "مقدمة", "نص قديم عن الحكاية."
    )
    calls = []

    def fake_chat_sync(messages, **_kwargs):
        calls.append(messages)
        if len(calls) % 2:
            return {"content": "### ملخص القسم: مقدمة\n\nملخص."}
        return {"content": "### نظرة عامة على العمل: الكتاب\n\nنظرة عامة."}

    monkeypatch.setattr(summarizer, "chat_sync", fake_chat_sync)
    first = summarizer.generate_and_store_section_summaries_sync(
        db_session_sync, document_id, model="stale-test-model"
    )
    assert first.get("skipped") is not True

    db_session_sync.execute(
        text("DELETE FROM chunks WHERE document_id = :document_id"),
        {"document_id": document_id},
    )
    db_session_sync.commit()
    new_chunk_ids = _insert_chunks(
        db_session_sync, document_id, "مقدمة", "نص جديد عن الحكاية."
    )

    second = summarizer.generate_and_store_section_summaries_sync(
        db_session_sync, document_id, model="stale-test-model"
    )

    assert second.get("skipped") is not True
    assert len(calls) == 4
    current_source_ids = db_session_sync.execute(
        text(
            "SELECT source_chunk_ids FROM section_summaries "
            "WHERE document_id = :document_id AND model = 'stale-test-model' AND level = 1"
        ),
        {"document_id": document_id},
    ).scalar_one()
    assert current_source_ids == new_chunk_ids
    assert not set(current_source_ids).intersection(old_chunk_ids)
