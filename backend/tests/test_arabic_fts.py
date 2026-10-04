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


async def test_library_english_fulltext_finds_matching_document_and_scopes_user(db_session):
    user_ids = [uuid4(), uuid4()]
    document_ids = [uuid4(), uuid4()]
    for index, (user_id, document_id) in enumerate(zip(user_ids, document_ids)):
        await db_session.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
            {"id": user_id, "email": f"english-library-{index}-{user_id}@example.test"},
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
                VALUES (:document_id, 1, :content, :content)
            """),
            {
                "document_id": document_id,
                "content": "Transformers use self attention to process sequences.",
            },
        )
    await db_session.commit()

    results = await pgvector.search_documents_fulltext(
        db_session, user_ids[0], "transformer attention", limit=10
    )

    assert [row["id"] for row in results] == [document_ids[0]]
    assert results[0]["fts_rank"] > 0


@pytest.mark.parametrize(
    ("query", "language", "direction", "content"),
    [
        ("transformer attention", "english", "ltr", "Transformers use self attention."),
        ("شبكات عصبية", "arabic", "rtl", "الشبكات العصبية العميقة."),
    ],
)
async def test_library_fulltext_can_limit_results_to_documents_without_search_vectors(
    db_session, query, language, direction, content,
):
    user_id = uuid4()
    vector_document_id, vectorless_document_id = uuid4(), uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
        {"id": user_id, "email": f"vector-filter-{user_id}@example.test"},
    )
    vector = [0.25] + [0.0] * (pgvector.settings.vector_dimension - 1)
    vector_literal = "[" + ",".join(map(str, vector)) + "]"
    for document_id, has_vector in (
        (vector_document_id, True), (vectorless_document_id, False),
    ):
        if has_vector:
            await db_session.execute(
                text("""
                    INSERT INTO documents
                        (id, user_id, filename, original_filename, status,
                         detected_language, text_direction, search_embedding)
                    VALUES (:id, :user_id, 'fixture.pdf', 'fixture.pdf', 'complete',
                            :language, :direction, CAST(:embedding AS vector))
                """),
                {
                    "id": document_id, "user_id": user_id, "language": language,
                    "direction": direction, "embedding": vector_literal,
                },
            )
        else:
            await db_session.execute(
                text("""
                    INSERT INTO documents
                        (id, user_id, filename, original_filename, status,
                         detected_language, text_direction)
                    VALUES (:id, :user_id, 'fixture.pdf', 'fixture.pdf', 'complete',
                            :language, :direction)
                """),
                {
                    "id": document_id, "user_id": user_id, "language": language,
                    "direction": direction,
                },
            )
        await db_session.execute(
            text("""
                INSERT INTO chunks (document_id, sequence_id, markdown, plain_text)
                VALUES (:document_id, 1, :content, :content)
            """),
            {"document_id": document_id, "content": content},
        )
    await db_session.commit()

    rows = await pgvector.search_documents_fulltext(
        db_session, user_id, query, limit=10, missing_vectors_only=True
    )

    assert [row["id"] for row in rows] == [vectorless_document_id]


def test_arabic_stemming_is_enabled_by_default():
    assert getattr(pgvector.settings, "arabic_fts_stemming_enabled", None) is True


async def test_arabic_snowball_matches_surface_forms_and_exact_text_ranks_first(db_session):
    document_id, chunk_ids = await _add_document(
        db_session,
        chunks=[
            "ذهبت إلى بالمكتبة الجامعية",
            "في المكتبة الوطنية",
            "والمعلمين في المدارس",
            "قرأ الطلاب كتابهم",
            "للشبكات العصبية العميقة",
            "الشبكات العصبية",
            "شبكة عصب متقدمة",
        ],
    )

    for query, expected_id in (
        ("مكتبة", chunk_ids[0]),
        ("المكتبات", chunk_ids[1]),
        ("المعلمون", chunk_ids[2]),
        ("كتاب", chunk_ids[3]),
        ("شبكة عصبية", chunk_ids[4]),
    ):
        rows = await search_chunks_fulltext(db_session, query, limit=10, document_id=document_id)
        assert expected_id in [row["id"] for row in rows], query

    rows = await search_chunks_fulltext(
        db_session, "الشبكات العصبية", limit=10, document_id=document_id
    )
    ranked_ids = [row["id"] for row in rows]
    assert ranked_ids.index(chunk_ids[5]) < ranked_ids.index(chunk_ids[6])


def test_arabic_fts_v2_is_enabled_by_default():
    assert getattr(pgvector.settings, "arabic_fts_v2_enabled", None) is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ﻻ ﻷ ﻹ ﻵ", "لا لا لا لا"),
        ("أ إ آ ٱ ة ى ؤ ئ ی ک", "ا ا ا ا ه ي و ي ي ك"),
        ("١٢٣ ۱۲۳", "123 123"),
        ("كَاتِبـ", "كاتب"),
    ],
)
def test_python_arabic_v2_normalization_folds_presentation_forms_letters_and_digits(raw, expected):
    normalize = getattr(pgvector, "normalize_arabic_v2", None)
    assert callable(normalize), "the Python v2 normalizer must be available"
    assert normalize(raw) == expected


@pytest.mark.asyncio
async def test_sql_v2_normalization_matches_python_and_has_safe_function_properties(db_session):
    metadata = await db_session.execute(text("""
        SELECT p.provolatile::text, p.proisstrict, p.proparallel::text
        FROM pg_proc p
        WHERE p.oid = to_regprocedure('ar_normalize_v2(text)')
    """))
    properties = metadata.first()
    assert properties is not None, "ar_normalize_v2(text) must be installed"
    assert tuple(properties) == ("i", True, "s")

    normalize = getattr(pgvector, "normalize_arabic_v2", None)
    assert callable(normalize)
    for raw in ("ﻻ ﻷ ﻹ ﻵ", "أ إ آ ٱ ة ى ؤ ئ ی ک", "١٢٣ ۱۲۳", "كَاتِبـ"):
        result = await db_session.execute(
            text("SELECT ar_normalize_v2(:value)"), {"value": raw}
        )
        assert result.scalar_one() == normalize(raw)


@pytest.mark.asyncio
async def test_v2_simple_and_arabic_expression_indexes_are_installed(db_session):
    result = await db_session.execute(text("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE indexname IN ('idx_chunks_fts_ar_v2_simple', 'idx_chunks_fts_ar_v2_arabic')
    """))
    indexes = {row["indexname"]: row["indexdef"] for row in result.mappings().all()}

    assert set(indexes) == {"idx_chunks_fts_ar_v2_simple", "idx_chunks_fts_ar_v2_arabic"}
    assert all("USING gin" in definition and "ar_normalize_v2" in definition for definition in indexes.values())


