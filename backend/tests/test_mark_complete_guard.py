"""_mark_document_and_job_complete must look at the document before marking it.

Production had a 916-page book sitting at status 'complete' with a stored
error message and 0 chunks: the helper set it complete without checking that
extraction had already failed, so the library showed an empty "finished" book.
"""

from uuid import uuid4

from sqlalchemy import text

from app.workers.tasks import _mark_document_and_job_complete


def _insert_document(session, status, error_message=None, chunks=0):
    doc_id = uuid4()
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status, error_message) "
            "VALUES (:id, 'x.pdf', 'x.pdf', :status, :error)"
        ),
        {"id": doc_id, "status": status, "error": error_message},
    )
    job_id = uuid4()
    session.execute(
        text("INSERT INTO ingestion_jobs (id, document_id, status) VALUES (:id, :doc, 'embedding')"),
        {"id": job_id, "doc": doc_id},
    )
    for seq in range(chunks):
        session.execute(
            text(
                "INSERT INTO chunks (id, document_id, sequence_id, chunk_type, markdown, plain_text) "
                "VALUES (:id, :doc, :seq, 'text', 'body', 'body')"
            ),
            {"id": uuid4(), "doc": doc_id, "seq": seq},
        )
    session.commit()
    return doc_id, job_id


def _state(session, doc_id, job_id):
    doc = session.execute(
        text("SELECT status, error_message FROM documents WHERE id = :id"), {"id": doc_id}
    ).mappings().first()
    job = session.execute(
        text("SELECT status FROM ingestion_jobs WHERE id = :id"), {"id": job_id}
    ).mappings().first()
    return doc["status"], doc["error_message"], job["status"]


def test_failed_document_is_not_marked_complete(db_session_sync):
    error = "Processing failed during extraction or chunking. Restart the backend and try again."
    doc_id, job_id = _insert_document(db_session_sync, "failed", error_message=error, chunks=2)

    _mark_document_and_job_complete(db_session_sync, doc_id)
    db_session_sync.commit()

    assert _state(db_session_sync, doc_id, job_id) == ("failed", error, "embedding")


def test_document_with_no_chunks_is_marked_failed(db_session_sync):
    doc_id, job_id = _insert_document(db_session_sync, "processing", chunks=0)

    _mark_document_and_job_complete(db_session_sync, doc_id)
    db_session_sync.commit()

    assert _state(db_session_sync, doc_id, job_id) == (
        "failed",
        "No readable text was extracted from this document.",
        "failed",
    )


def test_document_with_chunks_is_marked_complete(db_session_sync):
    doc_id, job_id = _insert_document(db_session_sync, "processing", chunks=3)

    _mark_document_and_job_complete(db_session_sync, doc_id)
    db_session_sync.commit()

    status, _error, job_status = _state(db_session_sync, doc_id, job_id)
    assert (status, job_status) == ("complete", "complete")
