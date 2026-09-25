"""Printed-Arabic-only OCR fallback through Ollama Cloud's native API."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.extraction.arabic_adapter import parse_complete_page_prefix
from app.extraction.arabic_types import (
    GemmaKeysExhausted,
    GemmaOutputInvalid,
    GemmaRequestInvalid,
    OcrBatchResult,
    OcrUsage,
    RenderedPage,
)

logger = get_logger(__name__)

_PROVIDER = "gemma4_arabic_fallback"
_MAX_OUTPUT_ATTEMPTS_PER_KEY = 2
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


class HandwrittenFallbackForbidden(RuntimeError):
    """Gemma is not an approved OCR route for handwritten Arabic."""


HttpClientFactory = Callable[[str | None], Any]


def _default_http_client_factory(_api_key: str | None) -> httpx.Client:
    return httpx.Client()


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_from_payload(payload: dict[str, Any]) -> OcrUsage:
    prompt_tokens = _count(payload.get("prompt_eval_count"))
    output_tokens = _count(payload.get("eval_count"))
    return OcrUsage(
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=prompt_tokens + output_tokens,
    )


def _sum_usage(usages: Sequence[OcrUsage]) -> OcrUsage:
    return OcrUsage(
        prompt_tokens=sum(usage.prompt_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        thought_tokens=sum(usage.thought_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
    )


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class GemmaArabicFallback:
    """Run one printed page through Gemma, rotating only Ollama credentials."""

    def __init__(
        self,
        *,
        settings: Settings = settings,
        http_client_factory: HttpClientFactory = _default_http_client_factory,
    ) -> None:
        self.settings = settings
        self.http_client_factory = http_client_factory

    def generate_page(
        self,
        page: RenderedPage,
        *,
        writing_style: str = "printed",
    ) -> OcrBatchResult:
        if writing_style != "printed":
            raise HandwrittenFallbackForbidden(
                "The Gemma Arabic fallback accepts only confidently printed Arabic."
            )
        if page.page_number < 1 or not page.png:
            raise GemmaRequestInvalid(
                "A positive absolute page number and rendered PNG are required."
            )

        configured_keys = (
            self.settings.arabic_gemma_api_keys or self.settings.ollama_api_keys
        )
        key_slots: list[str | None] = configured_keys or [None]
        url = f"{self.settings.arabic_gemma_base_url.rstrip('/')}/api/chat"
        image_base64 = base64.b64encode(page.png).decode("ascii")
        usages: list[OcrUsage] = []
        last_output_error: GemmaOutputInvalid | None = None
        final_kind = "provider_unavailable"
        batch_started = time.monotonic()

        for key_index, api_key in enumerate(key_slots):
            for attempt_index in range(_MAX_OUTPUT_ATTEMPTS_PER_KEY):
                started = time.monotonic()
                client = None
                try:
                    client = self.http_client_factory(api_key)
                    response = client.post(
                        url,
                        json=self._payload(page, image_base64),
                        headers=self._headers(api_key),
                        timeout=_TIMEOUT,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    _close(client)
                    status = error.response.status_code
                    final_kind = self._status_failure_kind(status)
                    self._log_failure(
                        key_index if api_key is not None else None,
                        page.page_number,
                        final_kind,
                        self._elapsed_ms(started),
                    )
                    if final_kind == "invalid_request":
                        raise GemmaRequestInvalid(
                            f"Ollama rejected the Gemma OCR request (HTTP {status})."
                        ) from None
                    break
                except (httpx.TransportError, TimeoutError, ConnectionError):
                    _close(client)
                    final_kind = "network_error"
                    self._log_failure(
                        key_index if api_key is not None else None,
                        page.page_number,
                        final_kind,
                        self._elapsed_ms(started),
                    )
                    break
                except Exception:
                    _close(client)
                    self._log_failure(
                        key_index if api_key is not None else None,
                        page.page_number,
                        "client_error",
                        self._elapsed_ms(started),
                    )
                    raise GemmaRequestInvalid(
                        "The configured Ollama OCR request could not be issued."
                    ) from None
                else:
                    _close(client)

                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    payload = None
                if not isinstance(payload, dict):
                    final_kind = "invalid_output"
                    last_output_error = GemmaOutputInvalid(
                        "Ollama returned a non-JSON OCR response."
                    )
                    usage = OcrUsage()
                else:
                    usage = _usage_from_payload(payload)
                    usages.append(usage)
                    text = self._response_text(payload)
                    parsed = parse_complete_page_prefix(
                        text,
                        expected_pages=[page.page_number],
                        provider=_PROVIDER,
                        model=self.settings.arabic_gemma_fallback_model,
                    )
                    if parsed.is_complete and parsed.pages:
                        duration = self._elapsed_ms(batch_started)
                        totals = _sum_usage(usages)
                        logger.info(
                            "Ollama Arabic OCR succeeded key_index=%s model=%s "
                            "pages=%d latency_ms=%d prompt_tokens=%d output_tokens=%d "
                            "thought_tokens=%d total_tokens=%d",
                            key_index if api_key is not None else "none",
                            self.settings.arabic_gemma_fallback_model,
                            page.page_number,
                            duration,
                            totals.prompt_tokens,
                            totals.output_tokens,
                            totals.thought_tokens,
                            totals.total_tokens,
                        )
                        return OcrBatchResult(
                            text=text,
                            provider=_PROVIDER,
                            model=self.settings.arabic_gemma_fallback_model,
                            key_index=key_index if api_key is not None else None,
                            usage=totals,
                            latency_ms=duration,
                        )
                    final_kind = "invalid_output"
                    last_output_error = GemmaOutputInvalid(
                        "Ollama returned an incomplete, empty, or repeated page."
                    )

                self._log_failure(
                    key_index if api_key is not None else None,
                    page.page_number,
                    final_kind,
                    self._elapsed_ms(started),
                    usage=usage,
                )
                if attempt_index == 0:
                    continue
                break

        exhausted = GemmaKeysExhausted(final_kind=final_kind, attempt_usage=usages)
        if final_kind == "invalid_output" and last_output_error is not None:
            raise exhausted from last_output_error
        raise exhausted

    def _payload(self, page: RenderedPage, image_base64: str) -> dict[str, Any]:
        prompt = (
            "Transcribe this confidently printed Arabic document page verbatim. "
            "Preserve paragraphs, headings, tables, and logical RTL reading order; "
            "keep any English spans and numerals in their original order. "
            "Do not translate, summarize, infer, or repair the text. "
            "Mark illegible text as [غير واضح]. Return only this page wrapped "
            f"exactly as <!-- PAGE:{page.page_number} -->, followed by its Markdown, "
            f"then <!-- END_PAGE:{page.page_number} -->."
        )
        return {
            "model": self.settings.arabic_gemma_fallback_model,
            "messages": [
                {"role": "user", "content": prompt, "images": [image_base64]}
            ],
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": self.settings.arabic_ocr_max_output_tokens,
            },
        }

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _response_text(payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    @staticmethod
    def _status_failure_kind(status: int) -> str:
        if status in (401, 403):
            return "authentication"
        if status == 429:
            return "rate_limited"
        if status == 408:
            return "timeout"
        if status >= 500:
            return "provider_error"
        return "invalid_request"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def _log_failure(
        self,
        key_index: int | None,
        page_number: int,
        failure_kind: str,
        latency_ms: int,
        *,
        usage: OcrUsage = OcrUsage(),
    ) -> None:
        logger.warning(
            "Ollama Arabic OCR failed key_index=%s model=%s pages=%d "
            "failure_kind=%s latency_ms=%d prompt_tokens=%d output_tokens=%d "
            "thought_tokens=%d total_tokens=%d",
            key_index if key_index is not None else "none",
            self.settings.arabic_gemma_fallback_model,
            page_number,
            failure_kind,
            latency_ms,
            usage.prompt_tokens,
            usage.output_tokens,
            usage.thought_tokens,
            usage.total_tokens,
        )
