"""Ollama chat and generation API wrapper."""

import json
from typing import AsyncIterator, Optional

import httpx

from app.api.errors import ModelUnavailable
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Cache of requested-tag → resolved installed tag. Ollama's installed model set
# doesn't change during a run, so resolving once avoids a `GET /api/tags` round
# trip before *every* chat call (there are 2–3 chat calls per /ask).
_TAG_CACHE: dict[str, str] = {}


async def _resolve_model_tag(client: httpx.AsyncClient, requested: str) -> str:
    """Return the actual installed tag matching `requested` case-insensitively.

    We standardize on lowercase tags (Ollama convention). The lookup tolerates
    case differences (e.g. gemma4:26B vs gemma4:26b) for user convenience. The
    result is cached so repeated calls don't re-hit `/api/tags`.
    """
    cached = _TAG_CACHE.get(requested)
    if cached:
        return cached
    try:
        resp = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        installed = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        raise ModelUnavailable(f"{requested} (Ollama unreachable: {e})")

    resolved = None
    if requested in installed:
        resolved = requested
    else:
        lower = requested.lower()
        for name in installed:
            if name.lower() == lower:
                resolved = name
                break
    if resolved is None:
        raise ModelUnavailable(
            f"{requested} not installed. Available: {', '.join(installed) or '(none)'}"
        )
    _TAG_CACHE[requested] = resolved
    return resolved


async def chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    stream: bool = False,
    num_predict: Optional[int] = None,
    keep_alive: Optional[str] = None,
) -> dict:
    """Send a chat completion request to Ollama.

    ``num_predict`` caps generated tokens (None = model default / config cap).
    ``keep_alive`` controls how long Ollama keeps the model resident afterward
    (defaults to settings.ollama_keep_alive so the big model stays warm between
    the multiple calls each /ask makes).
    """
    requested_model = model or settings.chat_model
    url = f"{settings.ollama_base_url}/api/chat"

    options: dict = {"temperature": temperature}
    # Apply an explicit cap, or the global answer cap, when provided (>0).
    effective_cap = num_predict if num_predict is not None else (settings.chat_num_predict or None)
    if effective_cap:
        options["num_predict"] = effective_cap

    # Long read timeout — local inference (especially the first request after a
    # model loads into VRAM, or large-context prompts) regularly exceeds 2 min.
    # Connect/write stay short so genuine network errors still fail fast.
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resolved_model = await _resolve_model_tag(client, requested_model)
        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive if keep_alive is not None else settings.ollama_keep_alive,
            "options": options,
        }
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            logger.error(f"Ollama chat HTTP {e.response.status_code}: {body}")
            raise ModelUnavailable(f"{resolved_model} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved_model} (network error: {e})")
        data = response.json()

    return {
        "content": data.get("message", {}).get("content", ""),
        "model": resolved_model,
        "prompt_tokens": data.get("prompt_eval_count"),
        "completion_tokens": data.get("eval_count"),
    }


async def stream_chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    num_predict: Optional[int] = None,
    keep_alive: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Stream a chat completion from Ollama token by token.

    Yields ``{"type": "token", "text": ...}`` events as they arrive, then a
    final ``{"type": "done", "content": <full answer>, "model": ...,
    "prompt_tokens": ..., "completion_tokens": ...}`` event.
    """
    requested_model = model or settings.chat_model
    url = f"{settings.ollama_base_url}/api/chat"

    options: dict = {"temperature": temperature}
    effective_cap = num_predict if num_predict is not None else (settings.chat_num_predict or None)
    if effective_cap:
        options["num_predict"] = effective_cap

    timeout = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
    content_parts: list[str] = []
    prompt_tokens = None
    completion_tokens = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        resolved_model = await _resolve_model_tag(client, requested_model)
        payload = {
            "model": resolved_model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive if keep_alive is not None else settings.ollama_keep_alive,
            "options": options,
        }
        try:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    logger.error(f"Ollama stream HTTP {response.status_code}: {body}")
                    raise ModelUnavailable(f"{resolved_model} ({response.status_code}: {body})")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    token = (data.get("message") or {}).get("content") or ""
                    if token:
                        content_parts.append(token)
                        yield {"type": "token", "text": token}
                    if data.get("done"):
                        prompt_tokens = data.get("prompt_eval_count")
                        completion_tokens = data.get("eval_count")
                        break
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved_model} (network error: {e})")

    yield {
        "type": "done",
        "content": "".join(content_parts),
        "model": resolved_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


async def generate(
    prompt: str,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
) -> dict:
    """Send a generation request to Ollama."""
    model = model or settings.chat_model
    url = f"{settings.ollama_base_url}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    timeout = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            logger.error(f"Ollama generate HTTP {e.response.status_code}: {body}")
            raise ModelUnavailable(f"{model} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{model} (network error: {e})")
        data = response.json()

    return {
        "content": data.get("response", ""),
        "model": model,
    }


async def is_available() -> bool:
    """Check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous variants (used by Celery workers for long-running summarization)
# These mirror the async versions but use blocking httpx.Client.
# Timeout is intentionally long because section summarization can be slow.
# ─────────────────────────────────────────────────────────────────────────────

import hashlib


def _resolve_model_tag_sync(requested: str) -> str:
    """Synchronous version of model tag resolution."""
    import httpx

    from app.core.config import settings

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
            installed = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        raise ModelUnavailable(f"{requested} (Ollama unreachable: {e})")

    if requested in installed:
        return requested
    lower = requested.lower()
    for name in installed:
        if name.lower() == lower:
            return name
    raise ModelUnavailable(
        f"{requested} not installed. Available: {', '.join(installed) or '(none)'}"
    )


def chat_sync(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.3,   # Slightly lower default for factual summarization
    images: Optional[list[str]] = None,  # base64 encoded images for vision models
) -> dict:
    """Synchronous chat completion for Celery workers (supports vision via images)."""
    import httpx

    from app.core.config import settings

    requested_model = model or settings.chat_model
    url = f"{settings.ollama_base_url}/api/chat"

    resolved_model = _resolve_model_tag_sync(requested_model)

    # Attach images at the message level for Ollama vision (same as async path)
    final_messages = list(messages)
    if images and final_messages:
        # Ollama expects images on the last user message for most vision models
        last_msg = final_messages[-1]
        if last_msg.get("role") == "user":
            last_msg = {**last_msg, "images": images}
            final_messages[-1] = last_msg

    payload = {
        "model": resolved_model,
        "messages": final_messages,
        "stream": False,
        "options": {"temperature": temperature},
    }

    with httpx.Client(timeout=300.0) as client:  # 5 minutes per section is generous
        try:
            response = client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            logger.error(f"[sync] Ollama chat HTTP {e.response.status_code}: {body}")
            raise ModelUnavailable(f"{resolved_model} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{resolved_model} (network error: {e})")
        data = response.json()

    return {
        "content": data.get("message", {}).get("content", ""),
        "model": resolved_model,
        "prompt_tokens": data.get("prompt_eval_count"),
        "completion_tokens": data.get("eval_count"),
    }


def hash_prompt(prompt_text: str) -> str:
    """Stable short hash for a prompt template (used for invalidation)."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]

