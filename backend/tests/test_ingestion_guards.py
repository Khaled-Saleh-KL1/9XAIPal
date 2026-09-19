"""The three guards added after the 2026-09-18 disk-full incident
(DEPLOYMENT-PRODUCTION.md §10): refuse ingestions on a nearly full disk, one
job per document at a time, and an ingestion that stops itself when its
paper is deleted mid-run. Real Postgres, same as test_ingestion_pipeline.py.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.api.errors import InsufficientStorage, JobAlreadyActive
from app.core.config import settings
from app.database.connection import sync_session_factory
from app.extraction import mineru_client, pipeline_sync
from app.services.ingestion import check_disk_headroom, create_ingestion_job


# ── disk headroom ────────────────────────────────────────────────────────────

def test_disk_headroom_refuses_at_or_past_the_threshold(monkeypatch):
    monkeypatch.setattr(settings, "ingestion_disk_refuse_percent", 0)  # any disk is "full"
    with pytest.raises(InsufficientStorage) as exc:
        check_disk_headroom()
    assert exc.value.limit_percent == 0
    assert 0 <= exc.value.used_percent <= 100


def test_disk_headroom_passes_below_the_threshold(monkeypatch):
    monkeypatch.setattr(settings, "ingestion_disk_refuse_percent", 101)  # unreachable
    check_disk_headroom()


# ── one job per document ─────────────────────────────────────────────────────

async def _insert_document(session, doc_id):
    await session.execute(
        text("INSERT INTO documents (id, filename, original_filename, status) VALUES (:id, 'a.pdf', 'a.pdf', 'queued')"),
        {"id": doc_id},
    )


async def test_second_job_for_the_same_document_is_refused(db_session):
    doc_id = uuid4()
    await _insert_document(db_session, doc_id)
    first = await create_ingestion_job(db_session, doc_id)
    assert first["status"] == "queued"

    with pytest.raises(JobAlreadyActive) as exc:
        await create_ingestion_job(db_session, doc_id)
    assert exc.value.status == "queued"

    # Once the first job is over, a new one is fine again.
    await db_session.execute(
        text("UPDATE ingestion_jobs SET status = 'complete' WHERE id = :id"), {"id": first["id"]}
    )
    second = await create_ingestion_job(db_session, doc_id)
    assert second["id"] != first["id"]
    await db_session.rollback()


async def test_a_job_on_another_document_is_not_in_the_way(db_session):
    a, b = uuid4(), uuid4()
    await _insert_document(db_session, a)
    await _insert_document(db_session, b)
    await create_ingestion_job(db_session, a)
    await create_ingestion_job(db_session, b)  # must not raise
    await db_session.rollback()


# ── deleted mid-run ──────────────────────────────────────────────────────────

def test_progress_callback_can_abort_a_batched_extraction(tmp_path, monkeypatch):
    """The real extract_pdf_sync batch loop: every other exception from
    on_progress is swallowed, ExtractionAborted goes through — and the
    server-stop `finally` still runs, so its scratch dir is gone."""
    monkeypatch.setattr(mineru_client, "extracted_dir", lambda: tmp_path)
    monkeypatch.setattr(mineru_client.shutil, "which", lambda _b: "/usr/bin/mineru")
    monkeypatch.setattr(mineru_client, "get_page_count", lambda _p: 16)
    monkeypatch.setattr(mineru_client, "_build_mineru_env", lambda: {})
    monkeypatch.setenv("MINERU_PAGE_BATCH_SIZE", "8")  # 16 pages -> 2 batches
    monkeypatch.setattr(settings, "allow_pymupdf_fallback", False)

    scratch = tmp_path / ".mineru-api-test"
    scratch.mkdir()
    proc = MagicMock()
    proc.poll.return_value = 0  # "already exited": the real stop function returns early
    monkeypatch.setattr(
        mineru_client, "_start_mineru_api_server",
        lambda env: ("http://127.0.0.1:1", proc, tmp_path / "log", scratch),
    )
    ran = []
    monkeypatch.setattr(mineru_client, "_run_mineru_cli", lambda *a, **k: ran.append(a[5]))

    pdf = tmp_path / "doc.pdf"
    pdf.write_text("x")

    # Plain exceptions are swallowed: both batches run.
    def broken(pages_done, total):
        raise ValueError("broken progress bar")
    (tmp_path / "doc-a").mkdir()
    md = tmp_path / "doc-a" / "x.md"
    md.write_text("# merged")
    with patch.object(mineru_client, "_merge_batch_outputs"), \
         patch.object(mineru_client, "find_markdown_output", return_value=md):
        mineru_client.extract_pdf_sync(pdf, "doc-a", on_progress=broken)
    assert ran == [(0, 7), (8, 15)]

    # ExtractionAborted is not: the second batch never runs, scratch is gone.
    ran.clear()
    scratch.mkdir(exist_ok=True)
    def abort(pages_done, total):
        raise mineru_client.ExtractionAborted("gone")
    with pytest.raises(mineru_client.ExtractionAborted):
        mineru_client.extract_pdf_sync(pdf, "doc-b", on_progress=abort)
    assert ran == [(0, 7)]
    assert not scratch.exists()
    assert not (tmp_path / ".doc-b_batches").exists()


def test_run_pipeline_stops_and_cleans_up_when_the_paper_is_deleted(db_session_sync, tmp_path, monkeypatch):
    doc_id, job_id = uuid4(), uuid4()
    db_session_sync.execute(
        text("INSERT INTO documents (id, filename, original_filename, status) VALUES (:id, 'a.pdf', 'a.pdf', 'queued')"),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) VALUES (:id, :doc_id, 'queued')"),
        {"id": job_id, "doc_id": doc_id},
    )
    db_session_sync.commit()

    extracted_root, images_root = tmp_path / "extracted", tmp_path / "images"
    monkeypatch.setattr(pipeline_sync, "extracted_dir", lambda: extracted_root)
    monkeypatch.setattr(pipeline_sync, "images_dir", lambda: images_root)
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")

    def extract_then_get_deleted(pdf_path, document_id, on_progress=None):
        # The reader hits DELETE /{paper_id} while batch 1 is running: a
        # different session removes the row (job too, by cascade) ...
        with sync_session_factory() as other:
            other.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
            other.commit()
        # ... and this run's output is on disk by the time the batch reports.
        (extracted_root / str(doc_id)).mkdir(parents=True, exist_ok=True)
        (images_root / str(doc_id)).mkdir(parents=True, exist_ok=True)
        on_progress(8, 100)
        raise AssertionError("on_progress must have raised DocumentDeleted")

    with patch.object(pipeline_sync, "extract_pdf_sync", side_effect=extract_then_get_deleted), \
         patch.object(pipeline_sync, "_finish_ingestion") as finish:
        pipeline_sync.run_pipeline_sync(db_session_sync, document_id=doc_id, job_id=job_id, pdf_path=pdf)  # no raise

    finish.assert_not_called()
    assert not (extracted_root / str(doc_id)).exists()
    assert not (images_root / str(doc_id)).exists()
    assert db_session_sync.execute(text("SELECT count(*) FROM chunks WHERE document_id = :id"), {"id": doc_id}).scalar_one() == 0


def test_run_pipeline_does_nothing_for_a_paper_deleted_while_queued(db_session_sync, tmp_path, monkeypatch):
    doc_id, job_id = uuid4(), uuid4()  # never inserted: already gone when the task starts
    monkeypatch.setattr(pipeline_sync, "extracted_dir", lambda: tmp_path / "extracted")
    monkeypatch.setattr(pipeline_sync, "images_dir", lambda: tmp_path / "images")
    with patch.object(pipeline_sync, "extract_pdf_sync") as extract:
        pipeline_sync.run_pipeline_sync(db_session_sync, document_id=doc_id, job_id=job_id, pdf_path=tmp_path / "a.pdf")
    extract.assert_not_called()
