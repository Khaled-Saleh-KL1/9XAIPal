"""Offline-friendly Cloudflare Workers AI image generation and failover."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import httpx

from app.core import circuit_breaker
from app.core.config import Settings, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODELS = [
    "@cf/black-forest-labs/flux-1-schnell",
    "@cf/black-forest-labs/flux-2-klein-4b",
    "@cf/bytedance/stable-diffusion-xl-lightning",
    "@cf/stabilityai/stable-diffusion-xl-base-1.0",
    "@cf/leonardo/phoenix-1.0",
    "@cf/leonardo/lucid-origin",
    "@cf/black-forest-labs/flux-2-dev",
    "@cf/black-forest-labs/flux-2-klein-9b",
]

_BASE_URL = "https://api.cloudflare.com/client/v4/accounts"
_TIMEOUT_SECONDS = 60.0
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_JSON_IMAGE_BYTES = _MAX_RESPONSE_BYTES
_RESPONSE_CHUNK_BYTES = 64 * 1024
_QUOTA_TEXT = re.compile(r"neurons?|quota|daily\s+limit", re.IGNORECASE)


class QuotaExhaustedError(RuntimeError):
    """Every configured Cloudflare account reported its daily quota exhausted."""


def _request_fields(model: str, prompt: str) -> tuple[dict[str, Any] | None, dict[str, tuple[None, str]] | None]:
    """Build the input shape expected by the Cloudflare model family."""
    if "flux-2-" in model:
        return None, {
            "prompt": (None, prompt),
            "width": (None, "1024"),
            "height": (None, "768"),
        }

    payload: dict[str, Any] = {"prompt": prompt}
    if "flux-1-schnell" in model:
        payload["steps"] = 4
    else:
        payload.update(
            negative_prompt="text, letters, watermark, logo",
            width=1024,
            height=768,
        )
        if "stable-diffusion-xl-lightning" in model:
            payload["num_steps"] = 8
        elif "phoenix-1.0" in model:
            payload["num_steps"] = 25
    return payload, None


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _read_bounded_response(response: httpx.Response) -> httpx.Response | None:
    """Buffer at most 8 MiB from a streamed response, then close the stream."""
    chunks: list[bytes] = []
    total_bytes = 0
    for chunk in response.iter_bytes(chunk_size=_RESPONSE_CHUNK_BYTES):
        if total_bytes + len(chunk) > _MAX_RESPONSE_BYTES:
            return None
        chunks.append(chunk)
        total_bytes += len(chunk)

    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=b"".join(chunks),
        request=response.request,
    )


def _error_text(body: dict[str, Any] | None, response: httpx.Response) -> str:
    parts: list[str] = []
    if body:
        errors = body.get("errors") or []
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, dict):
                    parts.extend(str(error.get(key, "")) for key in ("message", "code"))
                elif error:
                    parts.append(str(error))
        if body.get("success") is False:
            parts.append(str(body.get("messages") or ""))
    # Response text participates only in the quota classifier and is never
    # logged; Cloudflare error bodies can contain user supplied prompt text.
    parts.append(response.text[:2000])
    return " ".join(parts)


def _image_bytes(response: httpx.Response) -> bytes | None:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type.startswith("image/"):
        return response.content or None
    if media_type != "application/json" and not media_type.endswith("+json"):
        return None

    body = _json_body(response)
    if not body or body.get("success") is False:
        return None
    result = body.get("result")
    encoded = result.get("image") if isinstance(result, dict) else None
    encoded = encoded or body.get("image")
    if not isinstance(encoded, str) or not encoded:
        return None
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if len(encoded) > _MAX_JSON_IMAGE_BYTES:
        return None
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    return image or None


def generate_image(prompt: str, *, config: Settings | None = None) -> bytes | None:
    """Return generated image bytes, or ``None`` when generation failed.

    Requests are made only when at least one valid account is configured.
    Tests replace :func:`httpx.stream`; this client never has an implicit
    local model or a second provider fallback.
    """
    config = config or settings
    accounts = config.cloudflare_ai_accounts
    if not accounts:
        return None

    models = config.cloudflare_image_models or DEFAULT_MODELS
    breaker_names = [f"cloudflare#{index}" for index in range(len(accounts))]
    active_breakers = set(circuit_breaker.filter_open(breaker_names))
    all_accounts_quota_exhausted = True

    for account_index, (account_id, token) in enumerate(accounts):
        breaker_name = breaker_names[account_index]
        if breaker_name not in active_breakers:
            all_accounts_quota_exhausted = False
            continue

        account_failed = False
        account_quota = False
        for model in models:
            url = f"{_BASE_URL}/{account_id}/ai/run/{model}"
            json_payload, multipart_fields = _request_fields(model, prompt)
            try:
                kwargs: dict[str, Any] = {
                    "headers": {"Authorization": f"Bearer {token}"},
                    "timeout": _TIMEOUT_SECONDS,
                }
                if multipart_fields is not None:
                    kwargs["files"] = multipart_fields
                else:
                    kwargs["json"] = json_payload
                with httpx.stream("POST", url, **kwargs) as streamed_response:
                    response = _read_bounded_response(streamed_response)
            except httpx.RequestError:
                account_failed = True
                continue
            except Exception:
                account_failed = True
                continue

            if response is None:
                account_failed = True
                logger.warning(
                    "Cloudflare image response exceeded %d bytes",
                    _MAX_RESPONSE_BYTES,
                )
                continue

            status = response.status_code
            body = _json_body(response)
            if status in (401, 403):
                logger.warning("Cloudflare image account %d rejected credentials", account_index + 1)
                account_failed = True
                break

            error_text = _error_text(body, response)
            if status == 429 or _QUOTA_TEXT.search(error_text):
                # The free neuron budget is shared by every model under this
                # account. A different model cannot recover from this state.
                account_quota = True
                break

            if status < 200 or status >= 300 or (body and body.get("success") is False):
                account_failed = True
                continue

            image = _image_bytes(response)
            if image:
                circuit_breaker.record_success(breaker_name)
                return image
            account_failed = True

        else:
            # Every model failed for a reason other than a shared account
            # quota. Give the next account a chance and count one breaker hit.
            if account_failed:
                circuit_breaker.record_failure(breaker_name)
                all_accounts_quota_exhausted = False
                logger.warning("Cloudflare image account %d failed across configured models", account_index + 1)
            else:
                all_accounts_quota_exhausted = False
            continue

        # Quota can follow model-specific failures. Once Cloudflare confirms
        # the shared account budget is exhausted, those earlier failures do
        # not change the account-level retry classification.
        if account_quota:
            continue

        # Account failures such as auth errors must not trigger a quota retry
        # for the whole task.
        if account_failed:
            circuit_breaker.record_failure(breaker_name)
            all_accounts_quota_exhausted = False
        # A quota response intentionally does not open the circuit breaker;
        # the worker's daily retry already bounds further attempts.

    if all_accounts_quota_exhausted:
        raise QuotaExhaustedError("all configured Cloudflare accounts exhausted their daily quota")
    logger.warning("Cloudflare image generation failed for all available accounts")
    return None
