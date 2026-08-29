"""Synchronous extraction pipeline: PDF -> MinerU -> structural chunks -> assets -> bulk db injection."""

import uuid
from pathlib import Path
from uuid import UUID
from typing import Callable, Optional

from sqlalchemy import text, Table, MetaData, Column, String, Integer, JSON, insert
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.paths import extracted_dir
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
from app.extraction.glyph_repair import repair_chunks
from app.extraction.jobs import JobStatus
from app.extraction.vlm_client import extract_via_vlm

logger = get_logger(__name__)


def _sanitize_error_for_user(exc: Exception) -> str:
    """Turn ugly internal errors into a short, actionable message for the end user."""
    from app.services.article_extraction import ArticleExtractionError
    if isinstance(exc, ArticleExtractionError):
        # Already written to be shown directly — "couldn't fetch", "requires
        # a login", "too large" — not an internal detail to hide.
        return str(exc)

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
    # progress_fraction only means something within the CURRENT status (e.g.
    # pages extracted / total while status='extracting') — clear it on every
    # transition so a stale fraction from the previous stage never leaks in.
    sets = ["status = :status", "progress_fraction = NULL"]
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


def update_job_progress_sync(session: Session, job_id: UUID, fraction: float) -> None:
    """Report real incremental progress within the current job status.

    Distinct from update_job_status_sync: this never touches status, only the
    fraction, so it's safe to call repeatedly mid-stage (e.g. once per
    extraction page-batch) without disturbing anything else.
    """
    session.execute(
        text("UPDATE ingestion_jobs SET progress_fraction = :f WHERE id = :id"),
        {"id": job_id, "f": max(0.0, min(1.0, fraction))},
    )


def set_embedding_mode_sync(
    session: Session,
    document_id: UUID,
    mode: str,
    reason: str,
) -> None:
    """Record whether this document was embedded or skipped, and why.

    Stored once at ingestion and never recomputed: the mode is a fact about
    what was done, not a policy to be re-evaluated. That is what keeps a later
    change to PAPER_ONLY_MAX_TOKENS from silently reclassifying an existing
    library.
    """
    session.execute(
        text(
            "UPDATE documents SET embedding_mode = :mode, "
            "embedding_skip_reason = :reason, updated_at = NOW() WHERE id = :id"
        ),
        {"mode": mode, "reason": reason, "id": document_id},
    )


def _is_fast_ingest(session: Session, document_id: UUID) -> bool:
    """True when this document is DONE as soon as it is extracted and chunked.

    Two gates, both required:

    1. INGEST_PROFILE is "fast" (the default).
    2. The document is not a book. A book cannot be stuffed into a context
       window, so it still needs the embedding pass to be answerable; a paper
       is served at question time by app.chat.paper_agent instead.

    ⚠ Unlike _should_skip_embeddings this does NOT gate on token count. A
    survey too large to stuff is still readable immediately, and the agent's
    SEARCH/READ path covers it — there is nothing an embedding would buy that
    full-text search over the same chunks does not.
    """
    from app.core.config import settings

    if not settings.fast_ingest:
        return False

    row = session.execute(
        text("SELECT doc_kind FROM documents WHERE id = :id"),
        {"id": document_id},
    ).mappings().first()
    return not (row and row.get("doc_kind") == "book")


def _should_skip_embeddings(session: Session, document_id: UUID) -> tuple[bool, str]:
    """Decide whether the embedding pass can be skipped for this document.

    Gates, in order — every one of them must pass:

    1. The feature is on (PAPER_ONLY_MODE, default False).
    2. The document is not a book. ⚠ doc_kind is a *guard*, never the gate:
       it defaults to 'paper' (schema + upload fallback), so every document
       predating the book/paper chooser is already labelled 'paper'. Gating on
       it alone would sweep in the entire existing library.
    3. The measured token count fits the budget. This is the real test —
       "paper" is not a size, and a 90-page survey is a paper by every UI
       meaning while being far too large to stuff.

    Returns (skip, reason); the reason is stored for auditability either way.
    """
    from app.core.config import settings

    if not settings.paper_only_mode:
        return False, "feature_disabled"

    row = session.execute(
        text("SELECT doc_kind FROM documents WHERE id = :id"),
        {"id": document_id},
    ).mappings().first()
    if row and row.get("doc_kind") == "book":
        return False, "doc_kind=book"

    total = session.execute(
        text("SELECT COALESCE(SUM(token_count), 0) FROM chunks WHERE document_id = :id"),
        {"id": document_id},
    ).scalar_one()
    total = int(total or 0)

    if total == 0:
        # No chunks, or token_count never populated — cannot prove it fits, so
        # take the safe branch and embed.
        return False, "no_token_count"
    if total > settings.paper_only_max_tokens:
        return False, f"too_large({total}>{settings.paper_only_max_tokens})"
    return True, f"fits({total}<={settings.paper_only_max_tokens})"


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


