"""The library's "Done reading" shelf: documents.done_at / done_folder.

A finished book leaves the reading shelf without leaving the library —
nothing about the document changes but where the library shows it. These
tests pin the endpoint contract (PATCH /papers/{id}/done, PATCH
/papers/done-folders) and the two invariants that make folders safe to keep
implicit: bringing a document back clears its folder, and renaming a folder
touches only this user's documents.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis():
    # Sessions live in Redis; a client from a previous test must not be
    # signed in here. Same shape as test_strict_document_scope.
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


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
        {"e": f"{uuid4()}@example.com"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _make_doc(db_session, user_id) -> str:
    result = await db_session.execute(
        text("""
            INSERT INTO documents (user_id, filename, original_filename, status)
            VALUES (:u, :f, :f, 'complete') RETURNING id
        """),
        {"u": user_id, "f": f"{uuid4()}.pdf"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _signed_up(client) -> str:
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _shelf(db_session, doc_id):
    row = await db_session.execute(
        text("SELECT done_at, done_folder FROM documents WHERE id = :id"), {"id": doc_id}
    )
    return row.one()


@pytest.mark.asyncio
async def test_a_new_document_is_on_the_reading_shelf(db_session):
    user_id = await _make_user(db_session)
    doc_id = await _make_doc(db_session, user_id)
    done_at, folder = await _shelf(db_session, doc_id)
    assert done_at is None and folder is None


@pytest.mark.asyncio
async def test_mark_done_into_a_folder_then_bring_back(client, db_session):
    user_id = await _signed_up(client)
    doc_id = await _make_doc(db_session, user_id)

    resp = await client.patch(
        f"/api/v1/papers/{doc_id}/done", json={"done": True, "folder": "  Technical Books "}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["done_at"] is not None
    assert body["done_folder"] == "Technical Books"  # trimmed
    first_done_at, _ = await _shelf(db_session, doc_id)

    # Moving between folders keeps the original done_at: "when did I finish
    # this" is not "when did I last tidy the shelf".
    resp = await client.patch(f"/api/v1/papers/{doc_id}/done", json={"done": True, "folder": None})
    assert resp.status_code == 200
    assert resp.json()["done_folder"] is None
    done_at, folder = await _shelf(db_session, doc_id)
    assert done_at == first_done_at and folder is None

    # Back to the reading shelf clears both — and the list endpoint agrees.
    resp = await client.patch(f"/api/v1/papers/{doc_id}/done", json={"done": False, "folder": "ignored"})
    assert resp.status_code == 200
    assert resp.json()["done_at"] is None and resp.json()["done_folder"] is None
    listing = await client.get("/api/v1/papers")
    (row,) = [d for d in listing.json()["documents"] if d["id"] == str(doc_id)]
    assert row["done_at"] is None and row["done_folder"] is None


@pytest.mark.asyncio
async def test_done_is_a_shelf_label_not_a_lifecycle_state(client, db_session):
    """A done document is still listed, still complete, still openable."""
    user_id = await _signed_up(client)
    doc_id = await _make_doc(db_session, user_id)
    await client.patch(f"/api/v1/papers/{doc_id}/done", json={"done": True})
    listing = await client.get("/api/v1/papers")
    (row,) = [d for d in listing.json()["documents"] if d["id"] == str(doc_id)]
    assert row["status"] == "complete" and row["done_at"] is not None
    assert (await client.get(f"/api/v1/papers/{doc_id}")).status_code == 200


@pytest.mark.asyncio
async def test_the_endpoint_404s_for_someone_elses_document(client, db_session):
    victim_id = await _make_user(db_session)
    doc_id = await _make_doc(db_session, victim_id)
    await _signed_up(client)
    resp = await client.patch(f"/api/v1/papers/{doc_id}/done", json={"done": True})
    assert resp.status_code == 404
    done_at, _ = await _shelf(db_session, doc_id)
    assert done_at is None


@pytest.mark.asyncio
async def test_rename_folder_moves_every_document_in_it_and_only_mine(client, db_session):
    user_id = await _signed_up(client)
    a = await _make_doc(db_session, user_id)
    b = await _make_doc(db_session, user_id)
    c = await _make_doc(db_session, user_id)
    for doc_id in (a, b):
        await client.patch(f"/api/v1/papers/{doc_id}/done", json={"done": True, "folder": "Tech"})
    await client.patch(f"/api/v1/papers/{c}/done", json={"done": True, "folder": "Novels"})

    # Someone else's "Tech" folder must not be touched.
    other = await _make_user(db_session)
    theirs = await _make_doc(db_session, other)
    await db_session.execute(
        text("UPDATE documents SET done_at = NOW(), done_folder = 'Tech' WHERE id = :id"),
        {"id": theirs},
    )
    await db_session.commit()

    resp = await client.patch(
        "/api/v1/papers/done-folders", json={"from": "Tech", "to": "Technical Books"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"moved": 2, "folder": "Technical Books"}
    assert (await _shelf(db_session, a))[1] == "Technical Books"
    assert (await _shelf(db_session, b))[1] == "Technical Books"
    assert (await _shelf(db_session, c))[1] == "Novels"
    assert (await _shelf(db_session, theirs))[1] == "Tech"

    # Nothing left called "Tech" for this user → 404, not a silent no-op.
    resp = await client.patch("/api/v1/papers/done-folders", json={"from": "Tech", "to": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_folder_names_are_bounded(client, db_session):
    user_id = await _signed_up(client)
    doc_id = await _make_doc(db_session, user_id)
    resp = await client.patch(
        f"/api/v1/papers/{doc_id}/done", json={"done": True, "folder": "x" * 81}
    )
    assert resp.status_code == 422
