"""Multimodal request builder."""

import base64
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.core.paths import images_dir

logger = get_logger(__name__)


def build_multimodal_messages(
    prompt: str,
    *,
    system: Optional[str] = None,
    context_text: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
    image_b64s: Optional[list[str]] = None,
) -> list[dict]:
    """Build Ollama chat messages with optional images.

    ``image_paths`` are filesystem paths read from disk and base64-encoded.
    ``image_b64s`` are already-base64-encoded images supplied by the caller
    (e.g. the user drag-dropped a picture into the chat). Both sources are
    merged into the message's ``images`` array.

    image_paths may be:
      - Relative paths as stored in chunk_assets.file_path (e.g. "doc-uuid/fig-123.png")
      - Absolute paths (fallback)

    The function will resolve relative paths against the configured images_dir()
    so that LOCAL context can correctly attach figures to the VLM.
    """
    messages = []

    if system:
        messages.append({"role": "system", "content": system})

    user_content = ""
    if context_text:
        user_content += f"Context:\n{context_text}\n\n"
    user_content += prompt

    user_msg: dict = {"role": "user", "content": user_content}

    images: list[str] = []
    if image_paths:
        for img_path in image_paths:
            p = Path(img_path)
            resolved = False
            if p.exists():
                resolved = True
            else:
                # DB stores relative paths under images/ (e.g. "<doc_id>/<uuid>.png")
                candidate = images_dir() / img_path
                if candidate.exists():
                    p = candidate
                    resolved = True
                elif p.is_absolute() and p.exists():
                    resolved = True
            if resolved:
                try:
                    images.append(base64.b64encode(p.read_bytes()).decode("utf-8"))
                except Exception as e:
                    logger.warning(f"Failed to read image for multimodal {p}: {e}")
            else:
                logger.warning(f"Referenced image not found on disk for LOCAL context: {img_path}")

    if image_b64s:
        for b64 in image_b64s:
            if not b64:
                continue
            # Strip any "data:image/png;base64," prefix the frontend might have included.
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            images.append(b64)

    if images:
        user_msg["images"] = images

    messages.append(user_msg)
    return messages