def resolve_extractor(
    pdf_path: Path,
    output_dir: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[Path, str]:
    """Route extraction by EXTRACTOR_PROVIDER; returns ``(output_dir, extractor_name)``.

    ``extractor_name`` is persisted to ``documents.extractor`` by the caller so
    the UI can label the document (e.g. "Processed by MinerU" vs "Processed by
    PyMuPDF (fallback)" vs "Processed by VLM").

    "mineru"/"pymupdf" (the default) keep the existing MinerU-client path — it
    already honors ALLOW_PYMUPDF_FALLBACK and reports which of the two it
    actually used. extract_pdf_sync derives its own output dir from a
    document_id string (``extracted_dir() / document_id``), so we recover that
    id from ``output_dir.name`` — the caller builds ``output_dir`` the same way.
    ``on_progress`` is forwarded to it (see its docstring) — real incremental
    progress during extraction, not available on any other path.

    "vlm" routes to the Qwen3-VL extractor instead, which takes the output dir
    directly; there is no fallback distinction there, so the extractor name is
    always "vlm". It has no progress callback — ``on_progress`` is unused here.
    """
    provider = (settings.extractor_provider or "mineru").lower()
    if provider == "vlm":
        return extract_via_vlm(pdf_path, output_dir), "vlm"
    return extract_pdf_sync(pdf_path, output_dir.name, on_progress=on_progress)


def run_pipeline_sync(
    session: Session,
    *,
    document_id: UUID,
    job_id: UUID,
    pdf_path: Path,
) -> None:
    """Full extraction pipeline executed synchronously within a single transaction wrapper."""
    try:
        # Step 1: Extract with the configured extractor (EXTRACTOR_PROVIDER)
        update_job_status_sync(session, job_id, JobStatus.EXTRACTING)
        session.commit()

        def _report_extraction_progress(pages_done: int, total_pages: Optional[int]) -> None:
            if not total_pages:
                return
            update_job_progress_sync(session, job_id, pages_done / total_pages)
            session.commit()

        output_dir = extracted_dir() / str(document_id)
        output_dir, extractor = resolve_extractor(pdf_path, output_dir, on_progress=_report_extraction_progress)
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

        # Step 2b: Undo MinerU's mangling of inline math variables. It writes
        # U+FFFD wherever the paper used a Mathematical Alphanumeric Symbol
        # (𝑛, 𝑚, 𝑇 …); the PDF still knows, so we read it back.
        # See app/extraction/glyph_repair.py.
        try:
            repair_chunks(chunks, pdf_path)
        except Exception:
            logger.exception("[glyph-repair] failed (non-fatal, chunks kept as-is)")

        # Step 3: Handle physical image assets
        images = find_images(output_dir)
        asset_map = {}
        if images:
            for img_path in images:
                meta = move_asset_to_storage(img_path, document_id=str(document_id))
                asset_map[meta["original_name"]] = meta["file_path"]

        page_count = get_page_count(pdf_path)

        _finish_ingestion(
            session,
            document_id=document_id,
            job_id=job_id,
            chunks=chunks,
            asset_map=asset_map,
            page_count=page_count,
        )

    except Exception as e:
        _handle_ingestion_failure(session, document_id, job_id, e)
        raise e


def _finish_ingestion(
    session: Session,
    *,
    document_id: UUID,
    job_id: UUID,
    chunks: list[dict],
    asset_map: dict,
    page_count: Optional[int],
) -> None:
    """Persist chunks/assets and dispatch whatever comes next (embeddings,
    summaries, or neither) — the tail run_pipeline_sync (PDF) and
    run_article_pipeline_sync (a web article) share.

    ``asset_map`` is ``{basename: where the bytes live}`` — a local
    images_dir()-relative path for the PDF pipeline, a full external URL for
    an imported article (see article_extraction.py and
    repositories/assets.py's resolve_asset_url, the one place that
    distinction is resolved into a servable URL).

    Not wrapped in its own try/except: callers wrap their whole body
    (extraction + this) in one, via _handle_ingestion_failure, so a failure
    anywhere rolls back and marks failed the same way regardless of which
    pipeline it came from.
    """
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

    # Step 5a: Fast profile — extraction IS the pipeline. Mark the document
    # complete right here and dispatch nothing.
    #
    # ⚠ This is the ONE place other than generate_section_summaries that
    # sets status='complete'. That is deliberate and safe precisely because
    # nothing is dispatched afterwards: there is no downstream task left to
    # contradict it. Adding a dispatch to this branch without moving the
    # completion would reintroduce the "UI says done while the worker is
    # still running" bug.
    if _is_fast_ingest(session, document_id):
        set_embedding_mode_sync(
            session, document_id, "skipped", "fast_ingest"
        )
        update_document_status_sync(
            session, document_id, "complete", page_count=page_count
        )
        update_job_status_sync(session, job_id, JobStatus.COMPLETE)
        session.commit()
        logger.info(
            f"[fast-ingest] Document {document_id} complete after extraction: "
            f"{len(chunks)} chunks, {len(asset_payloads)} assets, {page_count} pages "
            "— no embeddings, no summaries, no figure descriptions"
        )
        return

    # Step 5b: Full profile — decide whether this document needs embeddings,
    # then dispatch the right downstream task.
    #
    # ⚠ The dispatcher is conditional; the CHAIN IS NOT. Completion is only
    # ever set by generate_section_summaries (see workers/tasks.py), which
    # is normally reached via embed_document. If the skip path simply
    # dropped embed_document, nothing would ever mark the document
    # complete — it would sit at 'processing' forever with the frontend
    # overlay spinning and no error. So the skip path dispatches
    # generate_section_summaries directly, re-attaching the chain.
    skip, reason = _should_skip_embeddings(session, document_id)
    set_embedding_mode_sync(
        session, document_id, "skipped" if skip else "embedded", reason
    )
    session.commit()

    if skip:
        logger.info(
            f"[paper-only] Skipping embeddings for document {document_id} ({reason}); "
            "dispatching summaries directly"
        )
        update_job_status_sync(session, job_id, JobStatus.SUMMARIZING)
        session.commit()
        from app.workers.tasks import generate_section_summaries
        generate_section_summaries.delay(str(document_id))  # type: ignore[attr-defined]
    else:
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
    update_document_status_sync(session, document_id, "processing", page_count=page_count)
    session.commit()

    logger.info(
        f"Pipeline sync extraction+chunking done for document {document_id}: "
        f"{len(chunks)} chunks, {page_count} pages — embedding/summarizing continues"
    )


def _handle_ingestion_failure(session: Session, document_id: UUID, job_id: UUID, exc: Exception) -> None:
    """Roll back, clean up any partial chunks, and mark the document/job
    failed with a message safe to show the reader. Shared by every pipeline
    (PDF and article) so a failure looks and behaves identically regardless
    of which one it came from.
    """
    session.rollback()
    # Ensure we delete any dirty/partial chunks for this document in a new rollback cleanup block
    try:
        clean_slate_sync(session, document_id)
        session.commit()
    except Exception as clean_err:
        logger.error(f"Failed to cleanup database after failure: {clean_err}")

    # Never leak raw SQLAlchemy / DB parameter dumps to the user.
    safe_error = _sanitize_error_for_user(exc)
    try:
        update_job_status_sync(session, job_id, JobStatus.FAILED, error_message=safe_error)
        update_document_status_sync(session, document_id, "failed", error_message=safe_error)
        session.commit()
    except Exception as status_err:
        logger.error(f"Failed to record failure status: {status_err}")

    logger.exception(f"Pipeline sync error for {document_id}: {exc}")


def run_article_pipeline_sync(
    session: Session,
    *,
    document_id: UUID,
    job_id: UUID,
    url: str,
) -> None:
    """Fetch and extract a web article, then hand off to the same
    chunk/asset/dispatch tail the PDF pipeline uses (_finish_ingestion).

    The fetch+extract step itself lives in services/article_extraction.py —
    SSRF-guarded, hard-timeout network calls, no local storage. Everything
    from there on (chunking, embedding/summary dispatch, failure handling)
    is the exact same code path a PDF goes through, just without a pdf_path,
    a page count, or glyph repair (none of those apply to an article).
    """
    try:
        update_job_status_sync(session, job_id, JobStatus.EXTRACTING)
        session.commit()

        from app.services.article_extraction import extract_article
        article = extract_article(url)

        # Persist the real page title and which extractor produced this —
        # both are only knowable once extraction has actually run, same
        # reason the PDF path persists `extractor` mid-pipeline rather than
        # up front. original_filename, not title: title is the reader's own
        # rename override, and this isn't one.
        try:
            session.execute(
                text(
                    "UPDATE documents SET original_filename = :title, "
                    "extractor = 'trafilatura' WHERE id = :id"
                ),
                {"title": article.title[:500], "id": document_id},
            )
            session.commit()
        except Exception as e:
            logger.warning(f"Could not persist article metadata for {document_id}: {e}")

        update_job_status_sync(session, job_id, JobStatus.CHUNKING)
        session.commit()

        chunks = create_chunks_from_markdown(article.markdown)
        if not chunks:
            raise MinerUError("No structural chunks extracted from article")
        logger.info(
            f"[sync] Article chunked: {len(chunks)} chunks, {len(article.asset_map)} images"
        )

        _finish_ingestion(
            session,
            document_id=document_id,
            job_id=job_id,
            chunks=chunks,
            asset_map=article.asset_map,
            page_count=None,
        )

    except Exception as e:
        _handle_ingestion_failure(session, document_id, job_id, e)
        raise e
