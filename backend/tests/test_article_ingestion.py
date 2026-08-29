"""run_article_pipeline_sync: the third pipeline (URL -> markdown -> chunks),
sharing _finish_ingestion with the PDF pipeline but never its extraction step.

article_extraction.extract_article itself is mocked here — no live network
call belongs in the regular suite. Its own fetch/filter logic is covered
separately (test_article_extraction.py) and was additionally verified
against real, live pages during development (see the plan notes).
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.extraction.pipeline_sync import run_article_pipeline_sync
from app.services.article_extraction import ArticleExtraction, ArticleExtractionError


def _insert_document_and_job(session, doc_id, job_id):
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, doc_kind, source_url, status) "
            "VALUES (:id, :filename, :url, 'article', :url, 'queued')"
        ),
        {"id": doc_id, "filename": f"{doc_id.hex}.html", "url": "https://example.com/some-article"},
    )
    session.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) VALUES (:id, :doc_id, 'queued')"),
        {"id": job_id, "doc_id": doc_id},
    )
    session.commit()


def test_run_article_pipeline_success(db_session_sync):
    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    fake = ArticleExtraction(
        title="A Real Page Title",
        markdown=(
            "# A Real Page Title\n\n"
            "Some opening paragraph about the topic at hand.\n\n"
            "## A Section\n\n"
            "More text here, with a figure below.\n\n"
            "![a photo](photo.jpg)\n"
        ),
        asset_map={"photo.jpg": "https://cdn.example.com/img/photo.jpg"},
    )

    with patch("app.services.article_extraction.extract_article", return_value=fake):
        run_article_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            url="https://example.com/some-article",
        )

    doc = db_session_sync.execute(
        text("SELECT * FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()

    # The placeholder original_filename (the URL) is overwritten with the
    # real page title once extraction has actually run.
    assert doc["original_filename"] == "A Real Page Title"
    assert doc["extractor"] == "trafilatura"
    assert doc["doc_kind"] == "article"
    assert doc["source_url"] == "https://example.com/some-article"
    # No PDF behind an article — page_count is never set.
    assert doc["page_count"] is None
    assert doc["status"] in ("processing", "complete")

    chunks = db_session_sync.execute(
        text("SELECT * FROM chunks WHERE document_id = :id ORDER BY sequence_id"),
        {"id": doc_id},
    ).mappings().all()
    assert len(chunks) >= 2
    assert any(c["chunk_type"] == "heading" for c in chunks)

    assets = db_session_sync.execute(
        text(
            "SELECT ca.* FROM chunk_assets ca "
            "JOIN chunks c ON c.id = ca.chunk_id WHERE c.document_id = :id"
        ),
        {"id": doc_id},
    ).mappings().all()
    assert len(assets) == 1
    # The hotlinked URL, not a local images_dir()-relative path — this is
    # what resolve_asset_url (repositories/assets.py) checks for at read time.
    assert assets[0]["file_path"] == "https://cdn.example.com/img/photo.jpg"


def test_run_article_pipeline_failure_marks_document_and_job_failed(db_session_sync):
    doc_id = uuid4()
    job_id = uuid4()
    _insert_document_and_job(db_session_sync, doc_id, job_id)

    with patch(
        "app.services.article_extraction.extract_article",
        side_effect=ArticleExtractionError("Couldn't fetch that page: timed out"),
    ):
        with pytest.raises(ArticleExtractionError):
            run_article_pipeline_sync(
                db_session_sync,
                document_id=doc_id,
                job_id=job_id,
                url="https://example.com/some-article",
            )

    doc = db_session_sync.execute(
        text("SELECT status, error_message FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().one()
    assert doc["status"] == "failed"
    # The reader-facing message survives sanitization verbatim — see
    # pipeline_sync._sanitize_error_for_user's ArticleExtractionError case.
    assert "Couldn't fetch that page" in doc["error_message"]

    job = db_session_sync.execute(
        text("SELECT status FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    ).mappings().one()
    assert job["status"] == "failed"

    # No dirty partial chunks left behind after a failure.
    remaining = db_session_sync.execute(
        text("SELECT COUNT(*) FROM chunks WHERE document_id = :id"), {"id": doc_id}
    ).scalar_one()
    assert remaining == 0
