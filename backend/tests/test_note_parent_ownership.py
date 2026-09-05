"""A follow-up note may only continue a thread on the SAME paper.

`parent_note_id` is a request body field on POST /papers/{id}/notes/stream,
so it is entirely client-supplied, and `note_repo.get_note` looks a note up
by id alone — it has no document or user filter, by design (every other
caller has already established ownership).

Without the check in create_note_stream, sending someone else's note id was
accepted: the endpoint read that note's whole thread via `get_note_thread`
and passed it to `answer_paper_question` as conversation context. That is
another user's questions and another user's document's answers, quoted into
a reply. It also inherited that note's scope, margin_side and model.

HTTP-level rather than repository-level (the narrow exception this suite
makes, see tests/README.md and test_auth_http.py) because the check lives in
the endpoint: there is no repository function to call. Nothing is mocked —
the rejection happens before any model call, so the request never reaches
one.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    """Same reason as test_auth_http.py's copy: the cached client is bound to
    the loop that made it, and pytest-asyncio gives each test its own."""
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()
    yield
    await r.flushdb()
    await r.aclose()
    redis_module._client = None


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _signup(client, email: str) -> httpx.AsyncClient:
    resp = await client.post(
        "/api/v1/auth/signup", json={"email": email, "password": "correct horse battery"}
    )
    assert resp.status_code == 201
    return client


async def _make_paper_with_note(db_session, user_id, *, question: str):
    """A document owned by `user_id` plus one root note on it."""
    doc = await db_session.execute(
        text("""
            INSERT INTO documents (user_id, filename, original_filename, status)
            VALUES (:uid, :fn, :fn, 'complete') RETURNING id
        """),
        {"uid": user_id, "fn": f"{uuid4()}.pdf"},
    )
    document_id = doc.scalar_one()
    note = await db_session.execute(
        text("""
            INSERT INTO paper_notes (document_id, anchor_sequence_id, anchor_kind, question)
            VALUES (:doc, 1, 'text', :q) RETURNING id
        """),
        {"doc": document_id, "q": question},
    )
    note_id = note.scalar_one()
    await db_session.commit()
    return document_id, note_id


async def _user_id(db_session, email: str):
    result = await db_session.execute(
        text("SELECT id FROM users WHERE LOWER(email) = :e"), {"e": email.lower()}
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_a_follow_up_cannot_adopt_another_users_note_as_its_parent(client, db_session):
    victim_email, attacker_email = f"{uuid4()}@example.com", f"{uuid4()}@example.com"

    # The victim's paper, carrying a note whose text must never be quoted back
    # to anyone else.
    await _signup(client, victim_email)
    _, victim_note_id = await _make_paper_with_note(
        db_session, await _user_id(db_session, victim_email),
        question="what does the confidential appendix say",
    )

    # A separate session for the attacker, with their own paper.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as attacker:
        await _signup(attacker, attacker_email)
        attacker_doc_id, _ = await _make_paper_with_note(
            db_session, await _user_id(db_session, attacker_email), question="mine",
        )

        resp = await attacker.post(
            f"/api/v1/papers/{attacker_doc_id}/notes/stream",
            json={
                "question": "go on",
                "anchor": {"kind": "text", "sequence_id": 1},
                "parent_note_id": str(victim_note_id),
            },
        )

    assert resp.status_code == 404

    # And nothing was written: no note row may reference that parent.
    leaked = await db_session.execute(
        text("SELECT COUNT(*) FROM paper_notes WHERE parent_note_id = :p"),
        {"p": victim_note_id},
    )
    assert leaked.scalar_one() == 0


@pytest.mark.asyncio
async def test_a_parent_id_that_does_not_exist_is_the_same_404(client, db_session):
    """Not a 500 from the foreign key, and not a silently-ignored parent that
    quietly starts a new root thread instead of continuing one."""
    email = f"{uuid4()}@example.com"
    await _signup(client, email)
    doc_id, _ = await _make_paper_with_note(
        db_session, await _user_id(db_session, email), question="mine",
    )

    resp = await client.post(
        f"/api/v1/papers/{doc_id}/notes/stream",
        json={
            "question": "go on",
            "anchor": {"kind": "text", "sequence_id": 1},
            "parent_note_id": str(uuid4()),
        },
    )
    assert resp.status_code == 404
