"""Arabic OCR through ModelGateway and Google Gemini providers."""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.core import tracing
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

_MAX_ATTEMPTS_PER_KEY = 3
_RETRY_DELAY_BASE_SEC = 0.25
_RETRY_DELAY_MAX_SEC = 4.0
_MAX_GATEWAY_ATTEMPTS = 12
_GATEWAY_RETRY_DELAY_MAX_SEC = 30.0
_GATEWAY_PROVIDER = "modelgateway_gemini"
_GATEWAY_FAILED_BATCHES_BEFORE_SKIP = 2


def batch_prompt(_pages: Sequence[RenderedPage]) -> str:
    """Ask for faithful page-bounded Markdown; page images carry the evidence."""

    return (
        "Transcribe the document body verbatim in its original language. "
        "Omit running headers, running footers, and bare page numbers. Keep footnotes. "
        "Output paragraphs, headings, lists, tables, displayed equations, and captions in Markdown. "
        "For Arabic or mixed Arabic/English pages, read the rightmost column top to bottom, "
        "then each column to the left through the leftmost column, each top to bottom; "
        "keep English spans and numerals in their original order. "
        "Do not translate, summarize, infer, perform spelling correction, invent diacritics, "
        "or modernize historical spelling. "
        "Mark illegible text as [غير واضح]. "
        "If a page has no text at all (a cover picture, a blank page, a full-page image), "
        "write exactly [NO_TEXT] between its markers. "
        "Wrap each page exactly with <!-- PAGE:n --> and <!-- END_PAGE:n --> "
        "markers, replacing n with that page's number shown before its image."
    )


def _default_client_factory(
    api_key: str,
    timeout_seconds: float,
    *,
    base_url: str | None = None,
) -> genai.Client:
    """Create an isolated SDK client and disable its implicit retry multiplier."""

    http_options: dict[str, Any] = {
        "timeout": round(timeout_seconds * 1000),
        "retry_options": types.HttpRetryOptions(attempts=1),
    }
    if base_url:
        http_options["base_url"] = base_url
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(**http_options),
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


def _error_details(error: errors.APIError) -> list[dict[str, Any]]:
    payload = getattr(error, "details", None)
    if not isinstance(payload, dict):
        return []
    body = payload.get("error", payload)
    if not isinstance(body, dict):
        return []
    details = body.get("details", [])
    return [detail for detail in details if isinstance(detail, dict)] if isinstance(details, list) else []


def _has_error_reason(error: errors.APIError, reason: str) -> bool:
    return any(detail.get("reason") == reason for detail in _error_details(error))


def _quota_metadata(error: errors.APIError) -> dict[str, Any]:
    for detail in _error_details(error):
        if not str(detail.get("@type", "")).endswith("QuotaFailure"):
            continue
        violations = detail.get("violations", [])
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            quota_id = str(violation.get("quotaId") or "")
            quota_metric = str(violation.get("quotaMetric") or "")
            clue = f"{quota_id} {quota_metric}".lower()
            if any(word in clue for word in ("perday", "per_day", "daily", "rpd")):
                scope = "daily"
            elif any(word in clue for word in ("perminute", "per_minute", "minute", "rpm", "tpm")):
                scope = "minute"
            else:
                scope = "unknown"
            # These are quota identifiers, not human error messages. Never
            # retain the full API payload, which could contain request data.
            return {
                "quota_scope": scope,
                "quota_id": quota_id[:160],
                "quota_metric": quota_metric[:160],
            }
    return {"quota_scope": "unknown"}


def _retry_after_seconds(error: errors.APIError) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds)


