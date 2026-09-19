"""Ingestion service: transactional document + chunk persistence."""

import shutil
from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import InsufficientStorage, JobAlreadyActive, TooManyQueuedJobs
from app.core.config import settings
from app.database.repositories import documents as doc_repo
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import assets as asset_repo
from app.core.logging import get_logger

logger = get_logger(__name__)


def check_disk_headroom() -> None:
    """Raise InsufficientStorage if the disk under storage_root is at or past
    settings.ingestion_disk_refuse_percent.

    An extraction writes gigabytes (page images, MinerU scratch) onto the
    same disk as Postgres, which PANICs on ENOSPC — that is how the box went
    down on 2026-09-18. Called from check_queue_capacity (so every accept
    path is covered) and again by the worker before it starts, because a
    job accepted at 85% can be reached after the one before it filled the
    disk.
    """
    usage = shutil.disk_usage(settings.storage_root)
    used_percent = round(usage.used * 100 / usage.total)
    if used_percent >= settings.ingestion_disk_refuse_percent:
        raise InsufficientStorage(used_percent, settings.ingestion_disk_refuse_percent)


async def check_queue_capacity(session: AsyncSession) -> None:
    """Raise TooManyQueuedJobs if the ingestion queue is at its ceiling.

    Celery runs this box's extraction pipeline at --concurrency=1 (see
    docker-compose.prod.yml), so an unbounded number of accepted-but-not-yet-
    processed jobs would just grow disk/DB rows without bound under a real
    burst. Call this BEFORE any destructive work (writing a file, deleting a
    paper's existing chunks for a re-ingest) — every caller does real I/O
    right after creating the job row, so checking any later than "first
    thing in the request" would leave a half-done upload or a wiped paper
    behind on rejection.
    """
    check_disk_headroom()
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM ingestion_jobs
            WHERE status NOT IN ('complete', 'failed')
        """)
    )
    count = result.scalar_one()
    if count >= settings.max_queued_ingestion_jobs:
        raise TooManyQueuedJobs(count, settings.max_queued_ingestion_jobs)


async def _reserve_queue_capacity(session: AsyncSession) -> None:
    """Serialize the count-and-insert invariant for one transaction.

    PostgreSQL advisory transaction locks need no schema row and are released
    automatically on commit or rollback. The job insert immediately follows
    while the lock is held, so concurrent requests cannot all pass a stale
    count and then enqueue beyond the configured ceiling.
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('9xaipal:ingestion_queue'))"))
    await check_queue_capacity(session)


async def create_ingestion_job(session: AsyncSession, document_id: UUID) -> dict:
    """Atomically reserve queue capacity and create a new ingestion job.

    Callers must commit this session only after this function returns. The
    advisory lock stays held until then, covering both the count and insert.
    """
    await _reserve_queue_capacity(session)
    # Same lock: two re-extract clicks cannot both see "no active job".
    active = (
        await session.execute(
            text("""
                SELECT status FROM ingestion_jobs
                WHERE document_id = :id AND status NOT IN ('complete', 'failed')
                LIMIT 1
            """),
            {"id": document_id},
        )
    ).scalar_one_or_none()
    if active is not None:
        raise JobAlreadyActive(str(document_id), active)
    result = await session.execute(
        text("""
            INSERT INTO ingestion_jobs (document_id, status)
            VALUES (:document_id, 'queued')
            RETURNING id, document_id, status, created_at
        """),
        {"document_id": document_id},
    )
    return dict(result.mappings().one())


async def update_job_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    error_message: Optional[str] = None,
) -> None:
    """Update ingestion job status."""
    sets = ["status = :status"]
    params: dict = {"id": job_id, "status": status}

    if status in ("extracting", "chunking", "embedding") and error_message is None:
        sets.append("started_at = COALESCE(started_at, NOW())")
    if status in ("complete", "failed"):
        sets.append("completed_at = NOW()")
    if error_message:
        sets.append("error_message = :error")
        params["error"] = error_message

    await session.execute(
        text(f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


async def store_chunks(
    session: AsyncSession,
    document_id: UUID,
    chunks: list[dict],
) -> list[dict]:
    """Store ordered chunks for a document transactionally.

    Strips non-persisted helper fields (e.g. ``image_refs``) before insertion.
    """
    _PERSIST_FIELDS = {
        "document_id", "sequence_id", "parent_sequence_id", "chunk_type",
        "heading_path", "markdown", "plain_text", "page_start", "page_end",
        "bbox_json", "token_count", "table_json",
    }
    payload = []
    for chunk in chunks:
        chunk["document_id"] = document_id
        payload.append({k: v for k, v in chunk.items() if k in _PERSIST_FIELDS})
    return await chunk_repo.create_chunks(session, payload)


async def mark_document_complete(
    session: AsyncSession,
    document_id: UUID,
    page_count: Optional[int] = None,
) -> None:
    """Mark document as complete."""
    await doc_repo.update_document_status(
        session, document_id, "complete", page_count=page_count
    )
    logger.info(f"Document {document_id} marked complete")


async def mark_document_failed(
    session: AsyncSession,
    document_id: UUID,
    error_message: str,
) -> None:
    """Mark document as failed."""
    await doc_repo.update_document_status(
        session, document_id, "failed", error_message=error_message
    )
    logger.error(f"Document {document_id} failed: {error_message}")
