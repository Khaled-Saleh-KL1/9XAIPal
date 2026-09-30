from uuid import uuid4

from sqlalchemy import text

from app.extraction.pipeline_sync import clean_slate_sync


def test_clean_slate_clears_summaries_from_every_model(db_session_sync):
    document_id = uuid4()
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename) "
            "VALUES (:id, 'cleanup.pdf', 'cleanup.pdf')"
        ),
        {"id": document_id},
    )
    for model in ("summary-model-one", "summary-model-two"):
        db_session_sync.execute(
            text(
                "INSERT INTO section_summaries "
                "(document_id, section_id, level, heading_path, summary_markdown, "
                "summary_plain, source_chunk_ids, model, prompt_hash) "
                "VALUES (:document_id, :section_id, 1, ARRAY['Old section'], "
                ":summary, :summary, ARRAY[]::UUID[], :model, 'old-prompt')"
            ),
            {
                "document_id": document_id,
                "section_id": f"section-{model}",
                "summary": "An old summary.",
                "model": model,
            },
        )
    db_session_sync.commit()

    clean_slate_sync(db_session_sync, document_id)
    db_session_sync.commit()

    summary_count = db_session_sync.execute(
        text("SELECT COUNT(*) FROM section_summaries WHERE document_id=:id"),
        {"id": document_id},
    ).scalar_one()
    assert summary_count == 0
