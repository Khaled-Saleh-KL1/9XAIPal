"""Safe, printed-only Gemini capability check for enabled Arabic OCR."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import httpx
from google.genai import errors, types

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.extraction.gemini_ocr_client import _default_client_factory, _safe_close

logger = get_logger(__name__)


class ArabicGeminiCapabilityError(RuntimeError):
    """No configured key can access the printed Flash model."""


def _probe_status(error: Exception) -> str:
    """"unavailable" only for answers that prove this key cannot use the model.

    Rate limits, spent daily quota, provider 5xx, timeouts and network errors
    say nothing about configuration. Treating them as fatal would stop the API
    and worker — English ingestion included — on a restart during an outage.
    """
    if isinstance(error, errors.APIError):
        code = getattr(error, "code", None)
        # 401/403: bad key or no model access; 404: model retired. An invalid
        # key comes back as 400 API_KEY_INVALID, and any other 400 means the
        # minimal probe request itself no longer fits the model.
        if code in (400, 401, 403, 404):
            return "unavailable"
        return "unconfirmed"
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return "unconfirmed"
    return "unavailable"


def probe_printed_flash(
    config: Settings = settings,
    *,
    client_factory: Callable[[str], Any] | None = None,
) -> list[dict[str, int | bool | str]]:
    """Check every key with a minimal Flash request; never call Gemini Pro.

    Startup fails only when every key definitively cannot use the model; a key
    that could not be checked (rate limit, outage, network) does not block.
    The result and logs contain only key indices and statuses. Actual
    credentials and provider error payloads are deliberately discarded.
    """
    if not config.arabic_ocr_enabled:
        return []
    keys = config.gemini_api_keys
    if not keys:
        raise ArabicGeminiCapabilityError(
            "Arabic OCR is enabled but GEMINI_API_KEYS is empty. Configure a "
            "Gemini Flash key or disable ARABIC_OCR_ENABLED."
        )

    factory = client_factory or (
        lambda key: _default_client_factory(
            key, min(config.arabic_gemini_timeout_seconds, 10.0)
        )
    )
    outcomes: list[dict[str, int | bool | str]] = []
    for key_index, key in enumerate(keys):
        client = None
        status = "available"
        try:
            client = factory(key)
            client.models.generate_content(
                model=config.arabic_gemini_printed_model,
                contents="Reply OK.",
                config=types.GenerateContentConfig(
                    max_output_tokens=16,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=config.arabic_gemini_printed_thinking_level
                    ),
                ),
            )
        except Exception as error:
            # Provider messages can echo request content or credentials:
            # keep only the classification, never the error text.
            status = _probe_status(error)
        finally:
            if client is not None:
                _safe_close(client)
        outcomes.append({
            "key_index": key_index,
            "success": status == "available",
            "status": status,
        })
        log = logger.warning if status == "unconfirmed" else logger.info
        log("Arabic printed Flash probe key_index=%d status=%s", key_index, status)

    if all(outcome["status"] == "unavailable" for outcome in outcomes):
        raise ArabicGeminiCapabilityError(
            "Arabic OCR is enabled, but no configured Gemini key can access "
            f"the printed Flash model {config.arabic_gemini_printed_model}. "
            "Check GEMINI_API_KEYS, model access, and quota; then restart, or "
            "disable ARABIC_OCR_ENABLED. Gemini Pro was not probed."
        )
    return outcomes


if __name__ == "__main__":
    try:
        probe_printed_flash()
    except ArabicGeminiCapabilityError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from None
