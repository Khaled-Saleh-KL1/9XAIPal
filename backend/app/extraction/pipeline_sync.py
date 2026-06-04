"""Synchronous extraction pipeline: PDF -> MinerU -> structural chunks -> assets -> bulk db injection."""

import uuid
from pathlib import Path
from uuid import UUID
from typing import Optional

from sqlalchemy import text, Table, MetaData, Column, String, Integer, JSON, insert
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.extraction.mineru_client import (
    extract_pdf_sync,
    find_markdown_output,
    find_content_list,
    find_images,
    get_page_count,
    MinerUError,
)
from app.extraction.chunker import (
    create_chunks_from_content_list,
    create_chunks_from_markdown,
)
from app.extraction.assets import move_asset_to_storage
from app.extraction.jobs import JobStatus

logger = get_logger(__name__)


def _sanitize_error_for_user(exc: Exception) -> str:
    """Turn ugly internal errors into a short, actionable message for the end user."""
    msg = str(exc).lower()

    if "undefinedcolumn" in msg or "does not exist" in msg:
        return (
            "Database schema is out of date (missing column on chunks or documents table). "
            "Please restart the backend so the migrations can finish applying the latest columns."
        )
    if "too many" in msg or "parameter" in msg or "f405" in msg:
        return (
            "Extraction produced too many fragments for the database to handle in one go. "
            "This commonly happens with the PyMuPDF fallback on dense academic papers. "
            "Install MinerU (magic-pdf) for much better results, or try a different PDF."
        )
    if "magic-pdf" in msg or "mineru" in msg:
        return str(exc)  # already user-friendly from mineru_client
    return "Processing failed during extraction or chunking. Restart the backend and try again."


# Declare metadata and table models for bulk inserts
metadata = MetaData()

chunks_table = Table(
    "chunks",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("document_id", PG_UUID(as_uuid=True)),
    Column("sequence_id", Integer),
    Column("parent_sequence_id", Integer),
    Column("chunk_type", String),
    Column("heading_path", PG_ARRAY(String)),
    Column("markdown", String),
    Column("plain_text", String),
    Column("page_start", Integer),
    Column("page_end", Integer),
    Column("bbox_json", JSON),
    Column("token_count", Integer),
    Column("table_json", JSON),   # Rich structured table data (headers + rows) for table chunks
)

chunk_assets_table = Table(
    "chunk_assets",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    Column("chunk_id", PG_UUID(as_uuid=True)),
    Column("asset_type", String),
    Column("file_path", String),
    Column("mime_type", String),
    Column("width", Integer),
    Column("height", Integer),
    Column("caption", String),
)


