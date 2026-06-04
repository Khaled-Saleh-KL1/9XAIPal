"""Paper endpoints: upload, list, detail, progress, delete."""

import os
import shutil
import traceback
from uuid import UUID, uuid4

import aiofiles
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings
from app.api.errors import DocumentNotFound
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.paths import documents_dir, assets_dir, extracted_dir, images_dir, ensure_storage_dirs
from app.schemas.documents import DocumentResponse, DocumentListResponse, DocumentUploadResponse
from app.services import documents as doc_service
from app.services.ingestion import create_ingestion_job, update_job_status as update_job_status_svc
from app.database.repositories.documents import update_document_status as update_doc_status_repo
from app.workers.tasks import process_ingestion, embed_document, generate_section_summaries, reconstruct_reading_order

logger = get_logger(__name__)
router = APIRouter()


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
async def upload_paper(
    file: UploadFile = File(...),
    kind: str = Form("paper"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Upload a PDF and dispatch ingestion to Celery worker.

    ``kind`` is ``"book"`` (chapter-by-chapter reading) or ``"paper"`` (linear).
    """
    doc_kind = kind if kind in ("book", "paper") else "paper"
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    ext = ".pdf"
    filename = f"{uuid4().hex}{ext}"
    dest = documents_dir() / filename

    content = await file.read()
    if len(content) > max_bytes:
        # Clean up any partial file we may have written (in case of race or previous write)
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / (1024*1024):.1f} MB). Maximum allowed is {settings.max_upload_size_mb} MB.",
        )

    # Defensive: ensure the storage directories exist right before we write.
    # (lifespan does this, but this protects against CWD differences, docker vs local runs, etc.)
    try:
        ensure_storage_dirs()
    except Exception:
        logger.exception("ensure_storage_dirs failed")

    display_name = file.filename or "unknown.pdf"

    try:
        async with aiofiles.open(dest, "wb") as f:
            await f.write(content)

        original_name = display_name

        doc = await doc_service.create_document(
            db,
            filename=filename,
            original_filename=original_name,
            file_size_bytes=len(content),
            doc_kind=doc_kind,
        )
        await db.commit()

        # Save raw copy to assets/<doc_id>.pdf (for /raw download + /static/assets).
        raw_path = assets_dir() / f"{doc['id']}.pdf"
        async with aiofiles.open(raw_path, "wb") as f:
            await f.write(content)

        job = await create_ingestion_job(db, doc["id"])
        await db.commit()

        # Dispatch to Celery...
        dispatch_ok = True
        try:
            process_ingestion.delay(str(doc["id"]), str(job["id"]), filename)  # type: ignore[attr-defined]
        except Exception as dispatch_exc:
            logger.exception(f"Failed to dispatch process_ingestion for {doc['id']}")
            dispatch_ok = False
            try:
                await update_doc_status_repo(
                    db,
                    doc["id"],
                    "failed",
                    error_message=(
                        "Failed to queue ingestion task (Celery broker / Redis unreachable). "
                        "Start Redis (e.g. via docker compose or redis-server) and the Celery worker "
                        f"(`celery -A app.core.celery_app worker`). Original error: {dispatch_exc}"
                    ),
                )
                await update_job_status_svc(
                    db,
                    job["id"],
                    "failed",
                    error_message=f"Dispatch failed: {dispatch_exc}",
                )
                await db.commit()
            except Exception as mark_exc:
                logger.error(f"Failed to record dispatch failure for doc {doc['id']}: {mark_exc}")

        return DocumentUploadResponse(
            id=doc["id"],
            filename=filename,
            status="processing" if dispatch_ok else "failed",
            message=(
                "Document uploaded and queued for processing"
                if dispatch_ok
                else "Document recorded but background ingestion could not be queued (Redis/Celery). See error details."
            ),
        )

    except Exception as exc:
        # This is the important safety net. Any DB error, write error, etc. in the
        # early part of upload used to produce a completely opaque 500 with no detail.
        # Now we log the *full* traceback server-side and return it in the response
        # body so the frontend (which now extracts .detail) can show the real cause.
        tb = traceback.format_exc()
        logger.error(f"Upload pipeline failed for {display_name}:\n{tb}")
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {exc}\n\nFull traceback (see server logs too):\n{tb}"
        ) from exc


@router.get("/{paper_id}/raw")
async def download_raw_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download the original raw PDF file."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    raw_path = assets_dir() / f"{paper_id}.pdf"
    if not raw_path.exists():
        # Fallback: try from documents dir
        raw_path = documents_dir() / doc["filename"]
    if not raw_path.exists():
        raise DocumentNotFound(str(paper_id))

    return FileResponse(
        path=str(raw_path),
        filename=doc["original_filename"],
        media_type="application/pdf",
    )


@router.get("", response_model=DocumentListResponse)
async def list_papers(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List all papers in the library."""
    docs = await doc_service.list_documents(db, limit=limit, offset=offset)
    total = await doc_service.count_documents(db)
    return DocumentListResponse(
        documents=[DocumentResponse(**d) for d in docs],
        total=total,
    )


@router.get("/{paper_id}", response_model=DocumentResponse)
async def get_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get paper metadata and status."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))
    return DocumentResponse(**doc)


@router.get("/{paper_id}/progress")
async def get_paper_progress(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get current processing status for frontend polling."""
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Also fetch latest job status so the frontend can show accurate
    # "Extracting" / "Chunking" / "Embedding" steps while processing.
    job_status = None
    try:
        job_row = await db.execute(
            text("""
                SELECT status
                FROM ingestion_jobs
                WHERE document_id = :doc_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"doc_id": paper_id},
        )
        job = job_row.mappings().first()
        if job:
            job_status = job["status"]
    except Exception:
        pass

    return {
        "paper_id": str(paper_id),
        "status": doc["status"],
        "job_status": job_status or doc["status"],
        "page_count": doc.get("page_count"),
        "error_message": doc.get("error_message"),
        "extractor": doc.get("extractor"),
    }


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a paper, its DB rows (cascade), and every on-disk artefact.

    Disk cleanup is best-effort: a missing file does NOT prevent the database
    row from being removed.
    """
    deleted = await doc_service.delete_document(db, paper_id)
    if not deleted:
        raise DocumentNotFound(str(paper_id))
    await db.commit()

    # ── Physical cleanup ──────────────────────────────────────────────
    # 1. Raw upload under documents/<filename>
    raw_upload = documents_dir() / deleted["filename"]
    try:
        os.remove(raw_upload)
    except FileNotFoundError:
        pass
    except OSError as e:  # permission errors, etc. — log and continue
        logger.warning(f"could not remove {raw_upload}: {e}")

    # 2. Raw asset copy under assets/<paper_id>.pdf
    raw_asset = assets_dir() / f"{paper_id}.pdf"
    try:
        os.remove(raw_asset)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"could not remove {raw_asset}: {e}")

    # 3. MinerU extraction directory: extracted/<paper_id>/
    extract_path = extracted_dir() / str(paper_id)
    try:
        shutil.rmtree(extract_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"could not rmtree {extract_path}: {e}")

    # 4. Image asset directory: images/<paper_id>/
    image_path = images_dir() / str(paper_id)
    try:
        shutil.rmtree(image_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"could not rmtree {image_path}: {e}")


@router.post("/{paper_id}/rechunk", status_code=200)
async def rechunk_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Re-run the chunker on the existing extracted markdown without re-running MinerU.

    Useful after improving the chunker (equation stitching, footnote detection,
    Unicode math normalization, …) to apply the new logic to papers already on disk.
    Clears existing chunks / embeddings / assets for this doc and rebuilds them
    from the cached MinerU output in storage/extracted/<paper_id>/.
    """
    import uuid as _uuid
    from sqlalchemy import insert
    from app.extraction.mineru_client import find_content_list, find_markdown_output
    from app.extraction.chunker import (
        create_chunks_from_content_list,
        create_chunks_from_markdown,
    )
    from app.extraction.assets import move_asset_to_storage
    from app.extraction.pipeline_sync import (
        chunks_table, chunk_assets_table,
    )

    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    extract_path = extracted_dir() / str(paper_id)
    if not extract_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "No cached extraction on disk for this paper — "
                "delete and re-upload to re-run MinerU."
            ),
        )

    # Prefer content_list.json (typed + page-indexed); fall back to markdown.
    content_list = find_content_list(extract_path)
    if content_list is not None:
        chunks = create_chunks_from_content_list(content_list)
        source = "content_list.json"
    else:
        md_file = find_markdown_output(extract_path)
        if not md_file:
            raise HTTPException(
                status_code=409,
                detail="Cached extraction has no markdown — delete and re-upload.",
            )
        chunks = create_chunks_from_markdown(md_file.read_text(encoding="utf-8"))
        source = "markdown"

    if not chunks:
        raise HTTPException(status_code=500, detail="Re-chunking produced zero chunks.")

    # Wipe and rebuild chunks / embeddings / assets atomically.
    await db.execute(text("""
        DELETE FROM chunk_embeddings
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
    """), {"doc_id": paper_id})
    await db.execute(text("""
        DELETE FROM chunk_assets
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
    """), {"doc_id": paper_id})
    await db.execute(text("DELETE FROM chunks WHERE document_id = :doc_id"),
                     {"doc_id": paper_id})

    # Re-register existing images so chunks can link to them. Images may already
    # have been moved to storage/images/<paper_id>; if so, use them directly.
    images_root = images_dir() / str(paper_id)
    asset_map: dict[str, str] = {}
    if images_root.exists():
        for img in images_root.rglob("*"):
            if img.is_file() and img.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                asset_map[img.name] = str(img)
    else:
        # Fall back to whatever MinerU dropped in the extraction dir.
        from app.extraction.mineru_client import find_images
        for img_path in find_images(extract_path):
            try:
                meta = move_asset_to_storage(img_path, document_id=str(paper_id))
                asset_map[meta["original_name"]] = meta["file_path"]
            except Exception:
                logger.exception("re-register image failed for %s", img_path)

    # Use raw SQL with explicit ::uuid / ::jsonb / ::text[] casts. The shared
    # `chunks_table` is declared with String columns (sized for the sync path);
    # asyncpg refuses to coerce varchar→uuid, so we bind via plain text() instead.
    chunk_sql = text("""
        INSERT INTO chunks (
            id, document_id, sequence_id, parent_sequence_id,
            chunk_type, heading_path, markdown, plain_text,
            page_start, page_end, bbox_json, token_count, table_json
        ) VALUES (
            :id, :document_id, :sequence_id, :parent_sequence_id,
            :chunk_type, CAST(:heading_path AS text[]), :markdown, :plain_text,
            :page_start, :page_end, CAST(:bbox_json AS jsonb),
            :token_count, CAST(:table_json AS jsonb)
        )
    """)
    asset_sql = text("""
        INSERT INTO chunk_assets (
            id, chunk_id, asset_type, file_path, mime_type, width, height, caption
        ) VALUES (
            :id, :chunk_id, :asset_type, :file_path, :mime_type, :width, :height, :caption
        )
    """)

    import json as _json
    for chunk in chunks:
        chunk_id = _uuid.uuid4()
        await db.execute(chunk_sql, {
            "id": chunk_id,
            "document_id": paper_id,
            "sequence_id": chunk["sequence_id"],
            "parent_sequence_id": chunk.get("parent_sequence_id"),
            "chunk_type": chunk["chunk_type"],
            "heading_path": chunk.get("heading_path"),
            "markdown": chunk["markdown"],
            "plain_text": chunk["plain_text"],
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "bbox_json": _json.dumps(chunk["bbox_json"]) if chunk.get("bbox_json") is not None else None,
            "token_count": chunk["token_count"],
            "table_json": _json.dumps(chunk["table_json"]) if chunk.get("table_json") is not None else None,
        })
        for img_ref in chunk.get("image_refs", []):
            if img_ref in asset_map:
                await db.execute(asset_sql, {
                    "id": _uuid.uuid4(),
                    "chunk_id": chunk_id,
                    "asset_type": "image",
                    "file_path": asset_map[img_ref],
                    "mime_type": "image/png",
                    "width": None, "height": None, "caption": None,
                })

    await db.commit()

    # Re-queue embedding generation so chat works again. The rechunk above
    # already replaced the chunks in-process; only embeddings need to be
    # (re)generated, so dispatch embed_document (not the full ingestion task,
    # which requires job_id/filename and would re-run extraction).
    try:
        embed_document.delay(str(paper_id))  # type: ignore[attr-defined]
    except Exception:
        logger.exception("could not dispatch re-embedding task after rechunk")

    counts: dict[str, int] = {}
    for c in chunks:
        counts[c["chunk_type"]] = counts.get(c["chunk_type"], 0) + 1

    return {
        "paper_id": str(paper_id),
        "status": "rechunked",
        "source": source,
        "chunks_total": len(chunks),
        "chunks_by_type": counts,
        "message": (
            "Re-chunked from cached extraction. Embeddings are regenerating in "
            "the background; chat may be slow until they finish."
        ),
    }


