import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import fitz
from sqlalchemy import text
from unittest.mock import MagicMock

from app.extraction import pipeline_sync
from app.extraction.arabic_classifier import (
    ArabicClassifierInputError,
    ArabicClassifierResponseInvalid,
    ArabicClassifierUnavailable,
)
from app.extraction.arabic_types import (
    ArabicOcrPage,
    ArabicRoutingError,
    ArabicStyleConfirmationRequired,
    ClassificationDecision,
    DocumentRoute,
    HandwrittenArabicUnavailable,
)
from app.extraction.arabic_adapter import pages_to_content_list


def _decision(route, language, writing_style, direction, confidence=0.99):
    return ClassificationDecision(
        route=route,
        language=language,
        writing_style=writing_style,
        text_direction=direction,
        confidence=confidence,
        classifier_model="qwen3-vl:4b-instruct",
    )


def _seed(session, *, classification=None):
    document_id, job_id = uuid4(), uuid4()
    if classification is None:
        session.execute(
            text(
                "INSERT INTO documents (id, filename, original_filename, status) "
                "VALUES (:id, 'sample.pdf', 'sample.pdf', 'queued')"
            ),
            {"id": document_id},
        )
    else:
        session.execute(
            text(
                "INSERT INTO documents (id, filename, original_filename, status, "
                "classification_source, detected_language, detected_writing_style, "
                "text_direction, classifier_model, classification_confidence) "
                "VALUES (:id, 'sample.pdf', 'sample.pdf', 'queued', :source, :language, "
                ":writing_style, :direction, :classifier_model, :confidence)"
            ),
            {"id": document_id, **classification},
        )
    session.execute(
        text(
            "INSERT INTO ingestion_jobs (id, document_id, status) "
            "VALUES (:id, :document_id, 'queued')"
        ),
        {"id": job_id, "document_id": document_id},
    )
    session.commit()
    return document_id, job_id


def _pipeline_mocks(monkeypatch, tmp_path, *, extractor="mineru"):
    output_dir = tmp_path / "extract"
    markdown_file = tmp_path / "output.md"
    markdown_file.write_text("# Heading\n\nbody", encoding="utf-8")
    resolver = MagicMock(return_value=(output_dir, extractor))
    arabic_extractor = MagicMock(
        return_value=SimpleNamespace(
            output_dir=output_dir,
            extractor="gemini_arabic_flash",
            provider_summary=[
                {"provider": "gemini_arabic_flash", "pages": [1]}
            ],
        )
    )
    finish = MagicMock()
    repair = MagicMock()
    gemini_factory = MagicMock(name="GeminiOcrClient")
    gemma_factory = MagicMock(name="GemmaArabicFallback")

    monkeypatch.setattr(pipeline_sync, "resolve_extractor", resolver)
    monkeypatch.setattr(pipeline_sync, "extract_arabic_document", arabic_extractor)
    monkeypatch.setattr(pipeline_sync, "GeminiOcrClient", gemini_factory)
    monkeypatch.setattr(pipeline_sync, "GemmaArabicFallback", gemma_factory)
    monkeypatch.setattr(pipeline_sync, "find_content_list", lambda _output: None)
    monkeypatch.setattr(
        pipeline_sync, "find_markdown_output", lambda _output: markdown_file
    )
    monkeypatch.setattr(
        pipeline_sync,
        "create_chunks_from_markdown",
        lambda _markdown: [
            {
                "sequence_id": 1,
                "chunk_type": "text",
                "markdown": "body",
                "plain_text": "body",
                "token_count": 1,
            }
        ],
    )
    monkeypatch.setattr(pipeline_sync, "find_images", lambda _output: [])
    monkeypatch.setattr(pipeline_sync, "get_page_count", lambda _pdf: 1)
    monkeypatch.setattr(pipeline_sync, "_finish_ingestion", finish)
    monkeypatch.setattr(pipeline_sync, "repair_chunks", repair)
    return SimpleNamespace(
        resolver=resolver,
        arabic_extractor=arabic_extractor,
        finish=finish,
        repair=repair,
        gemini_factory=gemini_factory,
        gemma_factory=gemma_factory,
        output_dir=output_dir,
    )


def _run(session, tmp_path, monkeypatch, document_id, job_id):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"original uploaded pdf")
    pipeline_sync.run_pipeline_sync(
        session,
        document_id=document_id,
        job_id=job_id,
        pdf_path=pdf_path,
    )
    return pdf_path


def _write_pdf_with_text(path, content):
    document = fitz.open()
    page = document.new_page()
    if content:
        page.insert_text((72, 72), content)
    document.save(path)
    document.close()
    return path


def _stored_document(session, document_id):
    return session.execute(
        text(
            "SELECT extractor, detected_language, detected_writing_style, "
            "text_direction, classifier_model, classification_confidence, "
            "classification_source, ocr_provider_summary "
            "FROM documents WHERE id=:id"
        ),
        {"id": document_id},
    ).mappings().one()


