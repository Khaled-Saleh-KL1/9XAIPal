from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database.repositories.documents import list_documents
from app.extraction.pipeline_sync import update_job_status_sync
from app.schemas.documents import DocumentResponse
from app.services.ingestion import requeue_failed_job, update_job_status
from app.api.arabic_status import document_error_fields


def test_non_arabic_error_keeps_actionable_document_message():
    fields = document_error_fields({
        "error_message": "Failed to queue ingestion task. Start Redis and retry.",
        "job_error_message": "Dispatch failed: private connection detail",
        "job_error_code": None,
    })
    assert fields["error_message"] == "Failed to queue ingestion task. Start Redis and retry."


def _insert_job_sync(session, status="queued"):
    document_id = uuid4()
    job_id = uuid4()
    session.execute(
        text("INSERT INTO documents (id, filename, original_filename) "
             "VALUES (:id, 'persist.pdf', 'persist.pdf')"),
        {"id": document_id},
    )
    session.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) "
             "VALUES (:id, :document_id, :status)"),
        {"id": job_id, "document_id": document_id, "status": status},
    )
    session.commit()
    return job_id


async def _insert_job_async(session, status="queued"):
    document_id = uuid4()
    job_id = uuid4()
    await session.execute(
        text("INSERT INTO documents (id, filename, original_filename) "
             "VALUES (:id, 'persist.pdf', 'persist.pdf')"),
        {"id": document_id},
    )
    await session.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) "
             "VALUES (:id, :document_id, :status)"),
        {"id": job_id, "document_id": document_id, "status": status},
    )
    await session.commit()
    return job_id


def test_arabic_columns_exist(db_session_sync):
    cols = db_session_sync.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name IN ('documents', 'ingestion_jobs')
    """)).scalars().all()
    for name in (
        "detected_language", "detected_writing_style", "text_direction",
        "classifier_model", "classification_confidence",
        "classification_source", "ocr_provider_summary", "error_code",
    ):
        assert name in cols


@pytest.mark.asyncio
async def test_requeue_clears_failure_fields(db_session):
    job_id = await _insert_job_async(db_session, "failed")
    await db_session.execute(
        text("""UPDATE ingestion_jobs SET error_code='OCR_QUOTA_EXHAUSTED',
                   error_message='quota exhausted', started_at=NOW(),
                   completed_at=NOW(), progress_fraction=0.7 WHERE id=:id"""),
        {"id": job_id},
    )
    await db_session.commit()

    row = await requeue_failed_job(db_session, job_id)
    assert row["status"] == "queued"
    persisted = (await db_session.execute(text(
        "SELECT error_code, error_message, completed_at FROM ingestion_jobs WHERE id=:id"
    ), {"id": job_id})).mappings().one()
    assert dict(persisted) == {
        "error_code": None, "error_message": None, "completed_at": None
    }


@pytest.mark.asyncio
async def test_requeue_rejects_a_nonfailed_job(db_session):
    job_id = await _insert_job_async(db_session, "queued")
    with pytest.raises(ValueError, match="only a failed ingestion job"):
        await requeue_failed_job(db_session, job_id)


@pytest.mark.asyncio
async def test_async_status_writer_sets_and_clears_typed_failure(db_session):
    job_id = await _insert_job_async(db_session)
    await update_job_status(
        db_session, job_id, "failed", error_message="Quota exhausted",
        error_code="OCR_QUOTA_EXHAUSTED",
    )
    failed = (await db_session.execute(text(
        "SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"
    ), {"id": job_id})).mappings().one()
    assert dict(failed) == {
        "error_code": "OCR_QUOTA_EXHAUSTED",
        "error_message": "Quota exhausted",
    }

    await update_job_status(db_session, job_id, "extracting")
    active = (await db_session.execute(text(
        "SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"
    ), {"id": job_id})).mappings().one()
    assert dict(active) == {"error_code": None, "error_message": None}


@pytest.mark.asyncio
async def test_document_list_exposes_arabic_metadata_and_latest_job_error(db_session):
    user_id = uuid4()
    document_id = uuid4()
    job_id = uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, password_hash) "
             "VALUES (:id, 'arabic-persist@example.test', 'hash')"),
        {"id": user_id},
    )
    await db_session.execute(text("""
        INSERT INTO documents (
            id, user_id, filename, original_filename, status,
            detected_language, detected_writing_style, text_direction,
            classifier_model, classification_confidence, classification_source,
            ocr_provider_summary
        ) VALUES (
            :id, :user_id, 'arabic.pdf', 'arabic.pdf', 'failed',
            'mixed', 'printed', 'rtl', 'qwen3-vl:4b-instruct', 0.99,
            'automatic', '[{"provider":"gemini_arabic_flash","pages":[1]}]'::jsonb
        )
    """), {"id": document_id, "user_id": user_id})
    await db_session.execute(text("""
        INSERT INTO ingestion_jobs (id, document_id, status, error_code, error_message)
        VALUES (:id, :document_id, 'failed', 'handwritten_arabic_unavailable', 'Not available')
    """), {"id": job_id, "document_id": document_id})

    listed = await list_documents(db_session, user_id)
    response = DocumentResponse.model_validate(listed[0])
    assert response.detected_language == "mixed"
    assert response.detected_writing_style == "printed"
    assert response.text_direction == "rtl"
    assert response.classifier_model == "qwen3-vl:4b-instruct"
    assert response.classification_confidence == pytest.approx(0.99)
    assert response.classification_source == "automatic"
    assert response.ocr_provider_summary == [
        {"provider": "gemini_arabic_flash", "pages": [1]}
    ]
    assert response.job_error_code == "handwritten_arabic_unavailable"
    assert response.job_error_message == "Not available"


def test_sync_status_writer_sets_and_clears_typed_failure(db_session_sync):
    job_id = _insert_job_sync(db_session_sync)
    update_job_status_sync(
        db_session_sync, job_id, "failed", error_message="Quota exhausted",
        error_code="OCR_QUOTA_EXHAUSTED",
    )
    failed = db_session_sync.execute(text(
        "SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"
    ), {"id": job_id}).mappings().one()
    assert dict(failed) == {
        "error_code": "OCR_QUOTA_EXHAUSTED",
        "error_message": "Quota exhausted",
    }

    update_job_status_sync(db_session_sync, job_id, "extracting")
    active = db_session_sync.execute(text(
        "SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"
    ), {"id": job_id}).mappings().one()
    assert dict(active) == {"error_code": None, "error_message": None}
