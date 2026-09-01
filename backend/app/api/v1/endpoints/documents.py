"""Paper endpoints: upload, list, detail, progress, delete."""

import os
import shutil
import traceback
from uuid import UUID, uuid4

import aiofiles
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings, get_current_user
from app.api.errors import DocumentNotFound
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.paths import documents_dir, assets_dir, extracted_dir, images_dir, raw_snapshots_dir, ensure_storage_dirs
from app.schemas.documents import (
    DocumentResponse,
    DocumentListResponse,
    DocumentUploadResponse,
    ImportArticleRequest,
    RenameDocumentRequest,
)
from app.services import covers as cover_service
from app.services import documents as doc_service
from app.services.ingestion import check_queue_capacity, create_ingestion_job, update_job_status as update_job_status_svc
from app.database.repositories.documents import update_document_status as update_doc_status_repo
from app.workers.tasks import (
    process_ingestion,
    process_article_ingestion,
    embed_document,
    generate_section_summaries,
    reconstruct_reading_order,
)

logger = get_logger(__name__)
router = APIRouter()


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
async def upload_paper(
    file: UploadFile = File(...),
    kind: str = Form("paper"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    current_user: dict = Depends(get_current_user),
):
    """Upload a PDF and dispatch ingestion to Celery worker.

    ``kind`` is ``"book"`` (chapter-by-chapter reading) or ``"paper"`` (linear).
    """
    # First thing, before reading the upload into memory or touching disk:
    # reject up front if the ingestion queue is already full, rather than
    # doing all that work and creating a documents row only to fail later.
    await check_queue_capacity(db)

    doc_kind = kind if kind in ("book", "paper") else "paper"
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    ext = ".pdf"
    filename = f"{uuid4().hex}{ext}"
    dest = documents_dir() / filename

    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / (1024*1024):.1f} MB). Maximum allowed is {settings.max_upload_size_mb} MB.",
        )

    # Reject anything that is not actually a PDF before it touches disk or the
    # extraction pipeline. The PDF spec requires the %PDF- header within the
    # first 1024 bytes; checking content (not the filename) blocks disguised
    # uploads (e.g. an executable renamed to .pdf).
    if b"%PDF-" not in content[:1024]:
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are accepted (the uploaded file has no PDF header).",
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
            user_id=current_user["id"],
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

    except HTTPException:
        raise
    except Exception as exc:
        # Safety net for DB/write errors early in upload. The full traceback is
        # logged server-side only — returning it to the client would leak
        # filesystem paths, connection details, and internal code structure.
        logger.error(f"Upload pipeline failed for {display_name}:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {type(exc).__name__}. Check the server logs for the full traceback.",
        ) from exc


