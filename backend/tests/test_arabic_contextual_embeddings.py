from app.core.config import settings
from app.embeddings.service_sync import (
    _embed_text_for_chunk,
    get_chunks_without_embeddings_sync,
)


def test_arabic_contextual_embeddings_are_enabled_by_default():
    assert settings.contextual_embeddings_arabic_enabled is True


def test_english_chunk_embedding_input_is_byte_identical(monkeypatch):
    monkeypatch.setattr(settings, "contextual_embeddings_arabic_enabled", True, raising=False)
    monkeypatch.setattr(settings, "embed_max_chars", 3000)
    chunk = {
        "plain_text": "  English body text.  ",
        "chunk_type": "text",
        "heading_path": ["Methods", "Embedding"],
        "title": "English Paper",
        "text_direction": "ltr",
        "detected_language": "english",
    }

    assert _embed_text_for_chunk(chunk) == "English body text."


def test_arabic_chunk_embedding_prepends_title_and_heading_within_cap(monkeypatch):
    monkeypatch.setattr(settings, "contextual_embeddings_arabic_enabled", True, raising=False)
    monkeypatch.setattr(settings, "embed_max_chars", 90)
    chunk = {
        "plain_text": "نص عربي مهم " * 20,
        "chunk_type": "text",
        "heading_path": ["الفصل الأول", "المنهجية"],
        "title": "دراسة الشبكات العصبية",
        "text_direction": "rtl",
        "detected_language": "arabic",
    }

    embedded = _embed_text_for_chunk(chunk)

    assert embedded.startswith(
        "العنوان: دراسة الشبكات العصبية | القسم: الفصل الأول › المنهجية\n\n"
    )
    assert "نص عربي مهم" in embedded
    assert len(embedded) <= 90
    assert chunk["plain_text"].startswith("نص عربي مهم")


def test_mixed_document_is_arabic_side_and_feature_can_be_disabled(monkeypatch):
    chunk = {
        "plain_text": "Arabic and English mixed passage.",
        "chunk_type": "text",
        "heading_path": "Results",
        "title": "Mixed study",
        "text_direction": "ltr",
        "detected_language": "mixed",
    }
    monkeypatch.setattr(settings, "contextual_embeddings_arabic_enabled", True, raising=False)

    embedded = _embed_text_for_chunk(chunk)

    assert embedded.startswith("العنوان: Mixed study | القسم: Results\n\n")
    monkeypatch.setattr(settings, "contextual_embeddings_arabic_enabled", False)
    assert _embed_text_for_chunk(chunk) == chunk["plain_text"]


def test_chunk_embedding_query_selects_context_without_changing_text(db_session_sync):
    from uuid import uuid4
    from sqlalchemy import text

    doc_id = uuid4()
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status, title, "
            "text_direction, detected_language) "
            "VALUES (:id, 'arabic.pdf', 'arabic.pdf', 'complete', 'عنوان', 'rtl', 'arabic')"
        ),
        {"id": doc_id},
    )
    db_session_sync.execute(
        text(
            "INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text, "
            "heading_path, token_count) VALUES (:id, 1, 'text', 'متن', 'متن', ARRAY['قسم'], 1)"
        ),
        {"id": doc_id},
    )
    db_session_sync.commit()

    chunks = get_chunks_without_embeddings_sync(db_session_sync, doc_id, limit=5)

    assert len(chunks) == 1
    assert chunks[0]["title"] == "عنوان"
    assert chunks[0]["heading_path"] == ["قسم"]
    assert chunks[0]["plain_text"] == "متن"
