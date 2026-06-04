"""Extraction pipeline: PDF → MinerU → structural chunks → assets → embedding dispatch."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.extraction.mineru_client import (
    extract_pdf,
    find_content_list,
    find_images,
    find_markdown_output,
    get_page_count,
    MinerUError,
)
from app.extraction.chunker import (
    create_chunks_from_content_list,
    create_chunks_from_markdown,
)
from app.extraction.assets import move_asset_to_storage
from app.extraction.jobs import JobStatus
from app.services.ingestion import (
    store_chunks,
    update_job_status,
    mark_document_complete,
    mark_document_failed,
)

logger = get_logger(__name__)


async def run_pipeline(
    session: AsyncSession,
    *,
    document_id: UUID,
    job_id: UUID,
    pdf_path: Path,
) -> None:
    """Full extraction pipeline: MinerU CLI → markdown → chunks → assets → embedding."""
    try:
        # Step 1: Extract with MinerU (subprocess: magic-pdf -p ... -o ... -m auto)
        await update_job_status(session, job_id, JobStatus.EXTRACTING)
        await session.commit()

        output_dir, extractor = await extract_pdf(pdf_path, str(document_id))
        try:
            await session.execute(
                text("UPDATE documents SET extractor = :ex WHERE id = :id"),
                {"ex": extractor, "id": document_id},
            )
            await session.commit()
        except Exception as e:
            logger.warning(f"Could not persist extractor='{extractor}' for {document_id}: {e}")

        # Step 2: Parse markdown into structural chunks
        await update_job_status(session, job_id, JobStatus.CHUNKING)
        await session.commit()

        # Prefer MinerU's content_list.json (typed blocks with page numbers).
        # Fall back to markdown regex when only markdown is available
        # (e.g. degraded PyMuPDF run).
        content_list = find_content_list(output_dir)
        if content_list is not None:
            chunks = create_chunks_from_content_list(content_list)
            logger.info(f"Chunked from content_list.json: {len(chunks)} chunks")
        else:
            md_file = find_markdown_output(output_dir)
            if not md_file:
                raise MinerUError("Extraction produced no markdown output")
            markdown_content = md_file.read_text(encoding="utf-8")
            chunks = create_chunks_from_markdown(markdown_content)
            logger.info(f"Chunked from markdown fallback: {len(chunks)} chunks")

        if not chunks:
            raise MinerUError("No structural chunks extracted from document")

        # Step 3: Persist chunks
        await store_chunks(session, document_id, chunks)
        await session.commit()

        # Step 4: Move extracted images to permanent storage and link to chunks
        images = find_images(output_dir)
        if images:
            # Build a lookup: original_filename → storage_path for linking
            asset_map: dict[str, str] = {}
            for img_path in images:
                meta = move_asset_to_storage(
                    img_path, document_id=str(document_id)
                )
                asset_map[meta["original_name"]] = meta["file_path"]

            # Link assets to chunks via image_refs
            for original_name, storage_path in asset_map.items():
                await session.execute(
                    text("""
                        UPDATE chunks
                        SET image_refs = array_replace(image_refs, :original, :stored)
                        WHERE document_id = :doc_id
                          AND :original = ANY(image_refs)
                    """),
                    {"original": original_name, "stored": storage_path, "doc_id": document_id},
                )
            await session.commit()
            logger.info(f"Linked {len(asset_map)} assets for document {document_id}")

        # Step 5: Dispatch embedding generation to Celery
        await update_job_status(session, job_id, JobStatus.EMBEDDING)
        await session.commit()

        from app.workers.tasks import embed_document
        embed_document.delay(str(document_id))  # type: ignore[attr-defined]

        # Step 6: Record page count, but DO NOT mark complete yet — embedding,
        # section summaries, and figure descriptions still run downstream. The
        # document only becomes "complete" when generate_section_summaries
        # finishes, so the UI never claims "done" while work continues.
        page_count = get_page_count(pdf_path)
        await session.execute(
            text("""
                UPDATE documents
                SET status = 'processing', page_count = :pc, updated_at = NOW()
                WHERE id = :id
            """),
            {"pc": page_count, "id": document_id},
        )
        await session.commit()

        logger.info(
            f"Pipeline extraction+chunking done for document {document_id}: "
            f"{len(chunks)} chunks, {page_count} pages — embedding/summarizing continues"
        )

    except MinerUError as e:
        await session.execute(
            text("DELETE FROM chunks WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        await update_job_status(session, job_id, JobStatus.FAILED, error_message=str(e))
        await mark_document_failed(session, document_id, str(e))
        await session.commit()
        logger.error(f"MinerU extraction failed for {document_id}: {e}")

    except Exception as e:
        await session.execute(
            text("DELETE FROM chunks WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        await update_job_status(session, job_id, JobStatus.FAILED, error_message=str(e))
        await mark_document_failed(session, document_id, str(e))
        await session.commit()
        logger.exception(f"Pipeline error for {document_id}: {e}")