@router.post("/{paper_id}/reextract", status_code=202)
async def reextract_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Wipe cached extraction artifacts and re-run the full pipeline (MinerU + chunker).

    Distinct from /rechunk, which only re-runs the chunker on already-extracted
    markdown. /reextract is the one to use after MinerU was installed (or fixed),
    or to migrate papers that were initially processed by the PyMuPDF fallback
    onto MinerU's higher-fidelity output.
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Mark document back to processing + clear any prior error so the UI shows
    # the processing overlay again.
    await db.execute(
        text("""
            UPDATE documents
            SET status = 'processing',
                error_message = NULL,
                extractor = NULL,
                updated_at = NOW()
            WHERE id = :id
        """),
        {"id": paper_id},
    )

    # Wipe DB-side: embeddings → assets → chunks. Cascades from chunks would
    # handle embeddings, but the explicit order is robust to schema drift.
    await db.execute(text("""
        DELETE FROM chunk_embeddings
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
    """), {"doc_id": paper_id})
    await db.execute(text("""
        DELETE FROM chunk_assets
        WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = :doc_id)
    """), {"doc_id": paper_id})
    await db.execute(text("DELETE FROM chunks WHERE document_id = :doc_id"),
                     {"doc_id": paper_id})
    await db.commit()

    # Wipe cached extraction + extracted images so MinerU runs fresh.
    extract_path = extracted_dir() / str(paper_id)
    try:
        if extract_path.exists():
            shutil.rmtree(extract_path)
    except OSError as e:
        logger.warning(f"could not rmtree {extract_path}: {e}")
    image_path = images_dir() / str(paper_id)
    try:
        if image_path.exists():
            shutil.rmtree(image_path)
    except OSError as e:
        logger.warning(f"could not rmtree {image_path}: {e}")

    # Create a fresh ingestion job and dispatch.
    job = await create_ingestion_job(db, paper_id)
    await db.commit()
    try:
        process_ingestion.delay(str(paper_id), str(job["id"]), doc["filename"])  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("Failed to dispatch reextract")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch reextract: {e}")

    return {
        "paper_id": str(paper_id),
        "status": "reextract_queued",
        "job_id": str(job["id"]),
        "message": (
            "Cached extraction wiped; MinerU is re-running from the original PDF. "
            "Poll /progress to watch extracting → chunking → embedding."
        ),
    }