@router.post("/import-url", response_model=DocumentUploadResponse, status_code=201)
async def import_article(
    payload: ImportArticleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Import a web article by URL and read it exactly like a paper.

    Created here as doc_kind='article' regardless of ``payload.kind`` — what
    the link actually is isn't known until the ingestion task fetches it.
    ``kind`` ("book"/"paper", set when the link was pasted through that
    picker rather than the generic "Article by URL" one) travels along as a
    hint: if the fetch finds a PDF, run_article_pipeline_sync adopts it with
    that doc_kind instead of 'article'; if it isn't a PDF, the hint is
    dropped and the row stays a normal article — there's no PDF-based
    pipeline to honor it with. The real page title and extractor label are
    likewise unknown until the fetch actually runs, so this creates the row
    with the URL itself as a placeholder original_filename; the ingestion
    task overwrites it once it has the real one (the same "known only
    mid-pipeline" pattern /upload uses for `extractor`).

    No file is written to disk here at all — unlike /upload, everything
    happens inside the dispatched Celery task (see
    extraction/pipeline_sync.py's run_article_pipeline_sync and
    services/article_extraction.py for the actual fetch, SSRF guard, and
    hard timeouts).
    """
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="A URL is required.")

    # Before creating anything: reject up front if the ingestion queue is
    # already full (see upload_paper's identical check for why "up front").
    await check_queue_capacity(db)

    try:
        doc = await doc_service.create_document(
            db,
            user_id=current_user["id"],
            filename=f"{uuid4().hex}.html",
            original_filename=url,
            doc_kind="article",
            source_url=url,
        )
        await db.commit()

        job = await create_ingestion_job(db, doc["id"])
        await db.commit()

        dispatch_ok = True
        try:
            process_article_ingestion.delay(str(doc["id"]), str(job["id"]), url, payload.kind)  # type: ignore[attr-defined]
        except Exception as dispatch_exc:
            logger.exception(f"Failed to dispatch process_article_ingestion for {doc['id']}")
            dispatch_ok = False
            try:
                await update_doc_status_repo(
                    db,
                    doc["id"],
                    "failed",
                    error_message=(
                        "Failed to queue ingestion task (Celery broker / Redis unreachable). "
                        f"Original error: {dispatch_exc}"
                    ),
                )
                await update_job_status_svc(
                    db, job["id"], "failed", error_message=f"Dispatch failed: {dispatch_exc}",
                )
                await db.commit()
            except Exception as mark_exc:
                logger.error(f"Failed to record dispatch failure for doc {doc['id']}: {mark_exc}")

        return DocumentUploadResponse(
            id=doc["id"],
            filename=doc["filename"],
            status="processing" if dispatch_ok else "failed",
            message=(
                "Article queued for import"
                if dispatch_ok
                else "Document recorded but background ingestion could not be queued (Redis/Celery). See error details."
            ),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Article import failed for {url}:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Import failed: {type(exc).__name__}. Check the server logs for the full traceback.",
        ) from exc


def _raw_html_headers() -> dict:
    """Response headers for any raw-snapshot HTML this app serves.

    Content-Security-Policy is the load-bearing one: the HTML underneath was
    fetched from a third party (see services/article_crawl.py) and, despite
    having its <script> tags/event-handler attributes stripped at save time,
    this is defense in depth for that same guarantee at serve time — a
    belt-and-suspenders pair, not a single point of failure. script-src
    'none' means even a sanitizer bug can't turn into script execution
    against this authenticated origin. X-Content-Type-Options is also set
    globally (SecurityHeadersMiddleware, via setdefault), so this is
    additive there, not a conflict.
    """
    return {
        "Content-Security-Policy": "script-src 'none'; object-src 'none'",
        "X-Content-Type-Options": "nosniff",
    }


def _raw_response_kind(doc_kind: str | None, pages: list) -> str:
    """Pure decision behind GET /{paper_id}/raw, pulled out of the endpoint
    so it's unit-testable without a DB or the FastAPI/ASGI stack — same
    reasoning as _to_storage_path (notes.py) and _pdf_name_from_url
    (pipeline_sync.py). Returns one of:
      'pdf'         — anything that isn't doc_kind='article' (unchanged today).
      'unavailable' — an article with no saved snapshot (the save failed, or
                      never ran — see extraction/pipeline_sync.py, which
                      saves this inline as part of the import itself, so
                      there's no meaningful "still saving" state to report:
                      by the time the article is readable at all, its raw
                      snapshot has already either saved or failed).
      'single'      — the one snapshot page for this article: serve it.
    """
    if doc_kind != "article":
        return "pdf"
    return "single" if pages else "unavailable"


@router.get("/{paper_id}/raw")
async def download_raw_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """The raw copy of this document: the original PDF for a paper/book, or
    a sanitized raw HTML snapshot of the imported page for an article (see
    services/article_crawl.py).
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    pages: list = []
    if doc.get("doc_kind") == "article":
        rows = await db.execute(
            text(
                "SELECT id, url, title, depth, storage_filename FROM raw_snapshot_pages "
                "WHERE document_id = :id ORDER BY created_at"
            ),
            {"id": paper_id},
        )
        pages = [dict(r) for r in rows.mappings().all()]

    kind = _raw_response_kind(doc.get("doc_kind"), pages)

    if kind == "unavailable":
        # Opened directly in a browser tab, not fetched by frontend JS —
        # a small readable message, not a bare JSON 404.
        return HTMLResponse(
            "<!DOCTYPE html><html><body style='font-family:-apple-system,sans-serif;"
            "max-width:32rem;margin:4rem auto;padding:0 1.5rem;color:#444'>"
            "No raw copy is available for this article.</body></html>",
            headers=_raw_html_headers(),
        )

    if kind == "single":
        page = pages[0]
        html_path = raw_snapshots_dir(paper_id) / page["storage_filename"]
        if not html_path.exists():
            raise DocumentNotFound(str(paper_id))
        return HTMLResponse(html_path.read_text(encoding="utf-8"), headers=_raw_html_headers())

    raw_path = assets_dir() / f"{paper_id}.pdf"
    if not raw_path.exists():
        # Fallback: try from documents dir
        raw_path = documents_dir() / doc["filename"]
    if not raw_path.exists():
        raise DocumentNotFound(str(paper_id))

    # ⚠ Only the SUGGESTED save name changes here. `filename`/`original_filename`
    # on the row are untouched (see rename_paper's docstring) — this is just the
    # Content-Disposition header, so a rename can win here too without touching
    # the on-disk key or losing the as-uploaded name from the database.
    title = (doc.get("title") or "").strip()
    download_name = f"{title}.pdf" if title else doc["original_filename"]

    return FileResponse(
        path=str(raw_path),
        filename=download_name,
        media_type="application/pdf",
    )


@router.get("/{paper_id}/raw/{page_id}")
async def get_raw_snapshot_page(
    paper_id: UUID,
    page_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """One specific crawled page from a multi-page raw snapshot. Ownership-
    checked the same way every other resource in this app is (see
    docs/02-architecture/auth.md's per-user isolation section): the parent
    document is loaded scoped to current_user first, and the page must
    belong to it — a page id that exists but belongs to someone else's
    document 404s exactly like a nonexistent one, never 403.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    row = await db.execute(
        text(
            "SELECT storage_filename FROM raw_snapshot_pages "
            "WHERE id = :page_id AND document_id = :document_id"
        ),
        {"page_id": page_id, "document_id": paper_id},
    )
    page = row.mappings().first()
    if not page:
        raise DocumentNotFound(str(paper_id))

    html_path = raw_snapshots_dir(paper_id) / page["storage_filename"]
    if not html_path.exists():
        raise DocumentNotFound(str(paper_id))

    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers=_raw_html_headers())


@router.get("", response_model=DocumentListResponse)
async def list_papers(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all papers this user owns."""
    docs = await doc_service.list_documents(db, current_user["id"], limit=limit, offset=offset)
    total = await doc_service.count_documents(db, current_user["id"])
    return DocumentListResponse(
        documents=[DocumentResponse(**d) for d in docs],
        total=total,
    )


@router.get("/{paper_id}", response_model=DocumentResponse)
async def get_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get paper metadata and status."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))
    return DocumentResponse(**doc)


@router.get("/{paper_id}/progress")
async def get_paper_progress(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get current processing status for frontend polling."""
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Also fetch latest job status so the frontend can show accurate
    # "Extracting" / "Chunking" / "Embedding" steps while processing.
    job_status = None
    progress_fraction = None
    queue_position = None
    try:
        job_row = await db.execute(
            text("""
                SELECT status, progress_fraction, created_at
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
            progress_fraction = job["progress_fraction"]
            # Celery runs this box's pipeline at --concurrency=1, so while
            # still 'queued' this job's actual position is just how many
            # other still-queued jobs got there first — same table the
            # capacity ceiling in ingestion.py::check_queue_capacity counts.
            if job_status == "queued":
                pos_row = await db.execute(
                    text("""
                        SELECT COUNT(*) FROM ingestion_jobs
                        WHERE status = 'queued' AND created_at < :created_at
                    """),
                    {"created_at": job["created_at"]},
                )
                queue_position = pos_row.scalar_one() + 1
    except Exception:
        pass

    raw_page_count = None
    if doc.get("doc_kind") == "article":
        count_row = await db.execute(
            text("SELECT COUNT(*) FROM raw_snapshot_pages WHERE document_id = :id"),
            {"id": paper_id},
        )
        raw_page_count = count_row.scalar_one()

    return {
        "paper_id": str(paper_id),
        "status": doc["status"],
        "job_status": job_status or doc["status"],
        # Real progress *within* job_status (e.g. pages extracted / total
        # while extracting) — None when there's nothing finer than the status.
        "progress_fraction": progress_fraction,
        # 1-based position among still-queued jobs, only while job_status is
        # 'queued' — None once it starts extracting (nothing left to wait on).
        "queue_position": queue_position,
        "page_count": doc.get("page_count"),
        "error_message": doc.get("error_message"),
        "extractor": doc.get("extractor"),
        # Raw-HTML snapshot crawl (see services/article_crawl.py) — 'none'
        # for anything that isn't doc_kind='article'. Independent of the
        # fields above: a 'failed' or still-'pending' snapshot never means
        # the article itself failed to import.
        "raw_snapshot_status": doc.get("raw_snapshot_status"),
        "raw_page_count": raw_page_count,
    }


@router.patch("/{paper_id}", response_model=DocumentResponse)
async def rename_paper(
    paper_id: UUID,
    payload: RenameDocumentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Rename a paper.

    Sets a display title that overrides the uploaded filename everywhere the
    library and reader show a name. A blank title clears the override.

    ⚠ Renames the ROW, never the file. ``filename`` is the on-disk key that
    documents/, extracted/, images/ and every chunk asset path are built from;
    touching it to satisfy a rename would break all of them. ``/raw``'s
    download name follows the rename instead (see download_raw_paper) —
    that only changes the response header, not this row's stored
    ``original_filename``, which stays the as-uploaded name.
    """
    doc = await doc_service.rename_document(db, paper_id, current_user["id"], payload.title)
    if not doc:
        raise DocumentNotFound(str(paper_id))
    await db.commit()
    return DocumentResponse(**doc)


@router.get("/{paper_id}/cover")
async def get_paper_cover(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """The paper's first page as a JPEG thumbnail, rendered on first request.

    Returns 204 rather than 404 when no cover can be produced (no source PDF,
    an unrenderable first page). The library asks for a cover for every card it
    draws, and a wall of 404s in the console makes a working library look
    broken; "there is nothing here" is the honest answer and the <img> falls
    back to its placeholder either way.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # ⚠ Off the event loop. Rasterising a page is 50-200ms of CPU inside a
    # native extension, and the library asks for every card's cover at once —
    # inline, a twelve-paper grid would stall every other request for a second.
    path = await run_in_threadpool(
        cover_service.render_cover, paper_id, doc.get("filename")
    )
    if not path:
        return Response(status_code=204)

    return FileResponse(
        path=str(path),
        media_type="image/jpeg",
        # A cover is immutable for the life of the document — the first page of
        # a PDF cannot change — so let the browser keep it for a day and stop
        # re-fetching one image per card on every library poll.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a paper, its DB rows (cascade), and every on-disk artefact.

    Disk cleanup is best-effort: a missing file does NOT prevent the database
    row from being removed.
    """
    deleted = await doc_service.delete_document(db, paper_id, current_user["id"])
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

    # 5. Cached first-page cover: covers/<paper_id>.jpg
    cover_service.delete_cover(paper_id)


@router.post("/{paper_id}/rechunk", status_code=200)
async def rechunk_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
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

    doc = await doc_service.get_document(db, paper_id, current_user["id"])
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

    # Repair the inline math variables MinerU turned into U+FFFD. Runs here too
    # (not only in the pipeline) so a paper already on disk can be fixed with a
    # re-chunk instead of a full re-extraction.
    from app.extraction.glyph_repair import repair_chunks
    glyphs_repaired = 0
    source_pdf = documents_dir() / (doc.get("filename") or "")
    if source_pdf.exists():
        try:
            glyphs_repaired = repair_chunks(chunks, source_pdf)
        except Exception:
            logger.exception("[glyph-repair] failed during rechunk (non-fatal)")
    else:
        logger.warning("[glyph-repair] source PDF missing for %s — skipping", paper_id)

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

    # Re-register images from the EXTRACTION directory, always.
    #
    # ⚠ Do not "optimise" this by reading storage/images/<paper_id> instead.
    # Chunks reference images by their original MinerU filename (the hash in
    # `![](<hash>.jpg)`), but move_asset_to_storage renames every file to a
    # fresh uuid on the way into storage. Keying the map off the stored
    # filenames therefore produces names that no chunk can ever match, and
    # every figure silently loses its image — with the files sitting right
    # there on disk. Only the extraction dir still knows the original names.
    #
    # Re-copying leaves the previous copies orphaned. That is deliberate: they
    # may still be referenced by figure_descriptions rows, and a few stale
    # images cost less than a broken figure.
    from app.extraction.mineru_client import find_images
    asset_map: dict[str, str] = {}
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
    #
    # Under the fast profile a paper has no embeddings to regenerate — the new
    # chunks are immediately answerable via app.chat.paper_agent, which reads
    # the chunks table directly. Dispatching here would burn worker time on an
    # index nothing queries.
    from app.core.config import settings as app_settings
    reembedding = not (app_settings.fast_ingest and doc.get("doc_kind") != "book")
    if reembedding:
        try:
            embed_document.delay(str(paper_id))  # type: ignore[attr-defined]
        except Exception:
            logger.exception("could not dispatch re-embedding task after rechunk")
            reembedding = False

    counts: dict[str, int] = {}
    for c in chunks:
        counts[c["chunk_type"]] = counts.get(c["chunk_type"], 0) + 1

    return {
        "paper_id": str(paper_id),
        "status": "rechunked",
        "source": source,
        "chunks_total": len(chunks),
        "chunks_by_type": counts,
        "glyphs_repaired": glyphs_repaired,
        "message": (
            "Re-chunked from cached extraction. Embeddings are regenerating in "
            "the background; chat may be slow until they finish."
            if reembedding
            else "Re-chunked from cached extraction. Reopen the paper to read it."
        ),
    }


@router.post("/{paper_id}/reextract", status_code=202)
async def reextract_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Wipe cached extraction artifacts and re-run the full pipeline (MinerU + chunker).

    Distinct from /rechunk, which only re-runs the chunker on already-extracted
    markdown. /reextract is the one to use after MinerU was installed (or fixed),
    or to migrate papers that were initially processed by the PyMuPDF fallback
    onto MinerU's higher-fidelity output.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
    if not doc:
        raise DocumentNotFound(str(paper_id))

    # Before wiping anything: reject up front if the ingestion queue is
    # already full. This one matters most of the three call sites — the code
    # below deletes the paper's existing chunks/embeddings before recreating
    # the job, so checking any later would leave a paper with nothing to
    # read and no job queued to fix it.
    await check_queue_capacity(db)

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
    current_user: dict = Depends(get_current_user),
):
    """
    Explicitly trigger (or re-trigger) high-quality section + paper-level summarization
    for a document.

    This is useful after changing the chat model, improving prompts, or if the
    automatic pass failed for some reason.

    Because this is a personal quality-first tool, the author accepts that this
    can take many minutes.
    """
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
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
    current_user: dict = Depends(get_current_user),
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
    doc = await doc_service.get_document(db, paper_id, current_user["id"])
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
