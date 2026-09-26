"""The shared job-status helpers behave exactly as on main for every job
that carries no Arabic error code.

Every ingestion (English, books, articles) goes through these helpers, so the
Arabic branch may only add the error_code write for its own typed failures.
An update without a code must not even name the new column: the worker can
run before the API's startup migration has added it.
"""

import asyncio

import pytest

from app.extraction import pipeline_sync
from app.services import ingestion


class _CapturingSession:
    def __init__(self):
        self.statements: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))


class _AsyncCapturingSession(_CapturingSession):
    async def execute(self, statement, params=None):  # type: ignore[override]
        super().execute(statement, params)


def _sync_update(status, **kwargs):
    session = _CapturingSession()
    pipeline_sync.update_job_status_sync(session, "job-id", status, **kwargs)
    (sql, params), = session.statements
    return sql, params


def _async_update(status, **kwargs):
    session = _AsyncCapturingSession()
    asyncio.run(ingestion.update_job_status(session, "job-id", status, **kwargs))
    (sql, params), = session.statements
    return sql, params


UPDATES = [_sync_update, _async_update]


@pytest.mark.parametrize("update", UPDATES)
@pytest.mark.parametrize(
    "status", ["extracting", "chunking", "embedding", "summarizing", "complete"]
)
def test_progress_updates_never_touch_error_columns(update, status):
    sql, _ = update(status)

    assert "error_code" not in sql
    assert "error_message" not in sql


@pytest.mark.parametrize("update", UPDATES)
def test_failure_without_a_code_does_not_name_the_error_code_column(update):
    sql, params = update("failed", error_message="MinerU extraction produced no markdown output")

    assert "error_code" not in sql
    assert "error_message = :error" in sql
    assert params["error"] == "MinerU extraction produced no markdown output"


@pytest.mark.parametrize("update", UPDATES)
def test_failure_with_an_empty_message_leaves_the_message_as_main_did(update):
    sql, _ = update("failed", error_message="")

    assert "error_message" not in sql


@pytest.mark.parametrize("update", UPDATES)
def test_started_at_rules_match_main(update):
    assert "started_at" in update("extracting")[0]
    assert "started_at" not in update("summarizing")[0]
    assert "started_at" not in update("extracting", error_message="x")[0]


@pytest.mark.parametrize("update", UPDATES)
def test_typed_arabic_failure_records_its_code(update):
    sql, params = update(
        "failed",
        error_message="Handwritten Arabic extraction is not currently available",
        error_code="handwritten_arabic_unavailable",
    )

    assert "error_code = :error_code" in sql
    assert params["error_code"] == "handwritten_arabic_unavailable"


def test_pipeline_failure_records_a_code_only_for_arabic_routing_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pipeline_sync,
        "update_job_status_sync",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(pipeline_sync, "update_document_status_sync", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_sync, "clean_slate_sync", lambda *a, **k: None)

    class ThirdPartyError(Exception):
        error_code = "SOME_LIBRARY_CODE"

    class _Session:
        def rollback(self):
            pass

        def commit(self):
            pass

    pipeline_sync._handle_ingestion_failure(_Session(), "doc", "job", ThirdPartyError("boom"))
    pipeline_sync._handle_ingestion_failure(
        _Session(), "doc", "job", pipeline_sync.HandwrittenArabicUnavailable()
    )

    assert calls[0].get("error_code") is None
    assert calls[1]["error_code"] == "handwritten_arabic_unavailable"


# --- API fields for documents without an Arabic error code -----------------

from uuid import uuid4  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.api.arabic_status import document_error_fields  # noqa: E402
from app.database.repositories.documents import get_document  # noqa: E402


@pytest.mark.parametrize("job_code", [None, "some_future_non_arabic_code"])
def test_error_message_for_non_arabic_documents_is_the_document_message(job_code):
    # main returned documents.error_message and nothing else; a job message
    # must not leak in when the document has none.
    fields = document_error_fields({
        "job_error_code": job_code,
        "job_error_message": "Dispatch failed: redis.exceptions.ConnectionError",
        "error_message": None,
    })

    assert fields["error_message"] is None
    assert fields["action_required"] is None
    assert fields["allowed_actions"] == []


@pytest.mark.asyncio
async def test_get_document_returns_the_same_job_fields_as_main(db_session):
    user_id, document_id = uuid4(), uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:id, 'compat@example.test', 'h')"),
        {"id": user_id},
    )
    await db_session.execute(
        text("INSERT INTO documents (id, user_id, filename, original_filename, status) "
             "VALUES (:id, :user_id, 'p.pdf', 'p.pdf', 'processing')"),
        {"id": document_id, "user_id": user_id},
    )
    await db_session.execute(
        text("INSERT INTO ingestion_jobs (document_id, status, progress_fraction) "
             "VALUES (:id, 'extracting', 0.5)"),
        {"id": document_id},
    )

    doc = await get_document(db_session, document_id, user_id)

    # main's get_document was SELECT * FROM documents: no job status here, so
    # the detail/rename/done responses keep job_status null as before.
    assert "job_status" not in doc
    assert "job_progress_fraction" not in doc
