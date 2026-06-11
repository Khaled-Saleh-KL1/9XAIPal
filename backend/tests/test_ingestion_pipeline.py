import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from app.extraction.pipeline_sync import run_pipeline_sync
from app.extraction.mineru_client import MinerUError
from app.extraction.jobs import JobStatus


def test_run_pipeline_success(
    db_session_sync,
    tmp_path,
):
    # Setup document and job IDs
    doc_id = uuid4()
    job_id = uuid4()
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_text("fake pdf content")

    # Insert initial document and job
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'test.pdf', 'original.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status) "
            "VALUES (:id, :doc_id, 'queued')"
        ),
        {"id": job_id, "doc_id": doc_id},
    )
    db_session_sync.commit()

    # Configure mocks
    fake_extracted_dir = tmp_path / "extracted"
    fake_extracted_dir.mkdir()

    fake_md = fake_extracted_dir / "output.md"
    fake_md.write_text(
        "# Introduction\nThis is a test research paper.\n\n"
        "## Method\nHere is a formula:\n$$\nx = y + 1\n$$\n\n"
        "Here is a figure:\n![A diagram](images/fig1.png)\n\n"
        "And a table:\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )

    img_path = fake_extracted_dir / "images" / "fig1.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_text("fake image data")

    with patch("app.extraction.pipeline_sync.extract_pdf_sync", return_value=(fake_extracted_dir, "mineru")) as mock_extract, \
         patch("app.extraction.pipeline_sync.find_markdown_output", return_value=fake_md) as mock_find_md, \
         patch("app.extraction.pipeline_sync.find_images", return_value=[img_path]) as mock_find_images, \
         patch("app.extraction.pipeline_sync.move_asset_to_storage", return_value={
             "asset_type": "image",
             "file_path": f"{doc_id}/moved_fig1.png",
             "mime_type": "image/png",
             "original_name": "fig1.png",
         }) as mock_move_asset, \
         patch("app.workers.tasks.embed_document.delay") as mock_embed_delay:

        # Run the pipeline
        run_pipeline_sync(
            db_session_sync,
            document_id=doc_id,
            job_id=job_id,
            pdf_path=pdf_path,
        )

        mock_extract.assert_called_once_with(pdf_path, str(doc_id))
        mock_find_md.assert_called_once_with(fake_extracted_dir)
        mock_find_images.assert_called_once_with(fake_extracted_dir)
        mock_move_asset.assert_called_once_with(img_path, document_id=str(doc_id))
        mock_embed_delay.assert_called_once_with(str(doc_id))

    # 1. Verify job and document statuses. The pipeline deliberately does NOT
    # mark the document "complete" here: completion is set at the true end of
    # the chain (embeddings → summaries → figure descriptions) so the UI's
    # "complete" is honest. After extraction+chunking the document stays
    # "processing" and the job sits in the dispatched "embedding" stage.
    res = db_session_sync.execute(
        text("SELECT status FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert res.scalar_one() == "processing"

    res = db_session_sync.execute(
        text("SELECT status FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    )
    assert res.scalar_one() == "embedding"

    # 2. Verify chunks were persisted
    res = db_session_sync.execute(
        text("SELECT * FROM chunks WHERE document_id = :doc_id ORDER BY sequence_id"),
        {"doc_id": doc_id}
    )
    chunks = [dict(r) for r in res.mappings().all()]
    # Introduction heading, intro text, Method heading, formula lead-in text,
    # math, figure, table — headings are now preserved as their own chunks and
    # no glued-on prose is dropped.
    assert len(chunks) == 7

    # Let's inspect the chunk structures
    types = [c["chunk_type"] for c in chunks]
    assert "heading" in types
    assert "math" in types
    assert "figure" in types
    assert "table" in types

    # Check heading path propagation
    intro_chunk = chunks[0]  # first chunk starting with # Introduction
    assert intro_chunk["heading_path"] == ["Introduction"]

    # 3. Verify chunk assets were linked
    assets = db_session_sync.execute(
        text("SELECT * FROM chunk_assets")
    )
    asset_rows = list(assets.mappings().all())
    assert len(asset_rows) == 1
    assert asset_rows[0]["file_path"] == f"{doc_id}/moved_fig1.png"
    assert asset_rows[0]["asset_type"] == "image"


def test_run_pipeline_failure_cleans_up(
    db_session_sync,
    tmp_path,
):
    # Setup document and job IDs
    doc_id = uuid4()
    job_id = uuid4()
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_text("fake pdf content")

    # Insert initial document and job
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'test.pdf', 'original.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status) "
            "VALUES (:id, :doc_id, 'queued')"
        ),
        {"id": job_id, "doc_id": doc_id},
    )
    db_session_sync.commit()

    with patch("app.extraction.pipeline_sync.extract_pdf_sync", return_value=(tmp_path, "mineru")), \
         patch("app.extraction.pipeline_sync.find_markdown_output", side_effect=Exception("Failed during parsing")):

        # Run pipeline - should raise exception
        with pytest.raises(Exception, match="Failed during parsing"):
            run_pipeline_sync(
                db_session_sync,
                document_id=doc_id,
                job_id=job_id,
                pdf_path=pdf_path,
            )

    # 1. Verify job and document statuses are failed
    res = db_session_sync.execute(
        text("SELECT status FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert res.scalar_one() == "failed"

    res = db_session_sync.execute(
        text("SELECT status, error_message FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    )
    job_row = res.mappings().one()
    assert job_row["status"] == "failed"
    # The raw exception still propagates (asserted via pytest.raises above), but
    # the message persisted for the user is sanitized to avoid leaking internals.
    assert "Processing failed during extraction or chunking" in job_row["error_message"]

    # 2. Verify no chunks or assets are left in database
    res = db_session_sync.execute(
        text("SELECT COUNT(*) AS count FROM chunks WHERE document_id = :doc_id"),
        {"doc_id": doc_id}
    )
    assert res.scalar_one() == 0

    assets = db_session_sync.execute(
        text("SELECT * FROM chunk_assets")
    )
    assert len(assets.all()) == 0


def test_run_pipeline_failure_after_commit_cleans_up(
    db_session_sync,
    tmp_path,
):
    # Setup document and job IDs
    doc_id = uuid4()
    job_id = uuid4()
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_text("fake pdf content")

    # Insert initial document and job
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'test.pdf', 'original.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status) "
            "VALUES (:id, :doc_id, 'queued')"
        ),
        {"id": job_id, "doc_id": doc_id},
    )
    db_session_sync.commit()

    # Configure mocks
    fake_extracted_dir = tmp_path / "extracted"
    fake_extracted_dir.mkdir()

    fake_md = fake_extracted_dir / "output.md"
    fake_md.write_text(
        "# Introduction\nThis is a test research paper.\n\n"
        "## Method\nHere is a formula:\n$$\nx = y + 1\n$$\n\n"
        "Here is a figure:\n![A diagram](images/fig1.png)\n\n"
        "And a table:\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )

    img_path = fake_extracted_dir / "images" / "fig1.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_text("fake image data")

    with patch("app.extraction.pipeline_sync.extract_pdf_sync", return_value=(fake_extracted_dir, "mineru")), \
         patch("app.extraction.pipeline_sync.find_markdown_output", return_value=fake_md), \
         patch("app.extraction.pipeline_sync.find_images", return_value=[img_path]), \
         patch("app.extraction.pipeline_sync.move_asset_to_storage", side_effect=Exception("Storage error during asset moving")):

        # Run pipeline - should raise exception
        with pytest.raises(Exception, match="Storage error during asset moving"):
            run_pipeline_sync(
                db_session_sync,
                document_id=doc_id,
                job_id=job_id,
                pdf_path=pdf_path,
            )

    # 1. Verify job and document statuses are failed
    res = db_session_sync.execute(
        text("SELECT status FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert res.scalar_one() == "failed"

    res = db_session_sync.execute(
        text("SELECT status, error_message FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    )
    job_row = res.mappings().one()
    assert job_row["status"] == "failed"
    # Raw exception propagates (pytest.raises above); persisted message is sanitized.
    assert "Processing failed during extraction or chunking" in job_row["error_message"]

    # 2. Verify no chunks or assets are left in database
    res = db_session_sync.execute(
        text("SELECT COUNT(*) AS count FROM chunks WHERE document_id = :doc_id"),
        {"doc_id": doc_id}
    )
    assert res.scalar_one() == 0

    assets = db_session_sync.execute(
        text("SELECT * FROM chunk_assets")
    )
    assert len(assets.all()) == 0
