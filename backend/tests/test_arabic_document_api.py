"""API contract tests for Arabic OCR status and reader metadata."""

import json
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import fitz
import httpx
import pytest
from sqlalchemy import text

from app.main import app
from app.api.v1.endpoints import documents as documents_endpoint
from app.extraction import pipeline_sync
from app.extraction.arabic_types import ClassificationDecision, DocumentRoute


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


def _install_english_pdf_pipeline_fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    classifier = MagicMock(return_value=ClassificationDecision(
        route=DocumentRoute.ENGLISH,
        language="english",
        writing_style="unknown",
        text_direction="ltr",
        confidence=1.0,
        classifier_model="text_layer",
    ))
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)

    output_dir = tmp_path / "extract"
    output_dir.mkdir(exist_ok=True)
    markdown_file = tmp_path / "output.md"
    markdown_file.write_text("# Heading\n\nBody text.", encoding="utf-8")
    resolver = MagicMock(return_value=(output_dir, "mineru"))
    monkeypatch.setattr(pipeline_sync, "resolve_extractor", resolver)
    monkeypatch.setattr(pipeline_sync, "find_content_list", lambda _output: None)
    monkeypatch.setattr(pipeline_sync, "find_markdown_output", lambda _output: markdown_file)
    monkeypatch.setattr(
        pipeline_sync,
        "create_chunks_from_markdown",
        lambda _markdown: [{
            "sequence_id": 1,
            "chunk_type": "text",
            "markdown": "Body text.",
            "plain_text": "Body text.",
            "token_count": 2,
        }],
    )
    monkeypatch.setattr(pipeline_sync, "find_images", lambda _output: [])
    monkeypatch.setattr(pipeline_sync, "get_page_count", lambda _pdf: 1)
    monkeypatch.setattr(pipeline_sync, "repair_chunks", MagicMock())
    monkeypatch.setattr(pipeline_sync, "_finish_ingestion", MagicMock())
    return classifier, resolver


def _english_pdf_bytes():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "An English paper with selectable body text.")
    content = document.tobytes()
    document.close()
    return content


