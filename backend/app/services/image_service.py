"""
Image service: reusable server-side fetching, proxying, and local storage for web images.

This module powers two important capabilities:

1. Option A (Backend Image Proxy):
   - A general-purpose proxy for any web image (SearXNG results, etc.).
   - Fetches on the server so the browser never makes direct requests to potentially
     hostile or hotlink-protected hosts.

2. Option B (ResearchAgent local persistence):
   - When the ResearchAgent discovers useful images during iterative research,
     we download them once server-side and store them permanently as local assets.
   - These become durable, offline-available parts of the research conversation,
     exactly like paper figures and extracted MinerU images.

Design principles:
- Best-effort: failures never break the user's answer.
- Strict but practical limits (size, timeout, content-type).
- Content-addressed storage for natural deduplication.
- Clear separation between transient proxy cache and permanent research assets.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Union
from uuid import UUID

import httpx

from app.core.logging import get_logger
from app.core.paths import research_images_dir

logger = get_logger(__name__)

# --- Limits (tunable via config later if needed) ---
MAX_IMAGE_BYTES = 12 * 1024 * 1024      # 12 MB
REQUEST_TIMEOUT = 20.0                  # seconds
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

# Simple on-disk cache for the general proxy (not research images).
# Keyed by URL hash so repeated requests for the same remote image are cheap.
PROXY_CACHE_DIR_NAME = "proxy_cache"


def _proxy_cache_dir() -> Path:
    """Returns storage/images/proxy_cache (created on demand)."""
    # We place it under the normal images dir for consistency
    from app.core.paths import images_dir
    cache_dir = images_dir() / PROXY_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _guess_extension(content_type: str) -> str:
    ext = mimetypes.guess_extension(content_type) or ".jpg"
    if ext == ".jpe":
        ext = ".jpg"
    return ext


async def fetch_image_via_proxy(url: str) -> tuple[bytes, str, str]:
    """
    Fetch an image through the server.

    Returns:
        (content_bytes, content_type, suggested_filename)

    Raises:
        httpx.HTTPError or ValueError on unrecoverable problems.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Refusing to fetch non-HTTP URL: {url}")

    cache_key = _hash_url(url)
    cache_dir = _proxy_cache_dir()

    # Very simple cache: if we have a file starting with the hash, use it.
    for existing in cache_dir.glob(f"{cache_key}.*"):
        if existing.is_file():
            content = existing.read_bytes()
            # Best-effort content type from extension
            ctype = mimetypes.guess_type(str(existing))[0] or "application/octet-stream"
            return content, ctype, existing.name

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "9XAIPal-ImageProxy/1.0"})
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Disallowed content type for image proxy: {content_type}")

        content = resp.content
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError(f"Image too large ({len(content)} bytes > {MAX_IMAGE_BYTES})")

        ext = _guess_extension(content_type)
        filename = f"{cache_key}{ext}"

        # Write to cache (best effort)
        try:
            (cache_dir / filename).write_bytes(content)
        except Exception as e:
            logger.warning("Failed to write proxy cache for %s: %s", url, e)

        return content, content_type, filename


async def download_and_store_research_image(
    url: str,
    conversation_id: Union[UUID, str],
) -> Optional[str]:
    """
    Download a research image and persist it permanently under the conversation's
    research image folder.

    conversation_id may be UUID or str (we normalize via str() inside paths).
    Returns the relative filename (e.g. "a3f9c2e1.jpg") or None on best-effort failure.

    This is the core primitive for Option B (per-conversation durable local assets).
    """
    try:
        content, content_type, suggested_name = await fetch_image_via_proxy(url)
    except Exception as e:
        logger.warning("Research image download failed for %s: %s", url, e)
        return None

    ext = _guess_extension(content_type)
    # Use content hash for the final filename (stable + dedup)
    content_hash = hashlib.sha256(content).hexdigest()[:16]
    filename = f"{content_hash}{ext}"

    target_dir = research_images_dir(conversation_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    try:
        target_path.write_bytes(content)
        logger.info(
            "Persisted research image for conversation %s: %s (from %s)",
            conversation_id, filename, url,
        )
        return filename
    except Exception as e:
        logger.error("Failed to write research image %s: %s", filename, e)
        return None


def build_research_image_url(conversation_id: Union[UUID, str], filename: str) -> str:
    """Returns the stable static URL the frontend can use for a locally-persisted research image."""
    return f"/static/images/research/{conversation_id}/{filename}"
