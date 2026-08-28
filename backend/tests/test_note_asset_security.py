"""The check standing between a note's client-supplied image_url and reading
an arbitrary file off the server's disk.

anchor.image_url is attacker-controlled (it's a request body field on
POST /papers/{id}/notes/stream): _to_storage_path strips this app's own
/static/images/ prefix, and file_path_belongs_to_document confirms what's
left actually names a chunk_assets row owned by THIS document, before
build_multimodal_messages is ever allowed to open it. Neither check alone
is redundant — see notes.py's _to_storage_path docstring.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.api.v1.endpoints.notes import _to_storage_path
from app.database.repositories import assets as asset_repo
from app.database.repositories import documents as doc_repo


def test_to_storage_path_only_accepts_the_apps_own_url_shape():
    assert _to_storage_path("/static/images/doc-id/fig.png") == "doc-id/fig.png"
    assert _to_storage_path(None) is None
    assert _to_storage_path("") is None
    # Everything below is what an attacker controlling this field would try —
    # none of it starts with the one prefix this app ever generates.
    assert _to_storage_path("/etc/passwd") is None
    assert _to_storage_path("/app/backend/.env") is None
    assert _to_storage_path("../../../../etc/passwd") is None
    assert _to_storage_path("relative/but/not/prefixed.png") is None
    # Even a crafted string starting with the right prefix but escaping via
    # `..` afterward is stripped to a value that still won't match a real
    # asset — file_path_belongs_to_document is what actually rejects it.
    assert _to_storage_path("/static/images/../../../../etc/passwd") == "../../../../etc/passwd"


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _make_chunk(db_session, document_id: str, seq: int = 1) -> str:
    result = await db_session.execute(
        text("""
            INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text)
            VALUES (:document_id, :seq, 'figure', 'a figure', 'a figure')
            RETURNING id
        """),
        {"document_id": document_id, "seq": seq},
    )
    await db_session.commit()
    return result.scalar_one()


@pytest.mark.asyncio
async def test_file_path_belongs_to_document_accepts_real_owned_asset(db_session):
    user = await _make_user(db_session)
    doc = await doc_repo.create_document(
        db_session, user_id=user, filename="a.pdf", original_filename="a.pdf"
    )
    chunk_id = await _make_chunk(db_session, doc["id"])
    asset = await asset_repo.create_asset(
        db_session, chunk_id=chunk_id, asset_type="figure",
        file_path=f"{doc['id']}/fig-1.png",
    )
    await db_session.commit()

    assert await asset_repo.file_path_belongs_to_document(
        db_session, asset["file_path"], doc["id"]
    ) is True


@pytest.mark.asyncio
async def test_file_path_belongs_to_document_rejects_cross_document_asset(db_session):
    """The exact IDOR this check exists to close: an asset that is real, but
    belongs to a DIFFERENT document than the one the note is being asked
    about, must not be readable through this paper's note endpoint."""
    user = await _make_user(db_session)
    doc_a = await doc_repo.create_document(
        db_session, user_id=user, filename="a.pdf", original_filename="a.pdf"
    )
    doc_b = await doc_repo.create_document(
        db_session, user_id=user, filename="b.pdf", original_filename="b.pdf"
    )
    chunk_a = await _make_chunk(db_session, doc_a["id"])
    asset_a = await asset_repo.create_asset(
        db_session, chunk_id=chunk_a, asset_type="figure",
        file_path=f"{doc_a['id']}/fig-1.png",
    )
    await db_session.commit()

    # Real asset, real path — but for doc_a, not doc_b.
    assert await asset_repo.file_path_belongs_to_document(
        db_session, asset_a["file_path"], doc_b["id"]
    ) is False


@pytest.mark.asyncio
async def test_file_path_belongs_to_document_rejects_forged_paths(db_session):
    user = await _make_user(db_session)
    doc = await doc_repo.create_document(
        db_session, user_id=user, filename="a.pdf", original_filename="a.pdf"
    )
    await db_session.commit()

    for forged in (
        "/etc/passwd",
        "../../../../etc/passwd",
        f"{doc['id']}/nonexistent.png",
        "some-other-doc-id/fig-1.png",
    ):
        assert await asset_repo.file_path_belongs_to_document(
            db_session, forged, doc["id"]
        ) is False
