"""Ingestion worker helper (legacy).

This thin async wrapper is mainly used by tests. The production path is
the Celery task in ``app.workers/tasks.py`` which calls the sync pipeline.
"""

from pathlib import Path
from uuid import UUID

from app.core.logging import get_logger
from app.core.paths import documents_dir
from app.database.transactions import transaction
from app.extraction.pipeline import run_pipeline

logger = get_logger(__name__)


async def process_ingestion(document_id: UUID, job_id: UUID, filename: str) -> None:
    """Run the full MinerU → chunking → assets → embedding pipeline.

    Used as an async helper. For Celery dispatch, see ``app.workers.tasks``.
    """
    pdf_path = documents_dir() / filename

    if not pdf_path.exists():
        logger.error(f"PDF not found: {pdf_path}")
        return

    logger.info(f"Starting ingestion for document {document_id}")

    async with transaction() as session:
        await run_pipeline(
            session,
            document_id=document_id,
            job_id=job_id,
            pdf_path=pdf_path,
        )
