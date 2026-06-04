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

    @app.exception_handler(ModelUnavailable)
    async def model_unavailable_handler(request: Request, exc: ModelUnavailable):
        return JSONResponse(
            status_code=503,
            content={"detail": f"Model unavailable: {exc.model}", "code": "MODEL_UNAVAILABLE"},
        )

    @app.exception_handler(MinerUError)
    async def extraction_error_handler(request: Request, exc: MinerUError):
        return JSONResponse(
            status_code=500,
            content={"detail": f"Extraction failed: {exc}", "code": "EXTRACTION_FAILED"},
        )

