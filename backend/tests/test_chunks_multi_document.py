"""get_all_chunks_for_documents: the study agent's multi-paper chunk load.

Replaces a loop of N single-document queries with one ANY(:ids) query,
grouped in Python — this confirms the grouping is correct (chunks land under
the right document, in sequence order) and that a document with zero chunks
still gets an entry rather than being silently dropped from the result dict,
which is what the old per-document loop always did.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database.repositories import chunks as chunk_repo
from app.database.repositories import documents as doc_repo


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _add_chunk(db_session, document_id: str, seq: int, text_body: str):
    await db_session.execute(
        text("""
            INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text)
            VALUES (:document_id, :seq, 'text', :body, :body)
        """),
        {"document_id": document_id, "seq": seq, "body": text_body},
    )


@pytest.mark.asyncio
async def test_groups_chunks_by_document_in_sequence_order(db_session):
    user = await _make_user(db_session)
    doc_a = await doc_repo.create_document(
        db_session, user_id=user, filename="a.pdf", original_filename="a.pdf"
    )
    doc_b = await doc_repo.create_document(
        db_session, user_id=user, filename="b.pdf", original_filename="b.pdf"
    )
    doc_c_empty = await doc_repo.create_document(
        db_session, user_id=user, filename="c.pdf", original_filename="c.pdf"
    )
    await _add_chunk(db_session, doc_a["id"], 2, "A second")
    await _add_chunk(db_session, doc_a["id"], 1, "A first")
    await _add_chunk(db_session, doc_b["id"], 1, "B only")
    await db_session.commit()

    result = await chunk_repo.get_all_chunks_for_documents(
        db_session, [doc_a["id"], doc_b["id"], doc_c_empty["id"]]
    )

    assert [c["plain_text"] for c in result[doc_a["id"]]] == ["A first", "A second"]
    assert [c["plain_text"] for c in result[doc_b["id"]]] == ["B only"]
    # A document with no chunks still gets an entry — the study agent
    # indexes chunks_by_doc[doc["id"]] for every paper in scope regardless.
    assert result[doc_c_empty["id"]] == []


@pytest.mark.asyncio
async def test_empty_document_id_list_returns_empty_dict(db_session):
    assert await chunk_repo.get_all_chunks_for_documents(db_session, []) == {}
