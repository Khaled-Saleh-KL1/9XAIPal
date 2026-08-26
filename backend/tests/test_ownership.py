"""Ownership-filtering tests: user A must never see user B's rows.

Repository-level, matching this suite's usual convention (real Postgres, no
HTTP layer) — see tests/README.md. The one deliberate exception is
tests/test_auth_http.py, which covers the cookie wire-protocol specifically.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database.repositories import documents as doc_repo
from app.database.repositories import studies as study_repo
from app.database.repositories import stickies as sticky_repo


async def _make_user(db_session, email: str | None = None):
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": email or f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


@pytest.mark.asyncio
async def test_list_and_get_document_scoped_to_owner(db_session):
    user_a = await _make_user(db_session)
    user_b = await _make_user(db_session)

    doc_a = await doc_repo.create_document(
        db_session, user_id=user_a, filename="a.pdf", original_filename="a.pdf"
    )
    await db_session.commit()

    # B's list is empty; A's list has exactly the one document.
    assert await doc_repo.list_documents(db_session, user_b) == []
    a_list = await doc_repo.list_documents(db_session, user_a)
    assert len(a_list) == 1
    assert a_list[0]["id"] == doc_a["id"]

    # B can't fetch A's document by id — not a 403, just not found.
    assert await doc_repo.get_document(db_session, doc_a["id"], user_b) is None
    assert await doc_repo.get_document(db_session, doc_a["id"], user_a) is not None

    # B can't rename or delete it either.
    assert await doc_repo.set_document_title(db_session, doc_a["id"], user_b, "hijacked") is False
    assert await doc_repo.delete_document(db_session, doc_a["id"], user_b) is False
    # A still can.
    assert await doc_repo.delete_document(db_session, doc_a["id"], user_a) is True


@pytest.mark.asyncio
async def test_filter_owned_document_ids_drops_foreign_ids(db_session):
    user_a = await _make_user(db_session)
    user_b = await _make_user(db_session)

    doc_a = await doc_repo.create_document(
        db_session, user_id=user_a, filename="a.pdf", original_filename="a.pdf"
    )
    doc_b = await doc_repo.create_document(
        db_session, user_id=user_b, filename="b.pdf", original_filename="b.pdf"
    )
    await db_session.commit()

    owned = await doc_repo.filter_owned_document_ids(
        db_session, [doc_a["id"], doc_b["id"]], user_a
    )
    assert owned == [doc_a["id"]]


@pytest.mark.asyncio
async def test_study_membership_cannot_smuggle_foreign_document(db_session):
    """The endpoint layer filters document_ids through filter_owned_document_ids
    before calling set_study_papers — this confirms what happens if it didn't:
    set_study_papers itself trusts its input, so the guard has to be upstream."""
    user_a = await _make_user(db_session)
    user_b = await _make_user(db_session)

    doc_b = await doc_repo.create_document(
        db_session, user_id=user_b, filename="b.pdf", original_filename="b.pdf"
    )
    study_a = await study_repo.create_study(db_session, user_id=user_a, name="A's study")
    await db_session.commit()

    # The endpoint-layer guard: only owned ids should ever reach set_study_papers.
    owned = await doc_repo.filter_owned_document_ids(db_session, [doc_b["id"]], user_a)
    assert owned == []  # B's document is correctly excluded

    papers = await study_repo.set_study_papers(db_session, study_a["id"], owned)
    assert papers == []


@pytest.mark.asyncio
async def test_sticky_notes_scoped_to_owner(db_session):
    user_a = await _make_user(db_session)
    user_b = await _make_user(db_session)

    sticky_a = await sticky_repo.create_sticky(db_session, user_id=user_a, body="A's note")
    await db_session.commit()

    assert await sticky_repo.list_stickies(db_session, user_id=user_b) == []
    a_list = await sticky_repo.list_stickies(db_session, user_id=user_a)
    assert len(a_list) == 1

    assert await sticky_repo.get_sticky(db_session, sticky_a["id"], user_b) is None
    assert await sticky_repo.get_sticky(db_session, sticky_a["id"], user_a) is not None

    # B can't delete or update it.
    assert await sticky_repo.delete_sticky(db_session, sticky_a["id"], user_b) is False
    assert await sticky_repo.update_sticky(db_session, sticky_a["id"], user_b, body="hijacked") is None


@pytest.mark.asyncio
async def test_library_wide_chat_scoped_to_owner(db_session):
    """The specific leak this retrofit fixes: _resolve_scope's LIBRARY branch
    (studies.py endpoint) now filters list_documents by user_id, and the
    library-wide conversation_turns (study_id IS NULL) require user_id
    directly since there's no parent row to derive it from."""
    user_a = await _make_user(db_session)
    user_b = await _make_user(db_session)

    doc_a = await doc_repo.create_document(
        db_session, user_id=user_a, filename="a.pdf", original_filename="a.pdf"
    )
    await db_session.execute(
        text("UPDATE documents SET status = 'complete' WHERE id = :id"),
        {"id": doc_a["id"]},
    )
    await db_session.commit()

    # A's "whole library" (list_documents with no study) includes their doc.
    a_docs = await doc_repo.list_documents(db_session, user_a)
    assert len(a_docs) == 1
    # B's "whole library" is empty — this is the exact query _resolve_scope's
    # LIBRARY branch runs; before the fix it had no user_id filter at all and
    # would have returned A's document here too.
    b_docs = await doc_repo.list_documents(db_session, user_b)
    assert b_docs == []

    # Library-wide chat turns (study_id=None) are also isolated per user.
    turn = await study_repo.add_turn(
        db_session, user_id=user_a, study_id=None, conversation_id=uuid4(),
        role="user", content="What's in my library?",
    )
    await db_session.commit()
    assert len(await study_repo.list_turns(db_session, user_a, None)) == 1
    assert await study_repo.list_turns(db_session, user_b, None) == []