def update_job_status_sync(session: Session, job_id: UUID, status: str, error_message: Optional[str] = None) -> None:
    """Update ingestion job status synchronously."""
    sets = ["status = :status"]
    params = {"id": job_id, "status": status}

    if status in ("extracting", "chunking", "embedding") and error_message is None:
        sets.append("started_at = COALESCE(started_at, NOW())")
    if status in ("complete", "failed"):
        sets.append("completed_at = NOW()")
    if error_message:
        sets.append("error_message = :error")
        params["error"] = error_message

    session.execute(
        text(f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


def update_document_status_sync(
    session: Session,
    document_id: UUID,
    status: str,
    error_message: Optional[str] = None,
    page_count: Optional[int] = None,
) -> None:
    """Update document status synchronously."""
    sets = ["status = :status", "updated_at = NOW()"]
    params = {"id": document_id, "status": status}

    if error_message is not None:
        sets.append("error_message = :error_message")
        params["error_message"] = error_message
    if page_count is not None:
        sets.append("page_count = :page_count")
        params["page_count"] = page_count

    session.execute(
        text(f"UPDATE documents SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


def clean_slate_sync(session: Session, document_id: UUID) -> None:
    """Idempotency check and pre-injection cleanup: delete existing chunks, assets, and embeddings."""
    logger.info(f"Cleaning slate for document {document_id}")
    # 1. Delete associated embeddings
    session.execute(
        text("""
            DELETE FROM chunk_embeddings 
            WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
        """),
        {"doc_id": document_id}
    )
    # 2. Delete associated assets
    session.execute(
        text("""
            DELETE FROM chunk_assets 
            WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
        """),
        {"doc_id": document_id}
    )
    # 3. Delete chunks
    session.execute(
        text("DELETE FROM chunks WHERE document_id = :doc_id"),
        {"doc_id": document_id}
    )


def run_pipeline_sync(
    session: Session,
    *,
    document_id: UUID,
    job_id: UUID,
    pdf_path: Path,
) -> None:
    """Full extraction pipeline executed synchronously within a single transaction wrapper."""
    try:
        # Step 1: Extract with MinerU
        update_job_status_sync(session, job_id, JobStatus.EXTRACTING)
        session.commit()

        output_dir, extractor = extract_pdf_sync(pdf_path, str(document_id))
        # Persist which extractor produced the artifacts so the UI can label
        # the document (e.g. "Processed by MinerU" vs "Processed by PyMuPDF (fallback)").
        try:
            session.execute(
                text("UPDATE documents SET extractor = :ex WHERE id = :id"),
                {"ex": extractor, "id": document_id},
            )
            session.commit()
        except Exception as e:
            logger.warning(f"Could not persist extractor='{extractor}' for {document_id}: {e}")

        # Step 2: Parse into structural chunks (prefer content_list.json for page numbers + types)
        update_job_status_sync(session, job_id, JobStatus.CHUNKING)
        session.commit()

        content_list = find_content_list(output_dir)
        if content_list is not None:
            chunks = create_chunks_from_content_list(content_list)
            logger.info(f"[sync] Chunked from content_list.json: {len(chunks)} chunks")
        else:
            md_file = find_markdown_output(output_dir)
            if not md_file:
                raise MinerUError("MinerU extraction produced no markdown output")
            markdown_content = md_file.read_text(encoding="utf-8")
            chunks = create_chunks_from_markdown(markdown_content)
            logger.info(f"[sync] Chunked from markdown fallback: {len(chunks)} chunks")

        if not chunks:
            raise MinerUError("No structural chunks extracted from document")

        # Step 3: Handle physical image assets
        images = find_images(output_dir)
        asset_map = {}
        if images:
            for img_path in images:
                meta = move_asset_to_storage(img_path, document_id=str(document_id))
                asset_map[meta["original_name"]] = meta["file_path"]

        # Step 4: Clean slate and bulk insert chunks + assets inside a single atomic transaction
        clean_slate_sync(session, document_id)

        # Prepare bulk records
        chunk_payloads = []
        asset_payloads = []

        for chunk in chunks:
            chunk_id = uuid.uuid4()
            chunk_payloads.append({
                "id": chunk_id,                     # native UUID for asyncpg + correct PG_UUID column
                "document_id": document_id,         # already UUID
                "sequence_id": chunk["sequence_id"],
                "parent_sequence_id": chunk.get("parent_sequence_id"),
                "chunk_type": chunk["chunk_type"],
                "heading_path": chunk.get("heading_path"),
                "markdown": chunk["markdown"],
                "plain_text": chunk["plain_text"],
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "bbox_json": chunk.get("bbox_json"),
                "token_count": chunk["token_count"],
                "table_json": chunk.get("table_json"),
            })

            # Match and link assets to this chunk
            for img_ref in chunk.get("image_refs", []):
                if img_ref in asset_map:
                    asset_payloads.append({
                        "id": uuid.uuid4(),          # native UUID
                        "chunk_id": chunk_id,        # native UUID
                        "asset_type": "image",
                        "file_path": asset_map[img_ref],
                        "mime_type": "image/png",
                        "width": None,
                        "height": None,
                        "caption": None,
                    })

        # Insert in batches to avoid "too many parameters" errors on papers that
        # produce hundreds/thousands of chunks (especially with PyMuPDF fallback).
        def _batched_insert(rows: list[dict], tbl: Table, bsize: int = 80):
            for i in range(0, len(rows), bsize):
                batch = rows[i : i + bsize]
                if batch:
                    session.execute(insert(tbl).values(batch))

        _batched_insert(chunk_payloads, chunks_table)
        if asset_payloads:
            _batched_insert(asset_payloads, chunk_assets_table)

        # Commit the transaction atomically
        session.commit()
        logger.info(f"Synchronously persisted {len(chunks)} chunks and {len(asset_payloads)} assets for document {document_id}")

        # Step 5: Dispatch embedding via Celery (pass to async or sync)
        update_job_status_sync(session, job_id, JobStatus.EMBEDDING)
        session.commit()

        from app.workers.tasks import embed_document
        embed_document.delay(str(document_id))  # type: ignore[attr-defined]

        # Step 6: Record page count, but DO NOT mark complete yet.
        #
        # Embeddings, section summaries, and figure descriptions all run in
        # downstream Celery tasks (embed_document → generate_section_summaries).
        # The document only becomes "complete" when that final task finishes.
        # Marking it complete here was the bug that made the UI report "done"
        # while the worker was still embedding and describing figures.
        page_count = get_page_count(pdf_path)
        update_document_status_sync(session, document_id, "processing", page_count=page_count)
        session.commit()

        logger.info(
            f"Pipeline sync extraction+chunking done for document {document_id}: "
            f"{len(chunks)} chunks, {page_count} pages — embedding/summarizing continues"
        )

    except Exception as e:
        session.rollback()
        # Ensure we delete any dirty/partial chunks for this document in a new rollback cleanup block
        try:
            clean_slate_sync(session, document_id)
            session.commit()
        except Exception as clean_err:
            logger.error(f"Failed to cleanup database after failure: {clean_err}")

        # Never leak raw SQLAlchemy / DB parameter dumps to the user.
        safe_error = _sanitize_error_for_user(e)
        try:
            update_job_status_sync(session, job_id, JobStatus.FAILED, error_message=safe_error)
            update_document_status_sync(session, document_id, "failed", error_message=safe_error)
            session.commit()
        except Exception as status_err:
            logger.error(f"Failed to record failure status: {status_err}")

        logger.exception(f"Pipeline sync error for {document_id}: {e}")
        raise e
