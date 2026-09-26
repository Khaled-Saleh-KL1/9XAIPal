"""Safe, printed-only Gemini capability check for enabled Arabic OCR."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from google.genai import types

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.extraction.gemini_ocr_client import _default_client_factory, _safe_close

logger = get_logger(__name__)


class ArabicGeminiCapabilityError(RuntimeError):
    """No configured key can access the printed Flash model."""


def probe_printed_flash(
    config: Settings = settings,
    *,
    client_factory: Callable[[str], Any] | None = None,
) -> list[dict[str, int | bool]]:
    """Check every key with a minimal Flash request; never call Gemini Pro.

    The result and logs contain only key indices and success flags. Actual
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
    outcomes: list[dict[str, int | bool]] = []
    for key_index, key in enumerate(keys):
        client = None
        success = False
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
            success = True
        except Exception:
            # Provider messages can echo request content or credentials.
            pass
        finally:
            if client is not None:
                _safe_close(client)
        outcomes.append({"key_index": key_index, "success": success})
        logger.info("Arabic printed Flash probe key_index=%d success=%s", key_index, success)

    if not any(outcome["success"] for outcome in outcomes):
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
