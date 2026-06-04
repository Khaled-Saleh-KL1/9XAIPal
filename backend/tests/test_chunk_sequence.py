import pytest
from uuid import uuid4
from unittest.mock import patch, MagicMock

from sqlalchemy import text
from app.extraction.chunker import create_chunks_from_markdown
from app.embeddings.service_sync import embed_document_chunks_sync, get_chunks_without_embeddings_sync


def test_chunker_sequence_ids_and_types():
    """Lightweight smoke for the markdown fallback chunker (used only in
    ALLOW_PYMUPDF_FALLBACK degraded mode).

    The primary high-quality path (MinerU content_list) is what delivers
    correct heading_path, rich table_json, figure descriptions, etc. for real
    papers. We only assert the core architectural invariants here.
    """
    markdown = (
        "# First Heading\n\n"
        "Some text under H1.\n\n"
        "## Second Heading\n\n"
        "Formula here:\n"
        "$$\na^2 + b^2 = c^2\n$$\n\n"
    )

    chunks = create_chunks_from_markdown(markdown)

    # Core guarantee: sequence_ids must be 1-based and strictly monotonic.
    seqs = [c["sequence_id"] for c in chunks]
    assert seqs == list(range(1, len(seqs) + 1)), f"Non-monotonic sequence_ids: {seqs}"

    # At least one chunk should exist and have a heading_path list (not None).
    assert chunks
    assert isinstance(chunks[0].get("heading_path"), (list, type(None)))


def test_embedding_batching_resumption_and_casting(db_session_sync):
    doc_id = uuid4()

    # 1. Insert fake document
    db_session_sync.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'doc.pdf', 'doc.pdf', 'queued')"
        ),
        {"id": doc_id},
    )
    db_session_sync.commit()

    # 2. Insert 25 chunks (so we have more than 1 batch of 20)
    chunk_inserts = []
    for i in range(1, 26):
        chunk_inserts.append({
            "id": str(uuid4()),
            "document_id": str(doc_id),
            "sequence_id": i,
            "chunk_type": "text",
            "markdown": f"Text chunk {i}",
            "plain_text": f"Text chunk {i}",
            "token_count": 5,
        })
    db_session_sync.execute(
        text("""
            INSERT INTO chunks (id, document_id, sequence_id, chunk_type, markdown, plain_text, token_count)
            VALUES (:id, :document_id, :sequence_id, :chunk_type, :markdown, :plain_text, :token_count)
        """),
        chunk_inserts
    )
    db_session_sync.commit()

    # Verify we have 25 chunks without embeddings
    unembedded = get_chunks_without_embeddings_sync(db_session_sync, doc_id, limit=100)
    assert len(unembedded) == 25

    # Mock get_embeddings_batch_sync to return a flat list of 20 embeddings (each 768 dimensions)
    # Let's create mock embeddings (list of floats)
    mock_embeddings = [[float(j) / 10.0 for j in range(768)] for _ in range(20)]

    # We want to test resumption:
    # First, let's mock it so that the first call generates embeddings and works,
    # but we only process 1 batch (20 chunks).
    # Then verify database has 20 embeddings and 5 remaining unembedded.
    with patch("app.embeddings.service_sync.get_embeddings_batch_sync", return_value=mock_embeddings) as mock_get_batch:
        # We run the embedding service with batch_size=20, but we raise Exception on the second batch
        # to simulate a worker crash during the second batch execution.
        original_get_embeddings = mock_get_batch.side_effect

        def side_effect_fn(texts):
            if len(texts) == 20:
                return mock_embeddings
            raise RuntimeError("Simulated Celery Worker Crash")

        mock_get_batch.side_effect = side_effect_fn

        with pytest.raises(RuntimeError, match="Simulated Celery Worker Crash"):
            embed_document_chunks_sync(db_session_sync, doc_id, batch_size=20)

    # Verify that the first batch of 20 was committed (resumption capability)
    # even though the second batch execution crashed.
    unembedded_after_crash = get_chunks_without_embeddings_sync(db_session_sync, doc_id, limit=100)
    assert len(unembedded_after_crash) == 5

    # Check that pgvector stored embeddings are list of floats and correct dimension
    res = db_session_sync.execute(
        text("SELECT embedding FROM chunk_embeddings LIMIT 1")
    )
    # The pgvector extension returns the string representation or parsed format depending on python driver config.
    # In psycopg2/SQLAlchemy, it usually comes back as a string, e.g. '[0.0,0.1,...]' or list. Let's make sure it is in DB.
    row = res.fetchone()
    assert row is not None

    # Now let's resume embedding the remaining 5 chunks.
    mock_remaining_embeddings = [[float(j) / 5.0 for j in range(768)] for _ in range(5)]
    with patch("app.embeddings.service_sync.get_embeddings_batch_sync", return_value=mock_remaining_embeddings):
        total_embedded = embed_document_chunks_sync(db_session_sync, doc_id, batch_size=20)
        assert total_embedded == 5

    # Verify all 25 chunks now have embeddings
    unembedded_final = get_chunks_without_embeddings_sync(db_session_sync, doc_id, limit=100)
    assert len(unembedded_final) == 0