class GeminiOcrClient:
    """Try ModelGateway first, then Google Gemini, with bounded retries."""

    def __init__(
        self,
        *,
        settings: Settings = settings,
        client_factory: ClientFactory | None = None,
        writing_style: str = "printed",
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if writing_style not in {"printed", "handwritten"}:
            raise ValueError("Gemini OCR writing style must be printed or handwritten.")
        self.settings = settings
        self.client_factory = client_factory
        self.writing_style = writing_style
        self.sleep = sleep
        self.jitter = jitter
        self._blocked_keys: dict[int, str] = {}
        self._gateway_blocked = False
        self._gateway_failed_batches = 0

    @tracing.traced("llm.gemini_ocr", tracing.LLM, record_args=False)
    def generate_batch(
        self,
        pages: Sequence[RenderedPage],
        validator: Validator,
    ) -> OcrBatchResult:
        page_numbers = tuple(page.page_number for page in pages)
        self._validate_pages(pages, page_numbers)
        batch_started = time.monotonic()
        prior_usage: list[OcrUsage] = []
        prior_metadata: list[dict[str, Any]] = []
        prior_attempts = 0
        best_partial: OcrBatchResult | None = None
        best_partial_count = 0
        final_kind = "no_keys_configured"
        final_provider: str | None = None

        gateway_key = self.settings.modelgateway_api_key.strip()
        if gateway_key and not self._gateway_blocked:
            try:
                gateway_result = self._generate_gateway(
                    pages,
                    page_numbers,
                    validator,
                    gateway_key,
                    batch_started,
                )
            except GeminiRequestInvalid:
                raise
            except GeminiKeysExhausted as error:
                prior_usage.extend(error.attempt_usage)
                prior_metadata.extend(error.attempt_metadata)
                prior_attempts += error.attempt_count
                best_partial, best_partial_count = self._better_partial(
                    best_partial,
                    best_partial_count,
                    error.best_partial,
                    validator,
                    page_numbers,
                )
                final_kind = error.final_kind
                final_provider = error.final_provider or _GATEWAY_PROVIDER
                # ⚠ When every gateway seller is failing, each page would wait
                # out the gateway's full retry budget before the Google keys
                # answer (live, 2026-09-30: 16 pages took 10.7 min). After two
                # exhausted batches in a row the rest of THIS document skips
                # the gateway; the next document (a new client) tries it again.
                self._gateway_failed_batches += 1
                if self._gateway_failed_batches >= _GATEWAY_FAILED_BATCHES_BEFORE_SKIP:
                    self._gateway_blocked = True
                    logger.warning(
                        "ModelGateway failed %d batches in a row; using the fallback "
                        "providers for the rest of this document",
                        self._gateway_failed_batches,
                    )
            else:
                self._gateway_failed_batches = 0
                return self._with_prior_attempts(
                    gateway_result,
                    prior_usage,
                    prior_metadata,
                    prior_attempts,
                    batch_started,
                )

        google_keys = self.settings.gemini_api_keys
        if google_keys:
            try:
                google_result = self._generate_key_provider(
                    provider="google",
                    pages=pages,
                    page_numbers=page_numbers,
                    validator=validator,
                    keys=google_keys,
                    batch_started=batch_started,
                )
            except GeminiRequestInvalid:
                raise
            except GeminiKeysExhausted as error:
                prior_usage.extend(error.attempt_usage)
                prior_metadata.extend(error.attempt_metadata)
                prior_attempts += error.attempt_count
                best_partial, best_partial_count = self._better_partial(
                    best_partial,
                    best_partial_count,
                    error.best_partial,
                    validator,
                    page_numbers,
                )
                final_kind = error.final_kind
                final_provider = error.final_provider or self._provider_label("google")
            else:
                return self._with_prior_attempts(
                    google_result,
                    prior_usage,
                    prior_metadata,
                    prior_attempts,
                    batch_started,
                )
        elif not gateway_key and prior_attempts == 0:
            final_kind = "no_keys_configured"
            final_provider = self._provider_label("google")

        exhausted = GeminiKeysExhausted(
            final_kind=final_kind,
            best_partial=best_partial,
            attempt_usage=prior_usage,
            attempt_count=prior_attempts,
            latency_ms=self._elapsed_ms(batch_started),
            attempt_metadata=prior_metadata,
            final_provider=final_provider,
        )
        raise exhausted

    def _generate_gateway(
        self,
        pages: Sequence[RenderedPage],
        page_numbers: tuple[int, ...],
        validator: Validator,
        api_key: str,
        batch_started: float,
    ) -> OcrBatchResult:
        batch_size = self.settings.arabic_gemini_gateway_batch_pages
        budget: dict[str, float] = {
            "started": batch_started,
            "waited": 0.0,
        }
        results: list[OcrBatchResult] = []
        usage: list[OcrUsage] = []
        metadata: list[dict[str, Any]] = []
        attempts = 0
        model = self._model_for("gateway")
        provider = self._provider_label("gateway")

        for offset in range(0, len(pages), batch_size):
            batch_pages = pages[offset:offset + batch_size]
            batch_numbers = tuple(page.page_number for page in batch_pages)
            try:
                result = self._generate_key_provider(
                    provider="gateway",
                    pages=batch_pages,
                    page_numbers=batch_numbers,
                    validator=validator,
                    keys=[api_key],
                    batch_started=batch_started,
                    retry_budget=budget,
                )
            except GeminiRequestInvalid:
                raise
            except GeminiKeysExhausted as error:
                usage.extend(error.attempt_usage)
                metadata.extend(error.attempt_metadata)
                attempts += error.attempt_count
                previous_text = [item.text for item in results]
                if error.best_partial is not None:
                    previous_text.append(error.best_partial.text)
                combined_text = "\n\n".join(previous_text)
                partial_count = 0
                if combined_text:
                    try:
                        partial_count = validator(combined_text, page_numbers)
                    except Exception:
                        partial_count = 0
                partial = None
                if partial_count > 0:
                    partial = OcrBatchResult(
                        text=combined_text,
                        provider=provider,
                        model=model,
                        key_index=0,
                        usage=_sum_usage(usage),
                        latency_ms=self._elapsed_ms(batch_started),
                        attempt_count=attempts,
                        attempt_metadata=tuple(metadata),
                    )
                raise GeminiKeysExhausted(
                    final_kind=error.final_kind,
                    best_partial=partial,
                    attempt_usage=usage,
                    attempt_count=attempts,
                    latency_ms=self._elapsed_ms(batch_started),
                    attempt_metadata=metadata,
                    final_provider=provider,
                )
            results.append(result)
            usage.extend((result.usage,))
            metadata.extend(result.attempt_metadata)
            attempts += result.attempt_count

        return OcrBatchResult(
            text="\n\n".join(result.text for result in results),
            provider=provider,
            model=model,
            key_index=0,
            usage=_sum_usage(usage),
            latency_ms=self._elapsed_ms(batch_started),
            attempt_count=attempts,
            attempt_metadata=tuple(metadata),
        )

    def _generate_key_provider(
        self,
        *,
        provider: str,
        pages: Sequence[RenderedPage],
        page_numbers: tuple[int, ...],
        validator: Validator,
        keys: Sequence[str],
        batch_started: float,
        retry_budget: dict[str, float] | None = None,
    ) -> OcrBatchResult:
        model = self._model_for(provider)
        provider_label = self._provider_label(provider)
        all_usage: list[OcrUsage] = []
        best_partial: OcrBatchResult | None = None
        best_partial_count = 0
        final_kind = "no_keys_configured"
        last_output_error: GeminiOutputInvalid | None = None
        attempt_count = 0
        attempt_metadata: list[dict[str, Any]] = []
        max_attempts = (
            _MAX_GATEWAY_ATTEMPTS if provider == "gateway" else _MAX_ATTEMPTS_PER_KEY
        )

        if not keys:
            raise GeminiKeysExhausted(
                final_kind=final_kind,
                best_partial=None,
                attempt_usage=(),
                final_provider=provider_label,
            )

        for key_index, api_key in enumerate(keys):
            if provider == "google" and key_index in self._blocked_keys:
                final_kind = self._blocked_keys[key_index]
                continue
            rate_retry_used = False
            for attempt_index in range(max_attempts):
                attempt_started = time.monotonic()
                attempt_count += 1
                client = None
                try:
                    client = self._make_client(api_key, provider)
                    response = client.models.generate_content(
                        model=model,
                        contents=self._contents(pages, provider),
                        config=self._request_config(provider),
                    )
                except errors.APIError as error:
                    _safe_close(client)
                    duration_ms = self._elapsed_ms(attempt_started)
                    code = _token_count(getattr(error, "code", 0))
                    quota = _quota_metadata(error) if code == 429 else {}
                    final_kind = self._api_failure_kind(
                        code, error, quota, provider=provider
                    )
                    retry_after = (
                        _retry_after_seconds(error)
                        if code == 429 or provider == "gateway"
                        else None
                    )
                    attempt_metadata.append({
                        "provider": provider_label,
                        "model": model,
                        "key_index": key_index,
                        "status_code": code,
                        "failure_kind": final_kind,
                        **quota,
                        **(
                            {"retry_after_seconds": retry_after}
                            if retry_after is not None
                            else {}
                        ),
                    })
                    self._log_attempt_failure(
                        provider_label, model, key_index, page_numbers,
                        final_kind, duration_ms,
                    )

                    if final_kind == "invalid_request":
                        raise GeminiRequestInvalid(
                            f"Gemini rejected the OCR request (HTTP {code})."
                        ) from None
                    if final_kind in {"authentication", "daily_quota"}:
                        if provider == "gateway":
                            self._gateway_blocked = True
                        else:
                            self._blocked_keys[key_index] = final_kind
                        break

                    if provider == "gateway":
                        if (
                            final_kind
                            in {"timeout", "transient_http", "network_error", "rate_limited"}
                            and attempt_index + 1 < max_attempts
                        ):
                            delay = (
                                retry_after
                                if retry_after is not None
                                else self._gateway_retry_delay(attempt_index)
                            )
                            remaining = self._gateway_wait_remaining(retry_budget)
                            if delay <= remaining:
                                self.sleep(delay)
                                if retry_budget is not None:
                                    retry_budget["waited"] += delay
                                continue
                        break

                    if (
                        code == 429
                        and not rate_retry_used
                        and retry_after is not None
                        and retry_after <= self.settings.arabic_gemini_retry_after_max_seconds
                        and attempt_index + 1 < max_attempts
                    ):
                        rate_retry_used = True
                        self.sleep(retry_after)
                        continue
                    if (
                        final_kind in {"timeout", "transient_http"}
                        and attempt_index + 1 < max_attempts
                    ):
                        self._bounded_retry_delay(attempt_index)
                        continue
                    break
                except (httpx.TimeoutException, TimeoutError):
                    _safe_close(client)
                    duration_ms = self._elapsed_ms(attempt_started)
                    final_kind = "timeout"
                    attempt_metadata.append({
                        "provider": provider_label,
                        "model": model,
                        "key_index": key_index,
                        "status_code": 408,
                        "failure_kind": final_kind,
                    })
                    self._log_attempt_failure(
                        provider_label, model, key_index, page_numbers,
                        final_kind, duration_ms,
                    )
                    if provider == "gateway":
                        if attempt_index + 1 < max_attempts:
                            delay = self._gateway_retry_delay(attempt_index)
                            remaining = self._gateway_wait_remaining(retry_budget)
                            if delay <= remaining:
                                self.sleep(delay)
                                if retry_budget is not None:
                                    retry_budget["waited"] += delay
                                continue
                        break
                    if attempt_index + 1 < max_attempts:
                        self._bounded_retry_delay(attempt_index)
                        continue
                    break
                except (httpx.TransportError, ConnectionError):
                    _safe_close(client)
                    duration_ms = self._elapsed_ms(attempt_started)
                    final_kind = "network_error"
                    attempt_metadata.append({
                        "provider": provider_label,
                        "model": model,
                        "key_index": key_index,
                        "status_code": None,
                        "failure_kind": final_kind,
                    })
                    self._log_attempt_failure(
                        provider_label, model, key_index, page_numbers,
                        final_kind, duration_ms,
                    )
                    if provider == "gateway":
                        if attempt_index + 1 < max_attempts:
                            delay = self._gateway_retry_delay(attempt_index)
                            remaining = self._gateway_wait_remaining(retry_budget)
                            if delay <= remaining:
                                self.sleep(delay)
                                if retry_budget is not None:
                                    retry_budget["waited"] += delay
                                continue
                        break
                    if attempt_index + 1 < max_attempts:
                        self._bounded_retry_delay(attempt_index)
                        continue
                    break
                except Exception:
                    _safe_close(client)
                    attempt_metadata.append({
                        "provider": provider_label,
                        "model": model,
                        "key_index": key_index,
                        "status_code": None,
                        "failure_kind": "client_error",
                    })
                    self._log_attempt_failure(
                        provider_label,
                        model,
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
                        attempt_metadata.append({
                            "provider": provider_label,
                            "model": model,
                            "key_index": key_index,
                            "status_code": 200,
                            "failure_kind": None,
                        })
                        duration_ms = self._elapsed_ms(batch_started)
                        self._log_attempt_success(
                            provider_label,
                            model,
                            key_index,
                            page_numbers,
                            duration_ms,
                            _sum_usage(all_usage),
                        )
                        return OcrBatchResult(
                            text=text,
                            provider=provider_label,
                            model=model,
                            key_index=key_index,
                            usage=_sum_usage(all_usage),
                            latency_ms=duration_ms,
                            attempt_count=attempt_count,
                            attempt_metadata=tuple(attempt_metadata),
                        )
                    raise GeminiOutputInvalid(
                        "Gemini returned an incomplete page-bounded OCR response."
                    )
                except GeminiOutputInvalid as output_error:
                    final_kind = "invalid_output"
                    last_output_error = output_error
                    attempt_metadata.append({
                        "provider": provider_label,
                        "model": model,
                        "key_index": key_index,
                        "status_code": 200,
                        "failure_kind": final_kind,
                    })
                    if 0 < completed_pages < len(page_numbers):
                        if completed_pages > best_partial_count:
                            best_partial_count = completed_pages
                            best_partial = OcrBatchResult(
                                text=text,
                                provider=provider_label,
                                model=model,
                                key_index=key_index,
                                usage=_sum_usage(all_usage),
                                latency_ms=self._elapsed_ms(batch_started),
                                attempt_count=attempt_count,
                                attempt_metadata=tuple(attempt_metadata),
                            )
                    self._log_attempt_failure(
                        provider_label,
                        model,
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
            attempt_count=attempt_count,
            latency_ms=self._elapsed_ms(batch_started),
            attempt_metadata=attempt_metadata,
            final_provider=provider_label,
        )
        if final_kind == "invalid_output" and last_output_error is not None:
            raise exhausted from last_output_error
        raise exhausted

    def _make_client(self, api_key: str, provider: str) -> Any:
        if self.client_factory is not None:
            return self.client_factory(api_key)
        base_url = (
            self.settings.modelgateway_base_url if provider == "gateway" else None
        )
        return _default_client_factory(
            api_key,
            self.settings.arabic_gemini_timeout_seconds,
            base_url=base_url,
        )

    def _model_for(self, provider: str) -> str:
        if provider == "gateway":
            attribute = (
                "arabic_gateway_handwritten_model"
                if self.writing_style == "handwritten"
                else "arabic_gateway_printed_model"
            )
        else:
            attribute = (
                "arabic_gemini_handwritten_model"
                if self.writing_style == "handwritten"
                else "arabic_gemini_printed_model"
            )
        return getattr(self.settings, attribute)

    def _provider_label(self, provider: str) -> str:
        if provider == "gateway":
            return _GATEWAY_PROVIDER
        if self.writing_style == "handwritten":
            return "gemini_arabic_pro"
        return "gemini_arabic_flash"

    def _request_config(self, provider: str) -> types.GenerateContentConfig:
        send_thinking = (
            provider != "gateway"
            and self.settings.arabic_gemini_send_thinking_config
        )
        return types.GenerateContentConfig(
            max_output_tokens=self.settings.arabic_ocr_max_output_tokens,
            thinking_config=(
                types.ThinkingConfig(
                    thinking_level=self.settings.arabic_gemini_printed_thinking_level
                )
                if send_thinking
                else None
            ),
        )

    def _contents(
        self, pages: Sequence[RenderedPage], provider: str
    ) -> list[types.Part]:
        contents = [types.Part.from_text(text=batch_prompt(pages))]
        send_resolution = (
            provider != "gateway"
            and self.settings.arabic_gemini_send_media_resolution
        )
        media_resolution = None
        if send_resolution:
            media_resolution = getattr(
                types.PartMediaResolutionLevel,
                f"MEDIA_RESOLUTION_{self.settings.arabic_gemini_media_resolution}",
            )
        for page in pages:
            page_parts = [
                types.Part.from_text(text=f"PAGE {page.page_number}"),
            ]
            if media_resolution is None:
                page_parts.append(
                    types.Part.from_bytes(data=page.png, mime_type="image/png")
                )
            else:
                page_parts.append(
                    types.Part.from_bytes(
                        data=page.png,
                        mime_type="image/png",
                        media_resolution=media_resolution,
                    )
                )
            contents.extend(page_parts)
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

    def _bounded_retry_delay(self, attempt_index: int) -> None:
        base = min(_RETRY_DELAY_BASE_SEC * (2 ** attempt_index), _RETRY_DELAY_MAX_SEC)
        delay = self.jitter(base, min(base * 1.5, _RETRY_DELAY_MAX_SEC))
        self.sleep(min(max(delay, base), _RETRY_DELAY_MAX_SEC))

    def _gateway_retry_delay(self, attempt_index: int) -> float:
        base = min(
            _RETRY_DELAY_BASE_SEC * (2 ** attempt_index),
            _GATEWAY_RETRY_DELAY_MAX_SEC,
        )
        delay = self.jitter(
            base,
            min(base * 1.5, _GATEWAY_RETRY_DELAY_MAX_SEC),
        )
        return min(max(delay, base), _GATEWAY_RETRY_DELAY_MAX_SEC)

    def _gateway_wait_remaining(self, budget: dict[str, float] | None) -> float:
        if budget is None:
            return 0.0
        max_wait = self.settings.arabic_gemini_gateway_max_wait_seconds
        real_elapsed = max(0.0, time.monotonic() - budget["started"])
        accounted_elapsed = max(real_elapsed, budget["waited"])
        return max(0.0, max_wait - accounted_elapsed)

    @staticmethod
    def _api_failure_kind(
        code: int,
        error: errors.APIError,
        quota: dict[str, Any],
        *,
        provider: str = "google",
    ) -> str:
        if provider == "gateway":
            # ⚠ ModelGateway hands each request to a marketplace seller, and a
            # broken seller answers 402 ("upstream provider balance is
            # insufficient"), 500 ("could not convert this request"), 400 or
            # 503 while the next attempt — another seller — succeeds (measured
            # on production, 2026-09-30). So for the gateway only a rejected
            # KEY is final; everything else is retried and, once the gateway's
            # budget is spent, the batch moves on to the Google keys, then
            # Gemma — never a hard stop.
            message = GeminiOcrClient._api_error_message(error).lower()
            if code == 401 or (code == 403 and "upstream provider" not in message):
                return "authentication"
            if code == 429:
                return "rate_limited"
            return "transient_http"
        if code == 429:
            return "daily_quota" if quota.get("quota_scope") == "daily" else "rate_limited"
        if code == 400 and _has_error_reason(error, "API_KEY_INVALID"):
            return "authentication"
        if code in (401, 403):
            return "authentication"
        if code == 408:
            return "timeout"
        if code >= 500:
            return "transient_http"
        return "invalid_request"

    @staticmethod
    def _api_error_message(error: errors.APIError) -> str:
        payload = getattr(error, "response_json", None)
        if isinstance(payload, dict):
            body = payload.get("error", payload)
            if isinstance(body, dict) and isinstance(body.get("message"), str):
                return body["message"]
        message = getattr(error, "message", "")
        return message if isinstance(message, str) else ""

    @staticmethod
    def _better_partial(
        current: OcrBatchResult | None,
        current_count: int,
        candidate: OcrBatchResult | None,
        validator: Validator,
        page_numbers: tuple[int, ...],
    ) -> tuple[OcrBatchResult | None, int]:
        if candidate is None:
            return current, current_count
        try:
            candidate_count = validator(candidate.text, page_numbers)
        except Exception:
            candidate_count = 0
        if candidate_count > current_count:
            return candidate, candidate_count
        return current, current_count

    @staticmethod
    def _with_prior_attempts(
        result: OcrBatchResult,
        prior_usage: Sequence[OcrUsage],
        prior_metadata: Sequence[dict[str, Any]],
        prior_attempts: int,
        batch_started: float,
    ) -> OcrBatchResult:
        usage = [*prior_usage, result.usage]
        return OcrBatchResult(
            text=result.text,
            provider=result.provider,
            model=result.model,
            key_index=result.key_index,
            usage=_sum_usage(usage),
            latency_ms=GeminiOcrClient._elapsed_ms(batch_started),
            attempt_count=prior_attempts + result.attempt_count,
            attempt_metadata=tuple([*prior_metadata, *result.attempt_metadata]),
        )

    @staticmethod
    def _page_range(page_numbers: Sequence[int]) -> str:
        return f"{page_numbers[0]}-{page_numbers[-1]}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    def _log_attempt_failure(
        self,
        provider: str,
        model: str,
        key_index: int,
        page_numbers: Sequence[int],
        failure_kind: str,
        latency_ms: int,
        *,
        usage: OcrUsage = OcrUsage(),
    ) -> None:
        logger.warning(
            "Gemini OCR attempt failed provider=%s key_index=%d model=%s pages=%s "
            "failure_kind=%s latency_ms=%d prompt_tokens=%d output_tokens=%d "
            "thought_tokens=%d total_tokens=%d",
            provider,
            key_index,
            model,
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
        provider: str,
        model: str,
        key_index: int,
        page_numbers: Sequence[int],
        latency_ms: int,
        usage: OcrUsage,
    ) -> None:
        logger.info(
            "Gemini OCR succeeded provider=%s key_index=%d model=%s pages=%s "
            "latency_ms=%d prompt_tokens=%d output_tokens=%d thought_tokens=%d "
            "total_tokens=%d",
            provider,
            key_index,
            model,
            self._page_range(page_numbers),
            latency_ms,
            usage.prompt_tokens,
            usage.output_tokens,
            usage.thought_tokens,
            usage.total_tokens,
        )