@pytest.mark.asyncio
async def test_v2_query_normalization_matches_arabic_presentation_forms(db_session, monkeypatch):
    document_id, chunk_ids = await _add_document(db_session, chunks=["لاجل"])
    monkeypatch.setattr(pgvector.settings, "arabic_fts_stemming_enabled", False)

    rows = await search_chunks_fulltext(db_session, "ﻷجل", limit=10, document_id=document_id)

    assert [row["id"] for row in rows] == [chunk_ids[0]]


@pytest.mark.asyncio
async def test_arabic_library_fulltext_uses_the_stemming_leg(db_session):
    document_id, _ = await _add_document(db_session, chunks=["ذهبت إلى بالمكتبة الجامعية"])
    result = await db_session.execute(
        text("SELECT user_id FROM documents WHERE id = :id"), {"id": document_id}
    )
    user_id = result.scalar_one()

    rows = await pgvector.search_documents_fulltext(db_session, user_id, "مكتبة", limit=10)

    assert [row["id"] for row in rows] == [document_id]


@pytest.mark.asyncio
async def test_v2_disabled_keeps_the_legacy_exact_fts_expression(db_session, monkeypatch):
    document_id, _ = await _add_document(db_session, chunks=["الشبكات العصبية"])
    monkeypatch.setattr(pgvector.settings, "arabic_fts_stemming_enabled", False)
    monkeypatch.setattr(pgvector.settings, "arabic_fts_v2_enabled", False, raising=False)
    statements = []
    original_execute = db_session.execute

    async def capture(statement, *args, **kwargs):
        statements.append(str(statement).strip())
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", capture)
    await search_chunks_fulltext(db_session, "شبكات عصبية", limit=10, document_id=document_id)

    assert statements == ["""
            SELECT c.id, c.document_id, c.sequence_id, c.markdown, c.plain_text,
                   c.page_start, c.page_end, c.chunk_type,
                   ts_rank(
                       to_tsvector('simple', ar_normalize(coalesce(c.plain_text, ''))),
                       to_tsquery('simple', :q)
                   ) AS fts_rank
            FROM chunks c
            WHERE to_tsvector('simple', ar_normalize(coalesce(c.plain_text, '')))
                  @@ to_tsquery('simple', :q)
            AND c.document_id = :document_id
            ORDER BY fts_rank DESC
            LIMIT :limit
        """.strip()]
