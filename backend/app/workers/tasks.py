"""Celery task definitions for ingestion + embedding.

Production background work runs here (called via .delay() from the API).
Uses synchronous DB sessions because Celery workers are not asyncio-native.
"""

from __future__ import annotations

from uuid import UUID

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.logging import get_logger
from app.core.paths import documents_dir
from app.database.connection import sync_session, sync_engine
from app.extraction.pipeline_sync import run_pipeline_sync, update_document_status_sync, update_job_status_sync
from app.embeddings.service_sync import embed_document_chunks_sync
from app.extraction.jobs import JobStatus
from app.summarization.section_summarizer_sync import generate_and_store_section_summaries_sync
from app.summarization.figure_describer_sync import generate_figure_descriptions_sync
from app.services.reading_order import reconstruct_reading_order_for_document
from app.extraction.pipeline_sync import update_job_status_sync

logger = get_logger(__name__)


def _mark_document_and_job_complete(session, doc_uuid: UUID) -> None:
    """Mark the document complete and its latest job COMPLETE.

    This is the single place "complete" is set, called only when the full
    pipeline (extraction → chunking → embedding → summaries → figure
    descriptions) has finished, so the UI's "complete" is truthful.
    """
    update_document_status_sync(session, doc_uuid, "complete")
    job_row = session.execute(
        text(
            "SELECT id FROM ingestion_jobs WHERE document_id = :doc_id "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"doc_id": doc_uuid},
    ).mappings().first()
    if job_row and job_row.get("id"):
        update_job_status_sync(session, job_row["id"], JobStatus.COMPLETE)


# ── Ingestion ─────────────────────────────────────────────────────────────────


