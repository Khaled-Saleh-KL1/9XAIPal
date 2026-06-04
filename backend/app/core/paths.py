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
        # research images base (per-conversation folders are created on demand)
        research_images_dir(),
    ]:
        d.mkdir(parents=True, exist_ok=True)

