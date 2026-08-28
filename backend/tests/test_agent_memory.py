"""agent_memories: the vector store behind chat/memory.py.

Repository-level, matching this suite's usual convention (real Postgres, no
HTTP layer, no live embedding provider) — see tests/test_vector_retrieval.py
for the identical pattern applied to chunk_embeddings. Hand-built vectors
stand in for a real embedding model: orthogonal vectors are "unrelated",
near-identical vectors are "the same thing said again".
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.database.pgvector import find_similar_memory, insert_memory, search_memories


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _make_document(db_session) -> str:
    result = await db_session.execute(
        text(
            "INSERT INTO documents (filename, original_filename, status) "
            "VALUES ('doc.pdf', 'doc.pdf', 'queued') RETURNING id"
        ),
    )
    await db_session.commit()
    return result.scalar_one()


async def _live_dimension(db_session) -> int:
    """Same adaptation test_vector_retrieval.py does — the column is only
    re-typed to settings.vector_dimension by the app's startup migration."""
    result = await db_session.execute(
        text("""
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = 'agent_memories'::regclass
              AND attname = 'embedding' AND NOT attisdropped
        """)
    )
    dims = result.scalar_one_or_none()
    return dims if dims and dims > 0 else settings.vector_dimension


def _vector(dim: int, hot_index: int) -> list[float]:
    """A unit vector with a single 1.0 at hot_index — orthogonal to any other
    unit vector built the same way with a different index, cosine similarity
    ~1.0 to a call with the same index."""
    v = [0.0] * dim
    v[hot_index] = 1.0
    return v


def _near(v: list[float]) -> list[float]:
    """A small perturbation of v — same "topic", not byte-identical."""
    out = list(v)
    out[0] += 0.05
    return out


@pytest.mark.asyncio
async def test_search_memories_scoping_and_similarity(db_session):
    dim = await _live_dimension(db_session)
    user = await _make_user(db_session)
    doc_a = await _make_document(db_session)
    doc_b = await _make_document(db_session)

    global_vec = _vector(dim, 0)
    doc_a_vec = _vector(dim, 1)
    unrelated_vec = _vector(dim, 2)

    await insert_memory(
        db_session, user, None, "reader prefers short answers", "explicit",
        global_vec, settings.embedding_model,
    )
    await insert_memory(
        db_session, user, doc_a, "confused about chapter 3's timeline", "explicit",
        doc_a_vec, settings.embedding_model,
    )
    await db_session.commit()

    # Querying with no document scope: only the global memory is in reach.
    global_hits = await search_memories(
        db_session, user, global_vec, limit=5, document_id=None, min_similarity=0.1,
    )
    assert [h["body"] for h in global_hits] == ["reader prefers short answers"]

    # Querying scoped to doc_a: both the global memory AND doc_a's are in
    # reach (each queried with ITS OWN vector so both clear the similarity
    # floor), but neither is visible from an unrelated document.
    a_hits_for_global_vec = await search_memories(
        db_session, user, global_vec, limit=5, document_id=doc_a, min_similarity=0.1,
    )
    assert "reader prefers short answers" in [h["body"] for h in a_hits_for_global_vec]

    a_hits_for_doc_vec = await search_memories(
        db_session, user, doc_a_vec, limit=5, document_id=doc_a, min_similarity=0.1,
    )
    assert "confused about chapter 3's timeline" in [h["body"] for h in a_hits_for_doc_vec]

    b_hits_for_doc_a_vec = await search_memories(
        db_session, user, doc_a_vec, limit=5, document_id=doc_b, min_similarity=0.1,
    )
    assert b_hits_for_doc_a_vec == []  # doc_a's memory never leaks into doc_b

    # An unrelated query vector clears no similarity floor worth keeping.
    unrelated_hits = await search_memories(
        db_session, user, unrelated_vec, limit=5, document_id=None, min_similarity=0.45,
    )
    assert unrelated_hits == []


@pytest.mark.asyncio
async def test_find_similar_memory_dedup_is_scope_exact(db_session):
    dim = await _live_dimension(db_session)
    user = await _make_user(db_session)
    doc_a = await _make_document(db_session)

    vec = _vector(dim, 0)
    await insert_memory(
        db_session, user, None, "reader prefers short answers", "explicit",
        vec, settings.embedding_model,
    )
    await db_session.commit()

    # A near-identical global candidate is caught as a duplicate.
    dup = await find_similar_memory(db_session, user, None, _near(vec), min_similarity=0.92)
    assert dup is not None
    assert dup["body"] == "reader prefers short answers"

    # The SAME near-identical vector, scoped to a document, is NOT a
    # duplicate — IS NOT DISTINCT FROM must not treat NULL and a real
    # document_id as matching scopes.
    scoped = await find_similar_memory(db_session, user, doc_a, _near(vec), min_similarity=0.92)
    assert scoped is None

    # An unrelated vector in the same (global) scope is not a duplicate either.
    unrelated = await find_similar_memory(
        db_session, user, None, _vector(dim, 5), min_similarity=0.92
    )
    assert unrelated is None