@router.post("/{paper_id}/regenerate-summaries", status_code=202)
async def regenerate_section_summaries(
    paper_id: UUID,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Explicitly trigger (or re-trigger) high-quality section + paper-level summarization
    for a document.

    This is useful after changing the chat model, improving prompts, or if the
    automatic pass failed for some reason.

    Because this is a personal quality-first tool, the author accepts that this
    can take many minutes.
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Fire the Celery task (idempotent inside the summarizer unless force=True)
    try:
        generate_section_summaries.delay(str(paper_id))  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("Failed to dispatch regenerate summaries")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch summarization task: {e}")

    return {
        "paper_id": str(paper_id),
        "status": "summarization_queued",
        "message": "High-quality section summarization task has been dispatched. "
                   "This can take 5-15+ minutes depending on paper length and hardware. "
                   "Poll /progress or check section_summaries table to monitor.",
        "force": force,
    }


@router.post("/{paper_id}/reconstruct-reading-order", status_code=202)
async def trigger_reading_order_reconstruction(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger LLM-based reconstruction of the correct reading order for this paper.

    This is especially useful for two-column academic papers where MinerU's
    default extraction order can be messy (left/right column confusion,
    figures breaking across columns, content continuing on next page in odd ways).

    The task sends chunks + bounding boxes to gemma4:26b and asks it to
    output the proper logical reading sequence. Results are cached on the document.

    After it finishes, the reading view can switch to "AI-corrected order"
    for a much more natural D + ↓ experience.
    """
    doc = await doc_service.get_document(db, paper_id)
    if not doc:
        raise DocumentNotFound(str(paper_id))

    try:
        reconstruct_reading_order.delay(str(paper_id))  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("Failed to dispatch reading order reconstruction")
        raise HTTPException(status_code=500, detail=f"Failed to dispatch task: {e}")

    return {
        "paper_id": str(paper_id),
        "status": "reconstruction_queued",
        "message": "LLM reading order reconstruction has been started. "
                   "This usually takes 30–90 seconds depending on paper length. "
                   "You can poll the document or check the reading view for the result.",
    }
