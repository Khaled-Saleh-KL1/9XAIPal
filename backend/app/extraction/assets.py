"""Extracted asset handling: move images to storage."""

import shutil
from pathlib import Path
from uuid import uuid4
from typing import Optional

from app.core.paths import images_dir
from app.core.logging import get_logger

logger = get_logger(__name__)


def move_asset_to_storage(
    source_path: Path,
    *,
    asset_type: str = "image",
    document_id: str = "",
) -> dict:
    """Move an extracted asset to permanent storage.

    Returns a metadata dict including:
      - file_path: path relative to ``images_dir()`` (e.g. ``"<doc_id>/<uuid>.png"``),
        suitable for serving under the ``/static/images/`` static mount.
      - original_name: the source filename, so callers can resolve markdown
        ``![...](filename.png)`` references back to the moved asset.
    """
    dest_dir = images_dir() / document_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = source_path.suffix or ".png"
    dest_name = f"{uuid4().hex}{ext}"
    dest_path = dest_dir / dest_name
    relative_path = f"{document_id}/{dest_name}"

    shutil.copy2(source_path, dest_path)
    logger.info(f"Stored asset: {dest_path}")

    return {
        "asset_type": asset_type,
        "file_path": relative_path,
        "mime_type": _guess_mime(ext),
        "original_name": source_path.name,
    }


def _guess_mime(ext: str) -> Optional[str]:
    """Guess MIME type from extension."""
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }
    return mapping.get(ext.lower())

