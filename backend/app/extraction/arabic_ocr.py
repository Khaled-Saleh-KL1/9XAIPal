"""Resumable Arabic OCR orchestration with atomic artifact publication."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import fitz

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.extraction.arabic_adapter import (
    pages_to_content_list,
    parse_complete_page_prefix,
)
from app.extraction.arabic_repair import repair_gemma_page
from app.extraction.arabic_types import (
    ArabicExtractionResult,
    ArabicOcrPage,
    ClassificationDecision,
    DocumentRoute,
    GemmaKeysExhausted,
    GemmaOutputInvalid,
    GemmaRequestInvalid,
    GeminiKeysExhausted,
    GeminiRequestInvalid,
    OcrBatchResult,
    OcrUsage,
    RenderedPage,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int], None]
_GEMINI_PROVIDER = "gemini_arabic_flash"
_GEMMA_PROVIDER = "gemma4_arabic_fallback"
_HYBRID_EXTRACTOR = "gemini_gemma_arabic_hybrid"
_GEMINI_FALLBACK_KINDS = frozenset(
    {
        "no_keys_configured",
        "authentication",
        "permission",
        "rate_limited",
        "quota",
        "quota_exceeded",
        "daily_quota",
        "timeout",
        "network",
        "network_error",
        "transient_http",
        "service",
        "provider_error",
        "provider_unavailable",
        "invalid_output",
        "empty_output",
        "incomplete_output",
        "empty-output",
        "incomplete-output",
    }
)


class ArabicExtractionFailed(RuntimeError):
    """Arabic OCR did not produce a fully validated document."""


class ArabicOcrBatchInvalid(ArabicExtractionFailed):
    """A single OCR page stayed invalid after a bounded retry."""

    failure_kind = "invalid_output"
    retryable = True

    def __init__(self, page_number: int) -> None:
        self.page_number = page_number
        super().__init__(f"Gemma Arabic OCR page {page_number} failed output validation.")


def extract_arabic_document(
    *,
    pdf_path: Path,
    output_dir: Path,
    classification: ClassificationDecision,
    gemini_client: Any,
    gemma_client: Any,
    settings: Settings = settings,
    progress_callback: ProgressCallback | None = None,
) -> ArabicExtractionResult:
    """OCR a printed Arabic PDF, resume at page boundaries, and publish atomically."""

    source_path = Path(pdf_path)
    target_dir = Path(output_dir)
    if not target_dir.name or target_dir == target_dir.parent:
        raise ArabicExtractionFailed("The Arabic OCR output directory is invalid.")
    if (
        classification.route != DocumentRoute.ARABIC_PRINTED
        or classification.writing_style != "printed"
    ):
        raise ArabicExtractionFailed(
            "Arabic OCR requires a confidently classified printed document."
        )
    if not source_path.is_file():
        raise ArabicExtractionFailed("The Arabic OCR source PDF is unavailable.")
    if settings.arabic_ocr_dpi < 1:
        raise ArabicExtractionFailed("Arabic OCR rendering DPI must be positive.")
    if (
        settings.arabic_ocr_single_request_max_pages < 1
        or settings.arabic_ocr_batch_pages < 1
    ):
        raise ArabicExtractionFailed("Arabic OCR batch sizes must be positive.")

    try:
        rendered_pages, source_texts = _render_document(source_path, settings.arabic_ocr_dpi)
    except Exception:
        raise ArabicExtractionFailed("The Arabic OCR source PDF could not be rendered.") from None
    page_count = len(rendered_pages)
    if page_count == 0:
        raise ArabicExtractionFailed("The Arabic OCR source PDF contains no pages.")

    batch_size = (
        page_count
        if page_count <= settings.arabic_ocr_single_request_max_pages
        else settings.arabic_ocr_batch_pages
    )
    page_map: dict[int, ArabicOcrPage] = {}
    provider_runs: list[dict[str, Any]] = []
    usage_parts: list[OcrUsage] = []
    next_page = 1
    gemma_mode = False
    gemini_model = _configured_model(
        gemini_client,
        "arabic_gemini_printed_model",
        settings.arabic_gemini_printed_model,
    )

    while next_page <= page_count and not gemma_mode:
        batch_end = min(page_count, next_page + batch_size - 1)
        no_progress_retries = 0

        while next_page <= batch_end:
            request_pages = rendered_pages[next_page - 1 : batch_end]
            expected_numbers = tuple(page.page_number for page in request_pages)
            if not request_pages or expected_numbers[0] != next_page:
                raise ArabicExtractionFailed("The Arabic OCR page cursor became inconsistent.")

            def validator(text: str, requested: Sequence[int]) -> int:
                parsed = parse_complete_page_prefix(
                    text,
                    requested,
                    provider=_GEMINI_PROVIDER,
                    model=gemini_model,
                )
                return len(parsed.pages)

            try:
                result = gemini_client.generate_batch(request_pages, validator=validator)
            except GeminiRequestInvalid:
                raise ArabicExtractionFailed(
                    "Gemini rejected the Arabic OCR request configuration."
                ) from None
            except GeminiKeysExhausted as error:
                attempt_usage = tuple(error.attempt_usage)
                usage_parts.extend(attempt_usage)
                committed_before = next_page
                if error.best_partial is not None:
                    partial = parse_complete_page_prefix(
                        error.best_partial.text,
                        expected_numbers,
                        provider=error.best_partial.provider or _GEMINI_PROVIDER,
                        model=error.best_partial.model or gemini_model,
                    )
                    next_page = _commit_prefix(
                        partial.pages,
                        expected_numbers,
                        next_page,
                        page_map,
                        page_count,
                        progress_callback,
                    )
                committed = list(range(committed_before, next_page))
                provider_runs.append(
                    _provider_run(
                        provider=_GEMINI_PROVIDER,
                        model=(
                            error.best_partial.model
                            if error.best_partial is not None
                            else gemini_model
                        ),
                        requested_pages=expected_numbers,
                        committed_pages=committed,
                        attempt_count=error.attempt_count,
                        latency_ms=error.latency_ms,
                        usage=_sum_usage(attempt_usage),
                        failure_kind=error.final_kind,
                        attempt_metadata=error.attempt_metadata,
                    )
                )
                if error.final_kind not in _GEMINI_FALLBACK_KINDS:
                    raise ArabicExtractionFailed(
                        "Gemini Arabic OCR failed with a non-recoverable request error."
                    ) from None
                gemma_mode = True
                break
            except Exception:
                raise ArabicExtractionFailed("Gemini Arabic OCR failed unexpectedly.") from None

            result_usage = result.usage
            usage_parts.append(result_usage)
            result_provider = result.provider or _GEMINI_PROVIDER
            result_model = result.model or gemini_model
            parsed = parse_complete_page_prefix(
                result.text,
                expected_numbers,
                provider=result_provider,
                model=result_model,
            )
            committed_before = next_page
            next_page = _commit_prefix(
                parsed.pages,
                expected_numbers,
                next_page,
                page_map,
                page_count,
                progress_callback,
            )
            provider_runs.append(
                _provider_run(
                    provider=result_provider,
                    model=result_model,
                    requested_pages=expected_numbers,
                    committed_pages=list(range(committed_before, next_page)),
                    attempt_count=result.attempt_count,
                    latency_ms=result.latency_ms,
                    usage=result_usage,
                    attempt_metadata=result.attempt_metadata,
                    retry_count=1 if no_progress_retries else 0,
                    failure_kind=None if parsed.is_complete else "invalid_output",
                )
            )

            if parsed.is_complete:
                no_progress_retries = 0
                break
            if parsed.pages:
                no_progress_retries = 0
                continue
            if no_progress_retries == 0:
                no_progress_retries = 1
                continue
            gemma_mode = True
            break

    gemma_model = _configured_model(
        gemma_client,
        "arabic_gemma_fallback_model",
        settings.arabic_gemma_fallback_model,
    )
    repair_retry_count = 0
    while next_page <= page_count:
        page = rendered_pages[next_page - 1]
        started = time.monotonic()
        try:
            result = gemma_client.generate_page(page, writing_style="printed")
        except GemmaRequestInvalid:
            raise ArabicExtractionFailed(
                "The Gemma Arabic OCR request configuration is invalid."
            ) from None
        except GemmaKeysExhausted as error:
            usage = _sum_usage(error.attempt_usage)
            usage_parts.extend(error.attempt_usage)
            provider_runs.append(
                _provider_run(
                    provider=_GEMMA_PROVIDER,
                    model=gemma_model,
                    requested_pages=(next_page,),
                    committed_pages=(),
                    attempt_count=error.attempt_count,
                    latency_ms=error.latency_ms,
                    usage=usage,
                    failure_kind=error.final_kind,
                )
            )
            raise ArabicExtractionFailed(
                "Gemma Arabic OCR could not complete every remaining page."
            ) from None
        except Exception:
            raise ArabicExtractionFailed("Gemma Arabic OCR failed unexpectedly.") from None

        usage_parts.append(result.usage)
        parsed = parse_complete_page_prefix(
            result.text,
            expected_pages=(next_page,),
            provider=result.provider or _GEMMA_PROVIDER,
            model=result.model or gemma_model,
        )
        if not parsed.is_complete or len(parsed.pages) != 1:
            provider_runs.append(
                _provider_run(
                    provider=_GEMMA_PROVIDER,
                    model=result.model or gemma_model,
                    requested_pages=(next_page,),
                    committed_pages=(),
                    attempt_count=result.attempt_count,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    failure_kind="invalid_output",
                )
            )
            raise ArabicExtractionFailed("Gemma Arabic OCR returned an invalid page.")

        try:
            repaired_page = repair_gemma_page(
                parsed.pages[0], source_text=source_texts[next_page - 1]
            )
        except GemmaOutputInvalid:
            provider_runs.append(
                _provider_run(
                    provider=_GEMMA_PROVIDER,
                    model=result.model or gemma_model,
                    requested_pages=(next_page,),
                    committed_pages=(),
                    attempt_count=result.attempt_count,
                    latency_ms=result.latency_ms or _elapsed_ms(started),
                    usage=result.usage,
                    failure_kind="invalid_output",
                )
            )
            if repair_retry_count == 0:
                repair_retry_count = 1
                continue
            raise ArabicOcrBatchInvalid(next_page) from None
        page_map[next_page] = repaired_page
        provider_runs.append(
            _provider_run(
                provider=repaired_page.provider,
                model=result.model or gemma_model,
                requested_pages=(next_page,),
                committed_pages=(next_page,),
                attempt_count=result.attempt_count,
                latency_ms=result.latency_ms or _elapsed_ms(started),
                usage=result.usage,
            )
        )
        next_page += 1
        repair_retry_count = 0
        _notify_progress(progress_callback, next_page - 1, page_count)

    if tuple(page_map) != tuple(range(1, page_count + 1)):
        raise ArabicExtractionFailed("The Arabic OCR result contains a page gap or duplicate.")

    final_pages = tuple(page_map[number] for number in range(1, page_count + 1))
    extractor = _extractor_from_pages(final_pages)
    provider_summary = _provider_summary(final_pages)
    total_usage = _sum_usage(usage_parts)
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ArabicExtractionFailed("The Arabic OCR output location is unavailable.") from None
    staging_dir = target_dir.parent / f".{target_dir.name}.staging-{uuid4()}"
    try:
        staging_dir.mkdir()
        _write_artifacts(
            staging_dir,
            final_pages,
            extractor,
            classification,
            provider_summary,
            provider_runs,
            total_usage,
            page_count,
            settings,
        )
        _publish_staging(staging_dir, target_dir)
    except Exception:
        _remove_path(staging_dir)
        raise ArabicExtractionFailed(
            "Arabic OCR artifacts could not be published atomically."
        ) from None

    return ArabicExtractionResult(
        output_dir=target_dir,
        extractor=extractor,
        pages=final_pages,
        provider_summary=provider_summary,
        usage=total_usage,
    )


def _extractor_from_pages(pages: Sequence[ArabicOcrPage]) -> str:
    providers = {page.provider for page in pages}
    if providers == {_GEMINI_PROVIDER}:
        return _GEMINI_PROVIDER
    if providers == {_GEMMA_PROVIDER}:
        return _GEMMA_PROVIDER
    if providers == {_GEMINI_PROVIDER, _GEMMA_PROVIDER}:
        return _HYBRID_EXTRACTOR
    raise ArabicExtractionFailed("The Arabic OCR provider provenance is invalid.")


def _render_document(pdf_path: Path, dpi: int) -> tuple[list[RenderedPage], list[str]]:
    rendered: list[RenderedPage] = []
    source_texts: list[str] = []
    with fitz.open(pdf_path) as document:
        for page_number, source_page in enumerate(document, start=1):
            pixmap = source_page.get_pixmap(
                dpi=dpi,
                colorspace=fitz.csRGB,
                alpha=False,
            )
            rendered.append(
                RenderedPage(page_number=page_number, png=pixmap.tobytes("png"))
            )
            source_texts.append(source_page.get_text("text") or "")
    return rendered, source_texts


def _commit_prefix(
    pages: Sequence[ArabicOcrPage],
    requested_pages: Sequence[int],
    next_page: int,
    page_map: dict[int, ArabicOcrPage],
    page_count: int,
    progress_callback: ProgressCallback | None,
) -> int:
    for parsed_page in pages:
        if (
            parsed_page.page_number != next_page
            or parsed_page.page_number not in requested_pages
            or parsed_page.page_number in page_map
            or parsed_page.page_number > page_count
        ):
            raise ArabicExtractionFailed("The OCR response violated page ordering.")
        page_map[parsed_page.page_number] = parsed_page
        next_page += 1
        _notify_progress(progress_callback, next_page - 1, page_count)
    return next_page


def _notify_progress(
    callback: ProgressCallback | None, completed_pages: int, total_pages: int
) -> None:
    if callback is None:
        return
    try:
        callback(completed_pages, total_pages)
    except Exception:
        logger.warning(
            "Arabic OCR progress callback failed completed_pages=%d total_pages=%d",
            completed_pages,
            total_pages,
        )


def _configured_model(client: Any, attribute: str, default: str) -> str:
    client_settings = getattr(client, "settings", None)
    model = getattr(client_settings, attribute, None)
    return str(model or default)


def _provider_run(
    *,
    provider: str,
    model: str,
    requested_pages: Sequence[int],
    committed_pages: Sequence[int],
    attempt_count: int,
    latency_ms: int,
    usage: OcrUsage,
    retry_count: int | None = None,
    failure_kind: str | None = None,
    attempt_metadata: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    attempts = max(0, attempt_count)
    run = {
        "provider": provider,
        "model": model,
        "requested_pages": list(requested_pages),
        "committed_pages": list(committed_pages),
        "attempt_count": attempts,
        "retry_count": (
            max(0, attempts - 1) if retry_count is None else retry_count
        ),
        "latency_ms": max(0, latency_ms),
        "usage": _usage_dict(usage),
    }
    if failure_kind:
        run["failure_kind"] = failure_kind
    if attempt_metadata:
        run["attempt_metadata"] = list(attempt_metadata)
    return run


def _provider_summary(pages: Sequence[ArabicOcrPage]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for page in pages:
        if summary and summary[-1]["provider"] == page.provider:
            summary[-1]["pages"].append(page.page_number)
        else:
            summary.append({"provider": page.provider, "pages": [page.page_number]})
    return summary


def _usage_dict(usage: OcrUsage) -> dict[str, int]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "output_tokens": usage.output_tokens,
        "thought_tokens": usage.thought_tokens,
        "total_tokens": usage.total_tokens,
    }


def _sum_usage(usages: Sequence[OcrUsage]) -> OcrUsage:
    return OcrUsage(
        prompt_tokens=sum(item.prompt_tokens for item in usages),
        output_tokens=sum(item.output_tokens for item in usages),
        thought_tokens=sum(item.thought_tokens for item in usages),
        total_tokens=sum(item.total_tokens for item in usages),
    )


def _classification_manifest(decision: ClassificationDecision) -> dict[str, Any]:
    route = decision.route.value if isinstance(decision.route, DocumentRoute) else decision.route
    return {
        "route": route,
        "language": decision.language,
        "writing_style": decision.writing_style,
        "text_direction": decision.text_direction,
        "confidence": decision.confidence,
        "classifier_model": decision.classifier_model,
    }


def _write_artifacts(
    staging_dir: Path,
    pages: Sequence[ArabicOcrPage],
    extractor: str,
    classification: ClassificationDecision,
    provider_summary: list[dict[str, Any]],
    provider_runs: Sequence[dict[str, Any]],
    usage: OcrUsage,
    page_count: int,
    config: Settings,
) -> None:
    content_list = pages_to_content_list(pages)
    document_markdown = "\n\n".join(
        f"<!-- PAGE:{page.page_number} -->\n{page.markdown}\n"
        f"<!-- END_PAGE:{page.page_number} -->"
        for page in pages
    )
    raw_pages = [
        {
            "page_number": page.page_number,
            "provider": page.provider,
            "model": page.model,
            "raw_markdown": page.raw_markdown,
            "markdown": page.markdown,
            "repaired": page.repaired,
        }
        for page in pages
    ]
    manifest = {
        "schema_version": 1,
        "extractor": extractor,
        "classification": _classification_manifest(classification),
        "page_count": page_count,
        "rendering": {"dpi": config.arabic_ocr_dpi, "color_space": "RGB"},
        "batching": {
            "single_request_max_pages": config.arabic_ocr_single_request_max_pages,
            "batch_pages": config.arabic_ocr_batch_pages,
        },
        "provider_summary": provider_summary,
        "provider_runs": list(provider_runs),
        "usage": _usage_dict(usage),
    }
    for filename, value in (
        ("content_list.json", content_list),
        ("raw_pages.json", raw_pages),
        ("ocr_manifest.json", manifest),
    ):
        (staging_dir / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    (staging_dir / "document.md").write_text(
        document_markdown + "\n",
        encoding="utf-8",
    )


def _publish_staging(staging_dir: Path, target_dir: Path) -> None:
    backup_dir = target_dir.parent / f".{target_dir.name}.backup-{uuid4()}"
    moved_existing = False
    if target_dir.exists() or target_dir.is_symlink():
        os.replace(target_dir, backup_dir)
        moved_existing = True
    try:
        os.replace(staging_dir, target_dir)
    except Exception:
        if moved_existing and (backup_dir.exists() or backup_dir.is_symlink()):
            os.replace(backup_dir, target_dir)
        raise
    if moved_existing:
        try:
            _remove_path(backup_dir)
        except OSError:
            logger.warning("Arabic OCR published; prior artifact backup cleanup failed.")


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