def test_feature_off_never_calls_classifier(db_session_sync, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", False)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock()
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(db_session_sync)

    _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    classifier.assert_not_called()
    mocks.resolver.assert_called_once()
    mocks.arabic_extractor.assert_not_called()
    mocks.repair.assert_called_once()


def test_english_route_calls_existing_resolver_only(db_session_sync, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock(
        return_value=_decision(
            DocumentRoute.ENGLISH, "english", "unknown", "ltr", 1.0
        )
    )
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(db_session_sync)

    _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    classifier.assert_called_once()
    mocks.resolver.assert_called_once()
    mocks.arabic_extractor.assert_not_called()
    mocks.gemini_factory.assert_not_called()
    mocks.gemma_factory.assert_not_called()
    mocks.repair.assert_called_once()
    stored = _stored_document(db_session_sync, document_id)
    assert stored["detected_language"] == "english"
    assert stored["text_direction"] == "ltr"
    assert stored["classification_source"] == "automatic"


def test_classifier_failure_on_clear_english_text_layer_continues_to_mineru(
    db_session_sync, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock(
        side_effect=ArabicClassifierUnavailable("secret document text from model")
    )
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(db_session_sync)
    pdf_path = _write_pdf_with_text(tmp_path / "english.pdf", "This is a clear English article with enough text.")

    pipeline_sync.run_pipeline_sync(
        db_session_sync,
        document_id=document_id,
        job_id=job_id,
        pdf_path=pdf_path,
    )

    classifier.assert_called_once()
    mocks.resolver.assert_called_once()
    mocks.arabic_extractor.assert_not_called()
    assert "secret document text" not in caplog.text
    stored = _stored_document(db_session_sync, document_id)
    assert stored["detected_language"] == "english"
    assert stored["text_direction"] == "ltr"


@pytest.mark.parametrize(
    "classifier_error",
    [
        ArabicClassifierUnavailable("local classifier unavailable"),
        ArabicClassifierResponseInvalid("bad local response"),
        ArabicClassifierInputError("unreadable classifier input"),
    ],
)
def test_classifier_failure_on_scanned_document_is_typed_and_actionable(
    db_session_sync, tmp_path, monkeypatch, classifier_error
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock(side_effect=classifier_error)
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(db_session_sync)
    pdf_path = _write_pdf_with_text(tmp_path / "scan.pdf", "")

    with pytest.raises(ArabicRoutingError) as raised:
        pipeline_sync.run_pipeline_sync(
            db_session_sync,
            document_id=document_id,
            job_id=job_id,
            pdf_path=pdf_path,
        )

    assert raised.value.error_code == "arabic_classifier_unavailable"
    assert "Ollama" in raised.value.public_message
    assert "model" in raised.value.public_message.lower()
    mocks.resolver.assert_not_called()
    mocks.arabic_extractor.assert_not_called()
    job = db_session_sync.execute(
        text("SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"),
        {"id": job_id},
    ).mappings().one()
    assert job["error_code"] == "arabic_classifier_unavailable"
    assert "Ollama" in job["error_message"]


def test_classification_read_transaction_is_closed_before_classifier_call(
    db_session_sync, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    transaction_states = []

    def classify(_pdf_path):
        transaction_states.append(db_session_sync.in_transaction())
        return _decision(DocumentRoute.ENGLISH, "english", "unknown", "ltr", 1.0)

    monkeypatch.setattr(pipeline_sync, "classify_document", classify)
    document_id, job_id = _seed(db_session_sync)
    pdf_path = _write_pdf_with_text(tmp_path / "english.pdf", "This is a clear English article with enough text.")

    pipeline_sync.run_pipeline_sync(
        db_session_sync,
        document_id=document_id,
        job_id=job_id,
        pdf_path=pdf_path,
    )

    assert transaction_states == [False]
    mocks.resolver.assert_called_once()


@pytest.mark.parametrize("language", ["arabic", "mixed"])
def test_printed_arabic_and_mixed_use_new_route_and_persist_summary(
    db_session_sync, tmp_path, monkeypatch, language
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock(
        return_value=_decision(
            DocumentRoute.ARABIC_PRINTED, language, "printed", "rtl"
        )
    )
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(db_session_sync)

    _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    mocks.resolver.assert_not_called()
    mocks.arabic_extractor.assert_called_once()
    assert mocks.arabic_extractor.call_args.kwargs["classification"].language == language
    mocks.gemini_factory.assert_called_once()
    mocks.gemma_factory.assert_called_once()
    mocks.repair.assert_not_called()
    stored = _stored_document(db_session_sync, document_id)
    assert stored["extractor"] == "gemini_arabic_flash"
    assert stored["detected_language"] == language
    assert stored["detected_writing_style"] == "printed"
    assert stored["text_direction"] == "rtl"
    assert stored["classification_source"] == "automatic"
    assert stored["ocr_provider_summary"] == [
        {"provider": "gemini_arabic_flash", "pages": [1]}
    ]


def test_disabled_handwriting_calls_no_ocr_and_keeps_source(
    db_session_sync, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    monkeypatch.setattr(pipeline_sync.settings, "arabic_handwritten_ocr_enabled", False)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_sync,
        "classify_document",
        MagicMock(
            return_value=_decision(
                DocumentRoute.ARABIC_HANDWRITTEN,
                "arabic",
                "handwritten",
                "rtl",
            )
        ),
    )
    document_id, job_id = _seed(db_session_sync)
    pdf_path = tmp_path / "handwritten.pdf"
    pdf_path.write_bytes(b"keep this original")

    with pytest.raises(HandwrittenArabicUnavailable):
        pipeline_sync.run_pipeline_sync(
            db_session_sync,
            document_id=document_id,
            job_id=job_id,
            pdf_path=pdf_path,
        )

    mocks.resolver.assert_not_called()
    mocks.arabic_extractor.assert_not_called()
    mocks.gemini_factory.assert_not_called()
    mocks.gemma_factory.assert_not_called()
    assert pdf_path.read_bytes() == b"keep this original"
    assert db_session_sync.execute(
        text("SELECT COUNT(*) FROM chunks WHERE document_id=:id"), {"id": document_id}
    ).scalar_one() == 0
    job = db_session_sync.execute(
        text("SELECT error_code, error_message FROM ingestion_jobs WHERE id=:id"),
        {"id": job_id},
    ).mappings().one()
    assert job["error_code"] == "handwritten_arabic_unavailable"
    assert "billing-enabled account" in job["error_message"]


def test_uncertain_style_requires_confirmation_without_ocr(
    db_session_sync, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_sync,
        "classify_document",
        MagicMock(
            return_value=_decision(
                DocumentRoute.ARABIC_STYLE_UNCERTAIN,
                "arabic",
                "mixed",
                "rtl",
                0.50,
            )
        ),
    )
    document_id, job_id = _seed(db_session_sync)

    with pytest.raises(ArabicStyleConfirmationRequired):
        _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    mocks.arabic_extractor.assert_not_called()
    mocks.gemini_factory.assert_not_called()
    mocks.gemma_factory.assert_not_called()
    job = db_session_sync.execute(
        text("SELECT error_code FROM ingestion_jobs WHERE id=:id"), {"id": job_id}
    ).mappings().one()
    assert job["error_code"] == "arabic_style_confirmation_required"


def test_user_confirmed_style_is_reused_without_reclassification(
    db_session_sync, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    classifier = MagicMock()
    monkeypatch.setattr(pipeline_sync, "classify_document", classifier)
    document_id, job_id = _seed(
        db_session_sync,
        classification={
            "source": "user_confirmed",
            "language": "mixed",
            "writing_style": "printed",
            "direction": "rtl",
            "classifier_model": "",
            "confidence": 0.0,
        },
    )

    _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    classifier.assert_not_called()
    mocks.arabic_extractor.assert_called_once()
    assert mocks.arabic_extractor.call_args.kwargs["classification"].route == DocumentRoute.ARABIC_PRINTED
    assert _stored_document(db_session_sync, document_id)["classification_source"] == "user_confirmed"
    assert _stored_document(db_session_sync, document_id)["classification_confidence"] is None


def test_handwritten_pro_flag_still_never_constructs_a_gemini_client(
    db_session_sync, tmp_path, monkeypatch
):
    from app.extraction.arabic_types import ArabicGeminiProNotConfigured

    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    monkeypatch.setattr(pipeline_sync.settings, "arabic_handwritten_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_sync,
        "classify_document",
        MagicMock(
            return_value=_decision(
                DocumentRoute.ARABIC_HANDWRITTEN,
                "arabic",
                "handwritten",
                "rtl",
            )
        ),
    )
    document_id, job_id = _seed(db_session_sync)

    with pytest.raises(ArabicGeminiProNotConfigured):
        _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    mocks.gemini_factory.assert_not_called()
    mocks.gemma_factory.assert_not_called()


def test_gemini_arabic_text_preserves_orthographic_codepoints(
    db_session_sync, tmp_path, monkeypatch
):
    monkeypatch.setattr(pipeline_sync.settings, "arabic_ocr_enabled", True)
    mocks = _pipeline_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_sync,
        "classify_document",
        MagicMock(
            return_value=_decision(
                DocumentRoute.ARABIC_PRINTED, "arabic", "printed", "rtl"
            )
        ),
    )
    document_id, job_id = _seed(db_session_sync)
    output_dir = tmp_path / "arabic-content"
    output_dir.mkdir()
    original = "أ إ آ ا نَصٌّ مدرسة ه ي ى"
    entries = pages_to_content_list(
        [
            ArabicOcrPage(
                page_number=1,
                raw_markdown=original,
                markdown=original,
                provider="gemini_arabic_flash",
                model="gemini-3.7-flash",
            )
        ]
    )
    (output_dir / "content_list.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    mocks.arabic_extractor.return_value.output_dir = output_dir
    monkeypatch.setattr(pipeline_sync, "find_content_list", lambda _path: output_dir / "content_list.json")
    captured_chunks = {}
    mocks.finish.side_effect = lambda _session, **kwargs: captured_chunks.update(kwargs)

    _run(db_session_sync, tmp_path, monkeypatch, document_id, job_id)

    plain_text = captured_chunks["chunks"][0]["plain_text"]
    assert plain_text == original
    mocks.repair.assert_not_called()
