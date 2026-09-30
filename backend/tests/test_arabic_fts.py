from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.services import retrieval
from app.database import pgvector
from app.database.pgvector import search_chunks_fulltext


async def _add_document(session, *, language="arabic", direction="rtl", chunks):
    user_id = uuid4()
    document_id = uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
        {"id": user_id, "email": f"{user_id}@example.test"},
    )
    await session.execute(
        text("""
            INSERT INTO documents
                (id, user_id, filename, original_filename, status,
                 detected_language, text_direction)
            VALUES (:id, :user_id, 'fixture.pdf', 'fixture.pdf', 'complete',
                    :language, :direction)
        """),
        {
            "id": document_id,
            "user_id": user_id,
            "language": language,
            "direction": direction,
        },
    )
    chunk_ids = []
    for sequence_id, content in enumerate(chunks, start=1):
        chunk_id = uuid4()
        chunk_ids.append(chunk_id)
        await session.execute(
            text("""
                INSERT INTO chunks (id, document_id, sequence_id, markdown, plain_text)
                VALUES (:id, :document_id, :sequence_id, :content, :content)
            """),
            {
                "id": chunk_id,
                "document_id": document_id,
                "sequence_id": sequence_id,
                "content": content,
            },
        )
    await session.commit()
    return document_id, chunk_ids


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("الذّكاء الاصطناعيّ", "الذكاء الاصطناعي"),
        ("أ إ آ ٱ", "ا ا ا ا"),
        ("ة ى", "ه ي"),
        ("الـبحث", "البحث"),
    ],
)
async def test_ar_normalize_folds_arabic_spellings(db_session, raw, expected):
    result = await db_session.execute(text("SELECT ar_normalize(:value)"), {"value": raw})

    assert result.scalar_one() == expected


async def test_arabic_fts_matches_articles_and_diacritics(db_session):
    document_id, chunk_ids = await _add_document(
        db_session,
        chunks=["الشبكات العصبية", "الذّكاء الاصطناعيّ", "أبحاث متقدمة"],
    )

    neural = await search_chunks_fulltext(db_session, "شبكات عصبية", document_id=document_id)
    intelligence = await search_chunks_fulltext(db_session, "ذكاء اصطناعي", document_id=document_id)
    stripped_article = await search_chunks_fulltext(db_session, "الأبحاث", document_id=document_id)

    assert chunk_ids[0] in [row["id"] for row in neural]
    assert chunk_ids[1] in [row["id"] for row in intelligence]
    assert chunk_ids[2] in [row["id"] for row in stripped_article]


async def test_arabic_stopword_only_query_does_not_issue_invalid_tsquery(db_session, monkeypatch):
    calls = []
    execute = db_session.execute

    async def capture(statement, *args, **kwargs):
        calls.append(str(statement))
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", capture)

    rows = await search_chunks_fulltext(
        db_session, "في من على إلى عن", document_id=uuid4()
    )

    assert rows == []
    assert calls == []


async def test_arabic_fts_expression_index_is_created(db_session):
    result = await db_session.execute(text("""
        SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_chunks_fts_ar'
    """))

    indexdef = result.scalar_one()
    assert "USING gin" in indexdef
    assert "to_tsvector('simple'::regconfig, ar_normalize(COALESCE(plain_text, ''::text)))" in indexdef


async def test_english_document_keeps_existing_fts_sql_and_does_not_translate(
    db_session, monkeypatch,
):
    document_id, chunk_ids = await _add_document(
        db_session,
        language="english",
        direction="ltr",
        chunks=["Attention Is All You Need introduces the Transformer architecture."],
    )
    monkeypatch.setattr(retrieval, "get_query_embedding", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(retrieval.emb_repo, "search_embeddings", AsyncMock(return_value=[]))
    translate = AsyncMock(return_value="translated")
    monkeypatch.setattr(retrieval, "translated_query", translate)
    statements = []
    execute = db_session.execute

    async def capture(statement, *args, **kwargs):
        if str(statement).lstrip().startswith("SELECT c.id, c.document_id"):
            statements.append(str(statement).strip())
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", capture)

    rows = await retrieval.search_chunks(
        db_session, "transformer architecture", document_id=document_id
    )

    translate.assert_not_awaited()
    assert [row["id"] for row in rows] == [chunk_ids[0]]
    assert statements == ["""
            SELECT c.id, c.document_id, c.sequence_id, c.markdown, c.plain_text,
                   c.page_start, c.page_end, c.chunk_type,
                   ts_rank(
                       to_tsvector('english', coalesce(c.plain_text, '')),
                       websearch_to_tsquery('english', :q)
                   ) AS fts_rank
            FROM chunks c
            WHERE to_tsvector('english', coalesce(c.plain_text, ''))
                  @@ websearch_to_tsquery('english', :q)
            AND c.document_id = :document_id
            ORDER BY fts_rank DESC
            LIMIT :limit
        """.strip()]


async def test_library_arabic_fulltext_is_scoped_to_the_requesting_user(db_session):
    user_ids = [uuid4(), uuid4()]
    document_ids = [uuid4(), uuid4()]
    for index, (user_id, document_id) in enumerate(zip(user_ids, document_ids)):
        await db_session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
            {"id": user_id, "email": f"library-{index}-{user_id}@example.test"},
        )
        await db_session.execute(
            text("""
                INSERT INTO documents (id, user_id, filename, original_filename, status)
                VALUES (:id, :user_id, 'paper.pdf', 'paper.pdf', 'complete')
            """),
            {"id": document_id, "user_id": user_id},
        )
        await db_session.execute(
            text("""
                INSERT INTO chunks (document_id, sequence_id, markdown, plain_text)
                VALUES (:document_id, 1, 'الشبكات العصبية', 'الشبكات العصبية')
            """),
            {"document_id": document_id},
        )
    await db_session.commit()

    search_documents = getattr(pgvector, "search_documents_fulltext")
    results = await search_documents(db_session, user_ids[0], "شبكات عصبية", limit=10)

    assert [row["id"] for row in results] == [document_ids[0]]
