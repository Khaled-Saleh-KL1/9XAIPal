"""Vision-language model client for image understanding."""

import base64
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def describe_image(
    image_path: str,
    *,
    prompt: str = "Describe this image in detail.",
    model: Optional[str] = None,
) -> str:
    """Use the VLM to describe an image."""
    model = model or settings.vlm_model
    url = f"{settings.ollama_base_url}/api/generate"

    # Read and encode image
    image_data = Path(image_path).read_bytes()
    b64_image = base64.b64encode(image_data).decode("utf-8")

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [b64_image],
        "stream": False,
    }

    timeout = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectError as e:
        from app.api.errors import ModelUnavailable
        logger.error("Ollama VLM connect error: %s", e)
        raise ModelUnavailable(f"{model} (Ollama unreachable: {e})")

    return data.get("response", "")