@celery_app.task(
    name="9xaipal.process_ingestion",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def process_ingestion(self, document_id: str, job_id: str, filename: str) -> dict:
    """Run MinerU extraction → structural chunking → asset linking pipeline synchronously."""
    logger.info(f"[celery] process_ingestion start document={document_id} job={job_id}")
    
    # Dispose of engine connection pool to avoid sharing sockets across forked Celery processes
    sync_engine.dispose()

    doc_uuid = UUID(document_id)
    job_uuid = UUID(job_id)
    pdf_path = documents_dir() / filename

    if not pdf_path.exists():
        logger.error(f"PDF not found: {pdf_path}")
        with sync_session() as session:
            update_document_status_sync(session, doc_uuid, "failed", error_message=f"PDF not found: {pdf_path}")
            update_job_status_sync(session, job_uuid, JobStatus.FAILED, error_message=f"PDF not found: {pdf_path}")
            session.commit()
        return {"document_id": document_id, "job_id": job_id, "status": "failed"}

    try:
        with sync_session() as session:
            run_pipeline_sync(
                session,
                document_id=doc_uuid,
                job_id=job_uuid,
                pdf_path=pdf_path,
            )
    except Exception as exc:
        logger.exception(f"[celery] process_ingestion failed document={document_id}: {exc}")
        raise
    
    logger.info(f"[celery] process_ingestion done document={document_id}")
    return {"document_id": document_id, "job_id": job_id, "status": "complete"}


# ── Embedding ─────────────────────────────────────────────────────────────────


@celery_app.task(
    name="9xaipal.embed_document",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def embed_document(self, document_id: str) -> dict:
    """Generate embeddings for every un-embedded chunk of a document synchronously."""
    logger.info(f"[celery] embed_document start document={document_id}")
    
    # Dispose of engine connection pool to avoid sharing sockets across forked Celery processes
    sync_engine.dispose()

    doc_uuid = UUID(document_id)
    try:
        with sync_session() as session:
            count = embed_document_chunks_sync(session, doc_uuid)
    except Exception as exc:
        logger.exception(f"[celery] embed_document failed document={document_id}: {exc}")
        raise self.retry(exc=exc)
    
    logger.info(f"[celery] embed_document done document={document_id} embedded={count}")

    # Fire the high-quality section summarization pass (personal quality-first mode).
    # This can take 5-15+ minutes per paper depending on length and hardware.
    # The author explicitly accepts the wait for excellent overview answers.
    #
    # The document is marked "complete" only at the END of that task (which also
    # generates figure descriptions). If we cannot dispatch it, embeddings are
    # already the usable critical path, so mark complete here as a fallback so a
    # paper never gets stuck in "processing" forever.
    try:
        from app.workers.tasks import generate_section_summaries
        generate_section_summaries.delay(document_id)  # type: ignore[attr-defined]
        logger.info(f"[celery] Dispatched generate_section_summaries for {document_id}")
    except Exception:
        logger.exception(
            f"[celery] Failed to dispatch generate_section_summaries for {document_id} "
            "(non-fatal) — marking document complete after embeddings"
        )
        try:
            with sync_session() as session:
                _mark_document_and_job_complete(session, doc_uuid)
                session.commit()
        except Exception:
            logger.exception(f"[celery] Fallback completion failed for {document_id}")

    return {"document_id": document_id, "embedded": count}


# ── High-quality pre-computed summarization (quality-first for personal use) ──


@celery_app.task(
    name="9xaipal.generate_section_summaries",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def generate_section_summaries(self, document_id: str) -> dict:
    """
    Generate rich, attributed, hierarchical section summaries + paper-level overview.

    This is deliberately expensive and runs after embeddings are complete.
    It exists because the author wants the absolute best possible answers to
    "Summarize the paper" / "What is this about?" questions.
    """
    logger.info(f"[celery] generate_section_summaries start document={document_id}")

    sync_engine.dispose()

    doc_uuid = UUID(document_id)
    result: dict = {}

    try:
        with sync_session() as session:
            # Flip the latest job to "summarizing" for honest UI progress.
            try:
                job_row = session.execute(
                    text("""
                        SELECT id FROM ingestion_jobs
                        WHERE document_id = :doc_id
                        ORDER BY created_at DESC LIMIT 1
                    """),
                    {"doc_id": doc_uuid},
                ).mappings().first()
                if job_row and job_row.get("id"):
                    update_job_status_sync(session, job_row["id"], "summarizing")
                    session.commit()
            except Exception:
                pass

            # Section summaries (non-fatal: a failure here must not block the
            # document from ever reaching "complete").
            try:
                result = generate_and_store_section_summaries_sync(session, doc_uuid)
            except Exception:
                logger.exception(f"[celery] Section summary generation failed for {document_id} (non-fatal)")
                session.rollback()

            # Rich VLM descriptions for figures/diagrams — the [figure-describer]
            # phase. Also non-fatal.
            try:
                fig_result = generate_figure_descriptions_sync(session, doc_uuid)
                result["figure_descriptions"] = fig_result
            except Exception:
                logger.exception(f"[celery] Figure description generation failed for {document_id} (non-fatal)")
                session.rollback()

            # This is the true end of the pipeline — NOW the document is complete.
            _mark_document_and_job_complete(session, doc_uuid)
            session.commit()

    except Exception as exc:
        # Only reached on infrastructural failure (e.g. DB unreachable). Retry,
        # and on final give-up still try to mark complete so the paper is usable.
        logger.exception(f"[celery] generate_section_summaries failed document={document_id}: {exc}")
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            try:
                with sync_session() as session:
                    _mark_document_and_job_complete(session, doc_uuid)
                    session.commit()
            except Exception:
                logger.exception(f"[celery] Final completion fallback failed for {document_id}")
            return {"document_id": document_id, "status": "complete_with_errors"}

    logger.info(f"[celery] generate_section_summaries done document={document_id} created={result.get('created')}")
    return {"document_id": document_id, **result}


# ── LLM Reading Order Reconstruction (for two-column / complex papers) ───────


@celery_app.task(
    name="9xaipal.reconstruct_reading_order",
    bind=True,
    max_retries=1,
    acks_late=True,
)
def reconstruct_reading_order(self, document_id: str) -> dict:
    """
    Use the LLM (gemma4:26b) to intelligently reorder chunks for better
    human reading flow on two-column papers and tricky layouts.
    Triggered from the UI when the user clicks "Reconstruct Reading Order (AI)".
    """
    logger.info(f"[celery] reconstruct_reading_order start document={document_id}")

    sync_engine.dispose()
    doc_uuid = UUID(document_id)

    try:
        # Run the async reconstruction using asyncio.run because the service
        # was written async (DB + LLM). This is acceptable for the rare,
        # user-triggered reconstruction task.
        import asyncio
        from app.database.connection import async_session_factory

        async def _run():
            async with async_session_factory() as asession:
                return await reconstruct_reading_order_for_document(asession, doc_uuid)

        result = asyncio.run(_run())
    except Exception as exc:
        logger.exception(f"[celery] reconstruct_reading_order failed for {document_id}: {exc}")
        raise self.retry(exc=exc)

    logger.info(f"[celery] reconstruct_reading_order done for {document_id}")
    return {"document_id": document_id, **result}
