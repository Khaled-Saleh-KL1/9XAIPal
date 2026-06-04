import pytest
from uuid import uuid4
from sqlalchemy import text
from app.database.repositories.embeddings import search_embeddings
from app.database.pgvector import insert_embedding


@pytest.mark.asyncio
async def test_search_similar_chunks_success(db_session):
    doc_id = uuid4()
    chunk_id = uuid4()

    # 1. Insert fake document and chunk
    await db_session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'doc.pdf', 'doc.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO chunks (id, document_id, sequence_id, chunk_type, markdown, plain_text, token_count)
            VALUES (:id, :document_id, 1, 'text', 'Test text content', 'Test text content', 3)
        """),
        {"id": chunk_id, "document_id": doc_id}
    )
    await db_session.commit()

    # 2. Insert fake embedding
    fake_vector = [0.1] * 768
    await insert_embedding(
        db_session,
        chunk_id=chunk_id,
        embedding=fake_vector,
        model_name="nomic-embed-text",
    )
    await db_session.commit()

    # 3. Search and assert success
    results = await search_embeddings(
        db_session,
        query_embedding=fake_vector,
        limit=5,
        document_id=doc_id
    )

    assert len(results) == 1
    assert results[0]["id"] == chunk_id
    assert results[0]["chunk_type"] == "text"
    assert results[0]["plain_text"] == "Test text content"
    assert "similarity" in results[0]
    assert results[0]["similarity"] > 0.99
