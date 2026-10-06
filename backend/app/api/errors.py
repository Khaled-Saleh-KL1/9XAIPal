"""Exception handlers mapping domain errors to HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.extraction.mineru_client import MinerUError



UPLOAD_ADMISSION_MESSAGES = {
    "queue_full": "The server is busy processing other uploads right now. Please try again shortly. Your file was not uploaded.",
    "user_queue_full": "Your earlier uploads are still processing. Please wait before adding another document.",
    "storage_full": "The server needs more storage space before accepting uploads. Please try again later.",
    "service_unavailable": "Uploads are temporarily unavailable. Please try again shortly.",
}


class UploadAdmissionError(Exception):
    def __init__(self, code, status_code=429, retry_after=120):
        self.code, self.status_code, self.retry_after = code, status_code, retry_after


class DocumentNotFound(Exception):
    def __init__(self, document_id: str):
        self.document_id = document_id


class ChunkNotFound(Exception):
    def __init__(self, chunk_id: str):
        self.chunk_id = chunk_id


class ModelUnavailable(Exception):
    def __init__(self, model: str, status_code: int | None = None):
        self.model = model
        self.status_code = status_code


class NoLLMConfigured(ModelUnavailable):
    """Neither Ollama nor any cloud API key is usable. The message carries
    full instructions, so handlers surface it verbatim (no prefix)."""


class TooManyQueuedJobs(Exception):
    """The ingestion queue (documents queued or in progress) is at its
    ceiling — see app.core.config.max_queued_ingestion_jobs and
    app.services.ingestion.check_queue_capacity. A single box running Celery
    at --concurrency=1 has no way to absorb an unbounded backlog; this is
    what stops one from accumulating under a real burst instead of an
    upload just quietly waiting forever."""
    def __init__(self, current: int, limit: int):
        self.current = current
        self.limit = limit


class JobAlreadyActive(Exception):
    """A job for this document is still queued or running — see
    app.services.ingestion.create_ingestion_job. With the worker at
    --concurrency > 1 a second job on the same paper would run alongside the
    first: both writing extracted/<id>/, each wiping the other's chunks."""
    def __init__(self, document_id: str, status: str):
        self.document_id = document_id
        self.status = status


class InsufficientStorage(Exception):
    """The disk under storage_root is at or past
    app.core.config.ingestion_disk_refuse_percent — see
    app.services.ingestion.check_disk_headroom. Postgres shares that disk and
    PANICs on ENOSPC (it did on 2026-09-18), so once it is this full the only
    safe answer to a new ingestion is no."""
    def __init__(self, used_percent: int, limit_percent: int):
        self.used_percent = used_percent
        self.limit_percent = limit_percent


class NotAdmitted(Exception):
    """Logged in, but the site is at its concurrent-active-user cap and this
    session hasn't been admitted yet — see app.core.capacity. Raised by
    get_current_user (app/api/deps.py) for every real endpoint; GET /me
    deliberately does NOT raise this (it checks capacity itself and always
    answers 200), since it's what the frontend polls to learn when a slot
    opens up."""
    def __init__(self, queue_position: int):
        self.queue_position = queue_position


def register_exception_handlers(app: FastAPI) -> None:
    """Register all domain exception handlers."""

    @app.exception_handler(UploadAdmissionError)
    async def upload_admission_handler(request: Request, exc: UploadAdmissionError):
        return JSONResponse(status_code=exc.status_code,
                            headers={"Retry-After": str(exc.retry_after)},
                            content={"code": exc.code, "message": UPLOAD_ADMISSION_MESSAGES[exc.code]})

    @app.exception_handler(DocumentNotFound)
    async def document_not_found_handler(request: Request, exc: DocumentNotFound):
        return JSONResponse(
            status_code=404,
            content={"detail": f"Document not found: {exc.document_id}", "code": "DOCUMENT_NOT_FOUND"},
        )

    @app.exception_handler(ChunkNotFound)
    async def chunk_not_found_handler(request: Request, exc: ChunkNotFound):
        return JSONResponse(
            status_code=404,
            content={"detail": f"Chunk not found: {exc.chunk_id}", "code": "CHUNK_NOT_FOUND"},
        )

    @app.exception_handler(NoLLMConfigured)
    async def no_llm_configured_handler(request: Request, exc: NoLLMConfigured):
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc.model), "code": "NO_LLM_CONFIGURED"},
        )

    @app.exception_handler(ModelUnavailable)
    async def model_unavailable_handler(request: Request, exc: ModelUnavailable):
        return JSONResponse(
            status_code=503,
            content={"detail": f"Model unavailable: {exc.model}", "code": "MODEL_UNAVAILABLE"},
        )

    @app.exception_handler(JobAlreadyActive)
    async def job_already_active_handler(request: Request, exc: JobAlreadyActive):
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"This paper is already being processed ({exc.status}) — wait for it to finish.",
                "code": "JOB_ACTIVE",
                "status": exc.status,
            },
        )

    @app.exception_handler(InsufficientStorage)
    async def insufficient_storage_handler(request: Request, exc: InsufficientStorage):
        return await upload_admission_handler(request, UploadAdmissionError("storage_full",503,600))

    @app.exception_handler(TooManyQueuedJobs)
    async def too_many_queued_jobs_handler(request: Request, exc: TooManyQueuedJobs):
        from app.services.upload_admission import retry_after
        return JSONResponse(status_code=429, headers={"Retry-After":str(retry_after(exc.current))},
                            content={"code":"queue_full", "message":UPLOAD_ADMISSION_MESSAGES["queue_full"],
                                     "queued":exc.current,"limit":exc.limit})

    @app.exception_handler(NotAdmitted)
    async def not_admitted_handler(request: Request, exc: NotAdmitted):
        return JSONResponse(
            status_code=423,
            content={
                "detail": "The site is at capacity right now, you're in the queue.",
                "code": "NOT_ADMITTED",
                "queue_position": exc.queue_position,
            },
        )

    @app.exception_handler(MinerUError)
    async def extraction_error_handler(request: Request, exc: MinerUError):
        return JSONResponse(
            status_code=500,
            content={"detail": f"Extraction failed: {exc}", "code": "EXTRACTION_FAILED"},
        )
