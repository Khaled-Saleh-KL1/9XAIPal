"""HTTP tests for manual Arabic writing-style confirmation and requeue."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.api.v1.endpoints import documents as documents_endpoint
from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    import app.core.redis as redis_module

    redis_module._client = None
    redis = redis_module.get_redis()
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()
    redis_module._client = None


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _signup(client, email=None):
    email = email or f"{uuid4()}@example.com"
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert response.status_code == 201
    return email


def _enable_arabic_ocr(monkeypatch):
    monkeypatch.setattr(documents_endpoint.settings, "arabic_ocr_enabled", True)


async def _user_id(db_session, email):
    result = await db_session.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :email"),
        {"email": email.lower()},
    )
    return result.scalar_one()


async def _uncertain_document(db_session, user_id, *, error_code="arabic_style_confirmation_required"):
    document_id, job_id = uuid4(), uuid4()
    await db_session.execute(
        text(
            "INSERT INTO documents (id, user_id, filename, original_filename, status, "
            "detected_language, detected_writing_style, text_direction, "
            "classification_source, classification_confidence, error_message) "
            "VALUES (:id, :user_id, :filename, :filename, 'failed', 'arabic', "
            "'unknown', 'rtl', 'automatic', 0.50, 'confirmation required')"
        ),
        {"id": document_id, "user_id": user_id, "filename": f"{document_id}.pdf"},
    )
    await db_session.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status, error_code, error_message) "
            "VALUES (:id, :document_id, 'failed', :error_code, 'confirmation required')"
        ),
        {"id": job_id, "document_id": document_id, "error_code": error_code},
    )
    await db_session.commit()
    return document_id, job_id


async def _job(db_session, job_id):
    result = await db_session.execute(
        text("SELECT id, status, error_code, error_message FROM ingestion_jobs WHERE id=:id"),
        {"id": job_id},
    )
    return result.mappings().one()


async def _document(db_session, document_id):
    result = await db_session.execute(
        text(
            "SELECT status, detected_language, detected_writing_style, text_direction, "
            "classification_source, error_message FROM documents WHERE id=:id"
        ),
        {"id": document_id},
    )
    return result.mappings().one()


@pytest.mark.asyncio
async def test_confirm_printed_requeues_same_job(client, db_session, monkeypatch):
    _enable_arabic_ocr(monkeypatch)
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )
    dispatch = []
    monkeypatch.setattr(
        documents_endpoint.process_ingestion,
        "delay",
        lambda *args: dispatch.append(args),
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == str(job_id)
    job = await _job(db_session, job_id)
    document = await _document(db_session, document_id)
    assert job["status"] == "queued"
    assert job["error_code"] is None
    assert document["detected_language"] == "arabic"
    assert document["detected_writing_style"] == "printed"
    assert document["text_direction"] == "rtl"
    assert document["classification_source"] == "user_confirmed"
    count = await db_session.execute(
        text("SELECT COUNT(*) FROM ingestion_jobs WHERE document_id=:id"),
        {"id": document_id},
    )
    assert count.scalar_one() == 1
    assert response.json()["status"] == "arabic_ocr_queued"
    assert dispatch == [(str(document_id), str(job_id), f"{document_id}.pdf")]


@pytest.mark.asyncio
async def test_confirm_handwritten_does_not_dispatch(client, db_session, monkeypatch):
    from app.services import ingestion

    _enable_arabic_ocr(monkeypatch)
    queue_reservations = []
    monkeypatch.setattr(
        ingestion,
        "check_queue_capacity",
        lambda _session: queue_reservations.append("checked"),
    )
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )
    dispatch = []
    monkeypatch.setattr(
        documents_endpoint.process_ingestion,
        "delay",
        lambda *args: dispatch.append(args),
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "handwritten"},
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "handwritten_arabic_unavailable"
    assert "Gemini Pro" in response.json()["message"]
    assert dispatch == []
    assert queue_reservations == []
    job = await _job(db_session, job_id)
    document = await _document(db_session, document_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "handwritten_arabic_unavailable"
    assert document["status"] == "failed"
    assert document["detected_writing_style"] == "handwritten"
    assert document["classification_source"] == "user_confirmed"


@pytest.mark.asyncio
async def test_confirmation_is_owner_scoped(client, db_session):
    owner_email, other_email = await _signup(client), await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, owner_email)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
        await other.post(
            "/api/v1/auth/login",
            json={"email": other_email, "password": "correct horse battery"},
        )
        response = await other.post(
            f"/api/v1/papers/{document_id}/arabic-writing-style",
            json={"writing_style": "printed"},
        )

    assert response.status_code == 404
    assert (await _job(db_session, job_id))["status"] == "failed"


@pytest.mark.asyncio
async def test_confirmation_rejects_invalid_style(client, db_session):
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "cursive"},
    )

    assert response.status_code == 422
    assert (await _job(db_session, job_id))["status"] == "failed"


@pytest.mark.asyncio
async def test_confirmation_rejects_job_without_uncertainty_error(client, db_session):
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session,
        await _user_id(db_session, email),
        error_code="handwritten_arabic_unavailable",
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )

    assert response.status_code == 409
    assert (await _job(db_session, job_id))["error_code"] == "handwritten_arabic_unavailable"


@pytest.mark.asyncio
async def test_printed_confirmation_does_not_requeue_when_arabic_feature_is_off(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(documents_endpoint.settings, "arabic_ocr_enabled", False)
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "arabic_ocr_disabled"
    job = await _job(db_session, job_id)
    document = await _document(db_session, document_id)
    assert job["status"] == "failed"
    assert document["detected_writing_style"] == "unknown"
    assert document["classification_source"] == "automatic"


@pytest.mark.asyncio
async def test_queue_full_rolls_back_printed_confirmation(client, db_session, monkeypatch):
    from app.api.errors import TooManyQueuedJobs
    from app.services import ingestion

    _enable_arabic_ocr(monkeypatch)
    async def _full(_session):
        raise TooManyQueuedJobs(10, 10)

    monkeypatch.setattr(ingestion, "check_queue_capacity", _full)
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )

    assert response.status_code == 429
    job = await _job(db_session, job_id)
    document = await _document(db_session, document_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "arabic_style_confirmation_required"
    assert document["detected_writing_style"] == "unknown"
    assert document["classification_source"] == "automatic"


@pytest.mark.asyncio
async def test_dispatch_failure_restores_typed_failed_state_without_leaking_error(
    client, db_session, monkeypatch
):
    _enable_arabic_ocr(monkeypatch)
    email = await _signup(client)
    document_id, job_id = await _uncertain_document(
        db_session, await _user_id(db_session, email)
    )

    def _fail_dispatch(*_args):
        raise RuntimeError("private broker credential")

    monkeypatch.setattr(documents_endpoint.process_ingestion, "delay", _fail_dispatch)
    response = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )

    assert response.status_code == 503
    assert "private broker credential" not in response.text
    assert response.json()["error_code"] == "arabic_confirmation_dispatch_failed"
    job = await _job(db_session, job_id)
    document = await _document(db_session, document_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "arabic_confirmation_dispatch_failed"
    assert document["status"] == "failed"

    dispatched = []
    monkeypatch.setattr(
        documents_endpoint.process_ingestion,
        "delay",
        lambda *args: dispatched.append(args),
    )
    retry = await client.post(
        f"/api/v1/papers/{document_id}/arabic-writing-style",
        json={"writing_style": "printed"},
    )
    assert retry.status_code == 202
    assert dispatched == [(str(document_id), str(job_id), f"{document_id}.pdf")]
