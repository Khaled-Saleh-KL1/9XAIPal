"""Centralized filesystem path management."""

from pathlib import Path
from typing import Optional, Union
from uuid import UUID

from app.core.config import settings


def _root() -> Path:
    return Path(settings.storage_root)


def documents_dir() -> Path:
    return _root() / "documents"


def extracted_dir() -> Path:
    return _root() / "extracted"


def images_dir() -> Path:
    return _root() / "images"


def assets_dir() -> Path:
    return _root() / "assets"


def logs_dir() -> Path:
    return _root() / "logs"


def covers_dir() -> Path:
    """First-page thumbnails, one JPEG per document, keyed by document id.

    A derived cache, not user data: every file here can be regenerated from the
    PDF in assets/, so losing the directory costs one render per paper and
    nothing else.
    """
    return _root() / "covers"


def raw_snapshots_dir(document_id: Optional[Union[UUID, str]] = None) -> Path:
    """Sanitized raw-HTML snapshots of imported articles (see
    services/article_crawl.py) — the doc_kind='article' equivalent of the
    original PDF documents_dir() already keeps for a paper/book. One
    subfolder per document, one file per crawled page (root page + any
    same-site links followed).
    """
    base = _root() / "raw_snapshots"
    if document_id:
        return base / str(document_id)
    return base


def research_images_dir(conversation_id: Optional[Union[UUID, str]] = None) -> Path:
    """
    Returns the directory for permanently stored research images.

    When conversation_id is provided (UUID or str), returns a per-conversation scoped folder:
        storage/images/research/<conversation_id>/

    This keeps research assets cleanly isolated per research session/thread,
    making them easy to manage, audit, or clean up when a conversation is deleted.
    """
    base = _root() / "images" / "research"
    if conversation_id:
        return base / str(conversation_id)
    return base


def ensure_storage_dirs() -> None:
    """Create all storage directories if they don't exist."""
    for d in [
        documents_dir(),
        extracted_dir(),
        images_dir(),
        assets_dir(),
        logs_dir(),
        covers_dir(),
        raw_snapshots_dir(),
        # research images base (per-conversation folders are created on demand)
        research_images_dir(),
    ]:
        d.mkdir(parents=True, exist_ok=True)

