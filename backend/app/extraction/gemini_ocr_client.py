"""Arabic printed-document OCR through a bounded Gemini API key cascade."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.extraction.arabic_types import (
    GeminiKeysExhausted,
    GeminiOutputInvalid,
    GeminiRequestInvalid,
    OcrBatchResult,
    OcrUsage,
    RenderedPage,
)

logger = get_logger(__name__)

Validator = Callable[[str, Sequence[int]], int]
ClientFactory = Callable[[str], Any]

_MAX_ATTEMPTS_PER_KEY = 2
_RETRY_DELAY_MIN_SEC = 0.1
_RETRY_DELAY_MAX_SEC = 0.5


def batch_prompt(_pages: Sequence[RenderedPage]) -> str:
    """Ask for faithful page-bounded Markdown; page images carry the evidence."""

    return (
        "Transcribe every visible page verbatim in its original language. "
        "Preserve reading order, paragraphs, headings, and tables in Markdown. "
        "For Arabic or mixed Arabic/English pages, preserve logical RTL reading order; "
        "keep English spans and numerals in their original order. "
        "Do not translate, summarize, infer, or repair text. "
        "Mark illegible text as [غير واضح]. "
        "Wrap each page exactly with <!-- PAGE:n --> and <!-- END_PAGE:n --> "
        "markers, replacing n with that page's number shown before its image."
    )


def _default_client_factory(api_key: str) -> genai.Client:
    """Create an isolated SDK client and disable its implicit retry multiplier."""

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )


def _token_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_from_response(response: Any) -> OcrUsage:
    metadata = getattr(response, "usage_metadata", None)
    return OcrUsage(
        prompt_tokens=_token_count(getattr(metadata, "prompt_token_count", 0)),
        output_tokens=_token_count(
            getattr(metadata, "candidates_token_count", 0)
        ),
        thought_tokens=_token_count(
            getattr(metadata, "thoughts_token_count", 0)
        ),
        total_tokens=_token_count(getattr(metadata, "total_token_count", 0)),
    )


def _sum_usage(usages: Sequence[OcrUsage]) -> OcrUsage:
    return OcrUsage(
        prompt_tokens=sum(item.prompt_tokens for item in usages),
        output_tokens=sum(item.output_tokens for item in usages),
        thought_tokens=sum(item.thought_tokens for item in usages),
        total_tokens=sum(item.total_tokens for item in usages),
    )


def _safe_close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # a close failure must not mask the provider result
            pass


class GeminiOcrClient:
    """Call Gemini Flash with bounded retries and rotate credentials safely."""

    def __init__(
        self,
        *,
        settings: Settings = settings,
        client_factory: ClientFactory = _default_client_factory,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self.sleep = sleep
        self.jitter = jitter

    def generate_batch(
        self,
        pages: Sequence[RenderedPage],
        validator: Validator,
    ) -> OcrBatchResult:
        page_numbers = tuple(page.page_number for page in pages)
        self._validate_pages(pages, page_numbers)
        keys = self.settings.gemini_api_keys
        all_usage: list[OcrUsage] = []
        best_partial: OcrBatchResult | None = None
        best_partial_count = 0
        final_kind = "no_keys_configured"
        batch_started = time.monotonic()
        last_output_error: GeminiOutputInvalid | None = None

        if not keys:
            raise GeminiKeysExhausted(
                final_kind=final_kind,
                best_partial=None,
                attempt_usage=(),
            )

        for key_index, api_key in enumerate(keys):
            for attempt_index in range(_MAX_ATTEMPTS_PER_KEY):
                attempt_started = time.monotonic()
                client = None
                try:
                    client = self.client_factory(api_key)
                    response = client.models.generate_content(
                        model=self.settings.arabic_gemini_printed_model,
                        contents=self._contents(pages),
                        config=self._request_config(),
                    )
                except errors.APIError as error:
                    _safe_close(client)
                    duration_ms = self._elapsed_ms(attempt_started)
                    code = _token_count(getattr(error, "code", 0))
                    final_kind = self._api_failure_kind(code)
                    self._log_attempt_failure(
                        key_index, page_numbers, final_kind, duration_ms
                    )

                    if final_kind == "invalid_request":
                        raise GeminiRequestInvalid(
                            f"Gemini rejected the OCR request (HTTP {code})."
                        ) from None
                    if final_kind == "transient_http" and attempt_index == 0:
                        self._bounded_retry_delay()
                        continue
                    break
                except (httpx.TransportError, TimeoutError, ConnectionError):
                    _safe_close(client)
                    duration_ms = self._elapsed_ms(attempt_started)
                    final_kind = "network_error"
                    self._log_attempt_failure(
                        key_index, page_numbers, final_kind, duration_ms
                    )
                    if attempt_index == 0:
                        self._bounded_retry_delay()
                        continue
                    break
                except Exception:
                    _safe_close(client)
                    # SDK/configuration/programming errors are not quota
                    # failures; never spray the same invalid request at every key.
                    self._log_attempt_failure(
                        key_index,
                        page_numbers,
                        "client_error",
                        self._elapsed_ms(attempt_started),
                    )
                    raise GeminiRequestInvalid(
                        "Gemini OCR client could not issue the configured request."
                    ) from None
                else:
                    _safe_close(client)

                output_usage = _usage_from_response(response)
                all_usage.append(output_usage)
                text = getattr(response, "text", None)
                completed_pages = 0
                try:
                    if not isinstance(text, str) or not text.strip():
                        raise GeminiOutputInvalid("Gemini returned empty OCR text.")
                    try:
                        reported_pages = validator(text, page_numbers)
                    except Exception:
                        raise GeminiOutputInvalid(
                            "The OCR response validator rejected the response."
                        ) from None
                    if (
                        isinstance(reported_pages, bool)
                        or not isinstance(reported_pages, int)
                        or reported_pages < 0
                        or reported_pages > len(page_numbers)
                    ):
                        raise GeminiOutputInvalid(
                            "The OCR response validator returned an invalid page count."
                        )
                    completed_pages = reported_pages
                    if completed_pages == len(page_numbers):
                        duration_ms = self._elapsed_ms(batch_started)
                        self._log_attempt_success(
                            key_index,
                            page_numbers,
                            duration_ms,
                            _sum_usage(all_usage),
                        )
                        return OcrBatchResult(
                            text=text,
                            provider="gemini",
                            model=self.settings.arabic_gemini_printed_model,
                            key_index=key_index,
                            usage=_sum_usage(all_usage),
                            latency_ms=duration_ms,
                        )
                    raise GeminiOutputInvalid(
                        "Gemini returned an incomplete page-bounded OCR response."
                    )
                except GeminiOutputInvalid as output_error:
                    final_kind = "invalid_output"
                    last_output_error = output_error
                    if 0 < completed_pages < len(page_numbers):
                        if completed_pages > best_partial_count:
                            best_partial_count = completed_pages
                            best_partial = OcrBatchResult(
                                text=text,
                                provider="gemini",
                                model=self.settings.arabic_gemini_printed_model,
                                key_index=key_index,
                                usage=_sum_usage(all_usage),
                                latency_ms=self._elapsed_ms(batch_started),
                            )
                    self._log_attempt_failure(
                        key_index,
                        page_numbers,
                        final_kind,
                        self._elapsed_ms(attempt_started),
                        usage=output_usage,
                    )
                    if attempt_index == 0:
                        continue
                    break

        exhausted = GeminiKeysExhausted(
            final_kind=final_kind,
            best_partial=best_partial,
            attempt_usage=all_usage,
        )
        if final_kind == "invalid_output" and last_output_error is not None:
            raise exhausted from last_output_error
        raise exhausted

    def _request_config(self) -> types.GenerateContentConfig:
        media_resolution = getattr(
            types.MediaResolution,
            f"MEDIA_RESOLUTION_{self.settings.arabic_gemini_media_resolution}",
        )
        return types.GenerateContentConfig(
            max_output_tokens=self.settings.arabic_ocr_max_output_tokens,
            media_resolution=media_resolution,
            thinking_config=types.ThinkingConfig(
                thinking_level=self.settings.arabic_gemini_printed_thinking_level
            ),
        )

    @staticmethod
    def _contents(pages: Sequence[RenderedPage]) -> list[types.Part]:
        contents = [types.Part.from_text(text=batch_prompt(pages))]
        for page in pages:
            contents.extend(
                [
                    types.Part.from_text(text=f"PAGE {page.page_number}"),
                    types.Part.from_bytes(data=page.png, mime_type="image/png"),
                ]
            )
        return contents

    @staticmethod
    def _validate_pages(
        pages: Sequence[RenderedPage], page_numbers: tuple[int, ...]
    ) -> None:
        if not pages:
            raise GeminiRequestInvalid("At least one rendered page is required.")
        if any(number < 1 for number in page_numbers):
            raise GeminiRequestInvalid("Page numbers must be absolute and 1-based.")
        if tuple(sorted(set(page_numbers))) != page_numbers:
            raise GeminiRequestInvalid("Page numbers must be unique and increasing.")
        if any(not page.png for page in pages):
            raise GeminiRequestInvalid("Rendered pages must contain PNG bytes.")

    def _bounded_retry_delay(self) -> None:
        delay = self.jitter(_RETRY_DELAY_MIN_SEC, _RETRY_DELAY_MAX_SEC)
        self.sleep(min(max(delay, _RETRY_DELAY_MIN_SEC), _RETRY_DELAY_MAX_SEC))

    @staticmethod
    def _api_failure_kind(code: int) -> str:
        if code == 429:
            return "rate_limited"
        if code in (401, 403):
            return "authentication"
        if code == 408 or code >= 500:
            return "transient_http"
        return "invalid_request"

    @staticmethod
    def _page_range(page_numbers: Sequence[int]) -> str:
        return f"{page_numbers[0]}-{page_numbers[-1]}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def _log_attempt_failure(
        self,
        key_index: int,
        page_numbers: Sequence[int],
        failure_kind: str,
        latency_ms: int,
        *,
        usage: OcrUsage = OcrUsage(),
    ) -> None:
        logger.warning(
            "Gemini OCR attempt failed key_index=%d model=%s pages=%s "
            "failure_kind=%s latency_ms=%d prompt_tokens=%d output_tokens=%d "
            "thought_tokens=%d total_tokens=%d",
            key_index,
            self.settings.arabic_gemini_printed_model,
            self._page_range(page_numbers),
            failure_kind,
            latency_ms,
            usage.prompt_tokens,
            usage.output_tokens,
            usage.thought_tokens,
            usage.total_tokens,
        )

    def _log_attempt_success(
        self,
        key_index: int,
        page_numbers: Sequence[int],
        latency_ms: int,
        usage: OcrUsage,
    ) -> None:
        logger.info(
            "Gemini OCR succeeded key_index=%d model=%s pages=%s "
            "latency_ms=%d prompt_tokens=%d output_tokens=%d thought_tokens=%d "
            "total_tokens=%d",
            key_index,
            self.settings.arabic_gemini_printed_model,
            self._page_range(page_numbers),
            latency_ms,
            usage.prompt_tokens,
            usage.output_tokens,
            usage.thought_tokens,
            usage.total_tokens,
        )
