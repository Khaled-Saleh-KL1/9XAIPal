"""API contract tests for Arabic OCR status and reader metadata."""

import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.main import app
from app.api.v1.endpoints import documents as documents_endpoint


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


async def _signup(client):
    email = f"{uuid4()}@example.com"
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert response.status_code == 201
    return email


async def _user_id(db_session, email):
    result = await db_session.execute(
        text("SELECT id FROM users WHERE LOWER(email)=:email"),
        {"email": email.lower()},
    )
    return result.scalar_one()


async def _document(
    db_session,
    user_id,
    *,
    language=None,
    writing_style=None,
    direction=None,
    source=None,
    confidence=None,
    provider_summary=None,
    status="complete",
    error_message=None,
    job_error_code=None,
    job_error_message=None,
):
    document_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO documents (id, user_id, filename, original_filename, status, "
            "detected_language, detected_writing_style, text_direction, "
            "classification_source, classification_confidence, ocr_provider_summary, "
            "error_message) VALUES (:id, :user_id, :filename, :filename, :status, "
            ":language, :writing_style, :direction, :source, :confidence, "
            "CAST(:provider_summary AS JSONB), :error_message)"
        ),
        {
            "id": document_id,
            "user_id": user_id,
            "filename": f"{document_id}.pdf",
            "status": status,
            "language": language,
            "writing_style": writing_style,
            "direction": direction,
            "source": source,
            "confidence": confidence,
            "provider_summary": (
                None
                if provider_summary is None
                else json.dumps(provider_summary)
            ),
            "error_message": error_message,
        },
    )
    if job_error_code or job_error_message:
        await db_session.execute(
            text(
                "INSERT INTO ingestion_jobs (document_id, status, error_code, error_message) "
                "VALUES (:document_id, :status, :error_code, :error_message)"
            ),
            {
                "document_id": document_id,
                "status": "failed" if job_error_code else "complete",
                "error_code": job_error_code,
                "error_message": job_error_message,
            },
        )
    await db_session.commit()
    return document_id


@pytest.mark.asyncio
async def test_progress_exposes_typed_confirmation_action(client, db_session):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="arabic",
        writing_style="mixed",
        direction="rtl",
        status="failed",
        error_message="confirmation required",
        job_error_code="arabic_style_confirmation_required",
        job_error_message="Confirm printed or handwritten.",
    )

    response = await client.get(f"/api/v1/papers/{document_id}/progress")
    detail = await client.get(f"/api/v1/papers/{document_id}")
    listing = await client.get("/api/v1/papers")

    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == "arabic_style_confirmation_required"
    assert body["error_message"] == "Confirm printed or handwritten."
    assert body["action_required"] == "confirm_arabic_writing_style"
    assert body["allowed_actions"] == ["printed", "handwritten"]
    assert body["detected_language"] == "arabic"
    assert body["text_direction"] == "rtl"
    assert detail.json()["error_code"] == body["error_code"]
    assert detail.json()["action_required"] == body["action_required"]
    listed = next(
        item for item in listing.json()["documents"]
        if item["id"] == str(document_id)
    )
    assert listed["allowed_actions"] == body["allowed_actions"]


@pytest.mark.asyncio
async def test_full_document_exposes_mixed_rtl_and_safe_provider_summary(client, db_session):
    email = await _signup(client)
    summary = [
        {"provider": "gemini_arabic_flash", "pages": [1]},
        {"provider": "gemma4_arabic_fallback", "pages": [2]},
    ]
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="mixed",
        writing_style="printed",
        direction="rtl",
        source="automatic",
        confidence=0.95,
        provider_summary=summary,
    )

    response = await client.get(f"/api/v1/papers/{document_id}/document")

    assert response.status_code == 200
    body = response.json()
    assert body["text_direction"] == "rtl"
    assert body["detected_language"] == "mixed"
    assert body["detected_writing_style"] == "printed"
    assert body["ocr_provider_summary"] == summary
    assert "api_key" not in response.text.lower()
    assert "AIza" not in response.text


@pytest.mark.asyncio
async def test_english_detail_and_list_keep_ltr_direction(client, db_session):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="english",
        writing_style="unknown",
        direction="ltr",
        source="automatic",
    )

    detail = await client.get(f"/api/v1/papers/{document_id}")
    listing = await client.get("/api/v1/papers")

    assert detail.status_code == 200
    assert detail.json()["text_direction"] == "ltr"
    listed = next(item for item in listing.json()["documents"] if item["id"] == str(document_id))
    assert listed["text_direction"] == "ltr"
    assert listed["error_code"] is None
    assert listed["action_required"] is None
    assert listed["allowed_actions"] == []


@pytest.mark.asyncio
async def test_old_document_with_null_arabic_metadata_serializes(client, db_session):
    email = await _signup(client)
    document_id = await _document(db_session, await _user_id(db_session, email))

    detail = await client.get(f"/api/v1/papers/{document_id}")
    progress = await client.get(f"/api/v1/papers/{document_id}/progress")
    full_document = await client.get(f"/api/v1/papers/{document_id}/document")

    assert detail.status_code == progress.status_code == full_document.status_code == 200
    assert detail.json()["text_direction"] is None
    assert progress.json()["text_direction"] is None
    assert full_document.json()["text_direction"] is None
    assert full_document.json()["ocr_provider_summary"] is None


@pytest.mark.asyncio
async def test_reextract_clears_stale_arabic_route_metadata(client, db_session, monkeypatch):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="arabic",
        writing_style="printed",
        direction="rtl",
        source="automatic",
        confidence=0.99,
        provider_summary=[{"provider": "gemini_arabic_flash", "pages": [1]}],
    )
    monkeypatch.setattr(documents_endpoint.process_ingestion, "delay", lambda *_args: None)

    response = await client.post(f"/api/v1/papers/{document_id}/reextract")

    assert response.status_code == 202
    row = (await db_session.execute(text(
        "SELECT detected_language, detected_writing_style, text_direction, "
        "classifier_model, classification_confidence, classification_source, "
        "ocr_provider_summary FROM documents WHERE id=:id"
    ), {"id": document_id})).mappings().one()
    assert all(value is None for value in row.values())