@pytest.mark.asyncio
async def test_direct_pdf_upload_reaches_the_shared_classifier(
    client, db_session, db_session_sync, monkeypatch, tmp_path
):
    email = await _signup(client)
    documents_dir = tmp_path / "documents"
    assets_dir = tmp_path / "assets"
    documents_dir.mkdir()
    assets_dir.mkdir()
    monkeypatch.setattr(documents_endpoint, "documents_dir", lambda: documents_dir)
    monkeypatch.setattr(documents_endpoint, "assets_dir", lambda: assets_dir)
    monkeypatch.setattr(documents_endpoint, "ensure_storage_dirs", lambda: None)
    classifier, resolver = _install_english_pdf_pipeline_fakes(monkeypatch, tmp_path)

    def dispatch_pdf(document_id, job_id, filename):
        pipeline_sync.run_pipeline_sync(
            db_session_sync,
            document_id=UUID(document_id),
            job_id=UUID(job_id),
            pdf_path=documents_dir / filename,
        )

    monkeypatch.setattr(documents_endpoint.process_ingestion, "delay", dispatch_pdf)
    response = await client.post(
        "/api/v1/papers/upload",
        files={"file": ("paper.pdf", _english_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 201
    classifier.assert_called_once()
    resolver.assert_called_once()
    stored = (await db_session.execute(
        text("SELECT detected_language, text_direction FROM documents WHERE id=:id"),
        {"id": response.json()["id"]},
    )).mappings().one()
    assert stored["detected_language"] == "english"
    assert stored["text_direction"] == "ltr"


@pytest.mark.asyncio
async def test_reextract_dispatch_reaches_the_shared_classifier(
    client, db_session, db_session_sync, monkeypatch, tmp_path
):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        status="complete",
    )
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    filename = f"{document_id}.pdf"
    (documents_dir / filename).write_bytes(_english_pdf_bytes())
    monkeypatch.setattr(documents_endpoint, "documents_dir", lambda: documents_dir)
    monkeypatch.setattr(documents_endpoint, "extracted_dir", lambda: tmp_path / "extracted")
    monkeypatch.setattr(documents_endpoint, "images_dir", lambda: tmp_path / "images")
    classifier, resolver = _install_english_pdf_pipeline_fakes(monkeypatch, tmp_path)

    def dispatch_pdf(paper_id, job_id, dispatched_filename):
        assert dispatched_filename == filename
        pipeline_sync.run_pipeline_sync(
            db_session_sync,
            document_id=UUID(paper_id),
            job_id=UUID(job_id),
            pdf_path=documents_dir / dispatched_filename,
        )

    monkeypatch.setattr(documents_endpoint.process_ingestion, "delay", dispatch_pdf)
    response = await client.post(f"/api/v1/papers/{document_id}/reextract")

    assert response.status_code == 202
    classifier.assert_called_once()
    resolver.assert_called_once()
    stored = (await db_session.execute(
        text("SELECT detected_language, text_direction FROM documents WHERE id=:id"),
        {"id": document_id},
    )).mappings().one()
    assert stored["detected_language"] == "english"
    assert stored["text_direction"] == "ltr"


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


@pytest.mark.asyncio
async def test_reextract_preserves_user_confirmed_classification(
    client, db_session, monkeypatch
):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="mixed",
        writing_style="printed",
        direction="rtl",
        source="user_confirmed",
        confidence=0.99,
        provider_summary=[{"provider": "gemini_arabic_flash", "pages": [1]}],
    )
    monkeypatch.setattr(documents_endpoint.process_ingestion, "delay", lambda *_args: None)

    response = await client.post(f"/api/v1/papers/{document_id}/reextract")

    assert response.status_code == 202
    row = (await db_session.execute(text(
        "SELECT detected_language, detected_writing_style, text_direction, "
        "classification_source, classifier_model, classification_confidence, "
        "ocr_provider_summary FROM documents WHERE id=:id"
    ), {"id": document_id})).mappings().one()
    assert row["detected_language"] == "mixed"
    assert row["detected_writing_style"] == "printed"
    assert row["text_direction"] == "rtl"
    assert row["classification_source"] == "user_confirmed"
    assert row["classifier_model"] is None
    assert row["classification_confidence"] is None
    assert row["ocr_provider_summary"] is None


@pytest.mark.asyncio
async def test_regenerate_summaries_dispatches_force_value(client, db_session, monkeypatch):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        status="complete",
    )
    dispatched = []
    monkeypatch.setattr(
        documents_endpoint.generate_section_summaries,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    response = await client.post(
        f"/api/v1/papers/{document_id}/regenerate-summaries?force=true"
    )

    assert response.status_code == 202
    assert response.json()["force"] is True
    assert dispatched == [((str(document_id),), {"force": True})]


@pytest.mark.asyncio
async def test_rechunk_cleans_arabic_plain_text_and_refreshes_token_count(
    client, db_session, monkeypatch, tmp_path
):
    email = await _signup(client)
    document_id = await _document(
        db_session,
        await _user_id(db_session, email),
        language="arabic",
        writing_style="printed",
        direction="rtl",
        source="automatic",
    )
    await db_session.execute(
        text("UPDATE documents SET extractor='gemini_arabic_flash' WHERE id=:id"),
        {"id": document_id},
    )
    await db_session.commit()

    extraction_root = tmp_path / "extracted"
    extraction_dir = extraction_root / str(document_id)
    extraction_dir.mkdir(parents=True)
    content_list = extraction_dir / "content_list.json"
    content_list.write_text(
        json.dumps(
            [{
                "type": "text",
                "page_idx": 0,
                "ocr_provider": "gemini_arabic_flash",
                "text": "**مرحبا** [الرابط](https://example.test) `المتغير`",
            }],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(documents_endpoint, "extracted_dir", lambda: extraction_root)
    monkeypatch.setattr(documents_endpoint, "documents_dir", lambda: tmp_path / "documents")

    from app.extraction import mineru_client
    from app.extraction.normalizer import estimate_tokens

    monkeypatch.setattr(mineru_client, "find_content_list", lambda _path: content_list)
    monkeypatch.setattr(mineru_client, "find_images", lambda _path: [])
    monkeypatch.setattr(documents_endpoint.embed_document, "delay", lambda *_args: None)

    response = await client.post(f"/api/v1/papers/{document_id}/rechunk")

    assert response.status_code == 200
    stored = (await db_session.execute(
        text("SELECT plain_text, token_count FROM chunks WHERE document_id=:id"),
        {"id": document_id},
    )).mappings().one()
    assert stored["plain_text"] == "مرحبا الرابط المتغير"
    assert stored["token_count"] == estimate_tokens("مرحبا الرابط المتغير")
