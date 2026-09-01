"""Exception handlers mapping domain errors to HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.extraction.mineru_client import MinerUError


class DocumentNotFound(Exception):
    def __init__(self, document_id: str):
        self.document_id = document_id


class ChunkNotFound(Exception):
    def __init__(self, chunk_id: str):
        self.chunk_id = chunk_id


class ModelUnavailable(Exception):
    def __init__(self, model: str):
        self.model = model


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

    @app.exception_handler(TooManyQueuedJobs)
    async def too_many_queued_jobs_handler(request: Request, exc: TooManyQueuedJobs):
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many papers waiting to process right now — try again in a few minutes.",
                "code": "QUEUE_FULL",
                "queued": exc.current,
                "limit": exc.limit,
            },
        )

    @app.exception_handler(NotAdmitted)
    async def not_admitted_handler(request: Request, exc: NotAdmitted):
        return JSONResponse(
            status_code=423,
            content={
                "detail": "The site is at capacity right now — you're in the queue.",
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

