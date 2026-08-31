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
import shutil
import time
from pathlib import Path
from typing import Optional, Union
from uuid import UUID

import httpx
from sqlalchemy import text

from app.core.logging import get_logger
from app.core.net_safety import resolves_to_private_address, safe_send_async
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
        httpx.HTTPError, ValueError, or UnsafeRedirectError (a redirect led
        somewhere the SSRF guard refuses) on unrecoverable problems.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Refusing to fetch non-HTTP URL: {url}")

    # SSRF guard: these URLs come from web search results, i.e. untrusted
    # content. Refuse anything that resolves to loopback / private / link-local
    # space so a crafted result can't make this server probe the LAN or itself.
    if await resolves_to_private_address(url):
        raise ValueError(f"Refusing to fetch image from private/internal address: {url}")

    cache_key = _hash_url(url)
    cache_dir = _proxy_cache_dir()

    # Very simple cache: if we have a file starting with the hash, use it.
    for existing in cache_dir.glob(f"{cache_key}.*"):
        if existing.is_file():
            # Bump mtime on every hit so prune_stale_proxy_cache() ages out by
            # last USE, not last write — a popular image stays cached indefinitely.
            try:
                existing.touch()
            except OSError:
                pass
            content = existing.read_bytes()
            # Best-effort content type from extension
            ctype = mimetypes.guess_type(str(existing))[0] or "application/octet-stream"
            return content, ctype, existing.name

    # safe_send_async walks any redirect chain itself, re-checking the
    # SSRF guard before following each hop, instead of trusting the single
    # check above and then a plain follow_redirects=True — see
    # core/net_safety.py's module docstring for why that combination isn't
    # actually safe against a crafted redirect.
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await safe_send_async(
            client, "GET", url, headers={"User-Agent": "9XAIPal-ImageProxy/1.0"},
        )
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


# ── Storage cleanup ──────────────────────────────────────────────────────────
#
# Neither cache above is touched by document deletion (documents.py's
# delete_paper) — deliberately: conversation_id belongs to a (user, study)
# chat scope, never to a document, so a document's own delete has no correct
# way to know which research-image folders it would even be safe to remove.
# Both are instead swept independently, keyed to their own real lifecycle.

# A cached proxy image with no read in this long is very unlikely to be asked
# for again; re-fetching a genuine repeat is cheap (the whole point of this
# being a cache, not permanent storage).
PROXY_CACHE_MAX_AGE_DAYS = 30


def prune_stale_proxy_cache(max_age_days: int = PROXY_CACHE_MAX_AGE_DAYS) -> int:
    """Delete proxy-cache files not read (see the touch() on cache hit above)
    in over `max_age_days`. Best-effort and synchronous — no DB row anywhere
    tracks this cache, so there is nothing to keep in sync with. Returns the
    count removed."""
    cache_dir = _proxy_cache_dir()
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for f in cache_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as e:
            logger.warning("Could not prune proxy cache file %s: %s", f, e)
    if removed:
        logger.info("Pruned %d stale proxy-cache image(s)", removed)
    return removed


async def sweep_orphaned_research_images(session) -> int:
    """Remove research-image folders whose conversation_id no longer has any
    conversation_turns row.

    A folder here is durable, not a cache — it exists exactly as long as the
    chat history that references it. conversation_turns rows for a
    conversation_id disappear when the study they belong to is deleted
    (CASCADE), when the chat is cleared (studies.py's clear_chat), or when
    every turn in it is deleted one at a time — at that point the images are
    unreachable from any UI and this is what finally reclaims the disk space.
    Best-effort and idempotent: safe to call after either of those actions, or
    on a schedule (currently: once per backend startup, see lifecycle.py).
    """
    research_dir = research_images_dir()
    if not research_dir.exists():
        return 0

    on_disk = {p.name for p in research_dir.iterdir() if p.is_dir()}
    if not on_disk:
        return 0

    result = await session.execute(text("SELECT DISTINCT conversation_id FROM conversation_turns"))
    live = {str(row[0]) for row in result.fetchall()}

    removed = 0
    for name in on_disk - live:
        try:
            shutil.rmtree(research_dir / name)
            removed += 1
        except OSError as e:
            logger.warning("Could not remove orphaned research-image folder %s: %s", name, e)
    if removed:
        logger.info("Swept %d orphaned research-image folder(s)", removed)
    return removed
