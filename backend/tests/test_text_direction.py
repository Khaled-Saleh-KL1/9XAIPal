"""_mark_rtl_if_arabic: with the Arabic OCR route switched off,
documents.text_direction stays NULL for every document — including a
legitimately-Arabic one extracted through the ordinary, non-Arabic-aware
path — and the reader defaults to left-to-right. This is a cheap backstop
that flips the direction to 'rtl' once the persisted chunk text is
predominantly Arabic-script, without ever overwriting a direction someone
(or the Arabic route) already set.
"""

from uuid import uuid4

from sqlalchemy import text

from app.extraction.pipeline_sync import _mark_rtl_if_arabic

# 60 Arabic letters (well over the 50-letter floor), 100% Arabic.
ARABIC_TEXT = "ا" * 60
# 60 plain-ASCII letters, 0% Arabic.
ENGLISH_TEXT = "a" * 60
# 60 letters total, 12 Arabic (20%) — under the 30% ratio floor.
MIXED_20_PERCENT = ("ا" * 12) + ("a" * 48)
# 10 Arabic letters — under the 50-letter floor regardless of ratio.
TOO_FEW_LETTERS = "ا" * 10


def _insert_document(session, direction=None):
    doc_id = uuid4()
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status, text_direction) "
            "VALUES (:id, 'x.pdf', 'x.pdf', 'processing', :direction)"
        ),
        {"id": doc_id, "direction": direction},
    )
    session.commit()
    return doc_id


def _direction(session, doc_id):
    row = session.execute(
        text("SELECT text_direction FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().first()
    return row["text_direction"]


def test_predominantly_arabic_text_marks_the_document_rtl(db_session_sync):
    doc_id = _insert_document(db_session_sync)
    _mark_rtl_if_arabic(db_session_sync, doc_id, [ARABIC_TEXT])
    db_session_sync.commit()
    assert _direction(db_session_sync, doc_id) == "rtl"


def test_english_text_stays_null_and_issues_no_update(db_session_sync, monkeypatch):
    doc_id = _insert_document(db_session_sync)

    real_execute = db_session_sync.execute
    issued = []

    def spy_execute(clause, *args, **kwargs):
        issued.append(str(clause))
        return real_execute(clause, *args, **kwargs)

    monkeypatch.setattr(db_session_sync, "execute", spy_execute)

    _mark_rtl_if_arabic(db_session_sync, doc_id, [ENGLISH_TEXT])
    db_session_sync.commit()

    assert _direction(db_session_sync, doc_id) is None
    assert not any("UPDATE documents" in sql for sql in issued)


def test_a_document_already_ltr_stays_ltr_even_with_arabic_text(db_session_sync):
    doc_id = _insert_document(db_session_sync, direction="ltr")
    _mark_rtl_if_arabic(db_session_sync, doc_id, [ARABIC_TEXT])
    db_session_sync.commit()
    assert _direction(db_session_sync, doc_id) == "ltr"


def test_20_percent_arabic_stays_null():
    """Below the 30% ratio floor — not predominantly Arabic."""
    from unittest.mock import MagicMock

    session = MagicMock()
    _mark_rtl_if_arabic(session, uuid4(), [MIXED_20_PERCENT])
    session.execute.assert_not_called()


def test_fewer_than_50_letters_stays_null_even_if_all_arabic():
    from unittest.mock import MagicMock

    session = MagicMock()
    _mark_rtl_if_arabic(session, uuid4(), [TOO_FEW_LETTERS])
    session.execute.assert_not_called()
