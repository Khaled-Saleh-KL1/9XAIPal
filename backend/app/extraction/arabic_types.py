"""Shared domain types for the isolated Arabic document pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


class DocumentRoute(str, Enum):
    ENGLISH = "english"
    ARABIC_PRINTED = "arabic_printed"
    ARABIC_HANDWRITTEN = "arabic_handwritten"
    ARABIC_STYLE_UNCERTAIN = "arabic_style_uncertain"


# Whole-document `documents.extractor` values written by the Arabic route
# (spec §11), including the reserved handwritten Pro value.
ARABIC_OCR_EXTRACTORS = frozenset({
    "gemini_arabic_flash",
    "gemma4_arabic_fallback",
    "gemini_gemma_arabic_hybrid",
    "gemini_arabic_pro",
})


def mineru_repairs_apply(extractor: str | None) -> bool:
    """Whether MinerU-specific glyph and heading repair may touch the chunks.

    Gemini output is stored without correction and Gemma output has its own
    provenance-gated repair (spec §2.8, §10), so neither goes through them.
    """
    return extractor not in ARABIC_OCR_EXTRACTORS


class ArabicRoutingError(RuntimeError):
    """A safe, user-displayable failure raised before Arabic OCR begins."""

    error_code = "arabic_routing_failed"
    public_message = "Arabic document routing failed. Please try again."


class ArabicStyleConfirmationRequired(ArabicRoutingError):
    """The local classifier abstained because the Arabic style is uncertain."""

    error_code = "arabic_style_confirmation_required"
    public_message = (
        "The Arabic writing style could not be identified confidently. "
        "Confirm whether the document is printed or handwritten, then retry."
    )

    def __init__(self) -> None:
        super().__init__(self.public_message)


class ArabicClassifierUnavailableError(ArabicRoutingError):
    """The local language/style classifier could not safely route the file."""

    error_code = "arabic_classifier_unavailable"
    public_message = (
        "The local document classifier could not identify this file. "
        "Start Ollama and make sure the configured vision model is available, then retry."
    )

    def __init__(self) -> None:
        super().__init__(self.public_message)


class HandwrittenArabicUnavailable(ArabicRoutingError):
    """Handwritten Arabic is detected but Gemini Pro billing is unavailable."""

    error_code = "handwritten_arabic_unavailable"
    public_message = (
        "Handwritten Arabic extraction is not currently available because it "
        "requires Gemini Pro with a billing-enabled account. No text was "
        "extracted, and your original file has been kept."
    )

    def __init__(self) -> None:
        super().__init__(self.public_message)


class ArabicGeminiProNotConfigured(HandwrittenArabicUnavailable):
    """The handwritten feature switch cannot enable unconfigured Pro access."""

    error_code = "arabic_gemini_pro_not_configured"


@dataclass(frozen=True)
class PageStyleVote:
    page_idx: int
    language: str
    writing_style: str
    confidence: float
    evidence: str = ""
    region: str = "page"
    primary_content: bool = True


@dataclass(frozen=True)
class ClassificationDecision:
    route: DocumentRoute
    language: str
    writing_style: str
    text_direction: str
    confidence: float
    classifier_model: str
    votes: tuple[PageStyleVote, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RenderedPage:
    """A rendered, absolute-numbered page ready for a vision OCR request."""

    page_number: int
    png: bytes


@dataclass(frozen=True)
class OcrUsage:
    """Token usage reported by one or more OCR provider attempts."""

    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class OcrBatchResult:
    """One complete or best-effort partial provider response."""

    text: str
    provider: str
    model: str
    key_index: int | None
    usage: OcrUsage
    latency_ms: int
    attempt_count: int = 1
    attempt_metadata: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class GeminiRequestInvalid(RuntimeError):
    """The Gemini request is invalid and retrying another key cannot help."""


class GeminiOutputInvalid(RuntimeError):
    """Gemini returned text without the requested complete page structure."""


class GeminiKeysExhausted(RuntimeError):
    """All configured Gemini keys failed; includes safe partial output/usage."""

    def __init__(
        self,
        final_kind: str,
        best_partial: OcrBatchResult | None,
        attempt_usage: Sequence[OcrUsage],
        *,
        attempt_count: int | None = None,
        latency_ms: int = 0,
        attempt_metadata: Sequence[dict[str, Any]] = (),
    ) -> None:
        self.final_kind = final_kind
        self.best_partial = best_partial
        self.attempt_usage = tuple(attempt_usage)
        self.attempt_count = (
            len(self.attempt_usage) if attempt_count is None else attempt_count
        )
        self.latency_ms = latency_ms
        self.attempt_metadata = tuple(attempt_metadata)
        super().__init__(f"Gemini OCR keys exhausted ({final_kind}).")


@dataclass(frozen=True)
class ArabicOcrPage:
    """One committed page of provider Markdown and its provenance."""

    page_number: int
    raw_markdown: str
    markdown: str
    provider: str
    model: str
    repaired: bool = False


@dataclass(frozen=True)
class ParsedPagePrefix:
    """The valid contiguous prefix parsed from a multi-page OCR response."""

    pages: tuple[ArabicOcrPage, ...]
    first_uncommitted_page: int | None
    is_complete: bool


class GemmaOutputInvalid(GeminiOutputInvalid):
    """Gemma returned empty, malformed, incomplete, or looping OCR output."""


class GemmaRequestInvalid(RuntimeError):
    """The direct Ollama request is invalid and key rotation cannot fix it."""


class GemmaKeysExhausted(RuntimeError):
    """The dedicated Gemma OCR target and its configured keys all failed."""

    def __init__(
        self,
        final_kind: str,
        attempt_usage: Sequence[OcrUsage],
        *,
        attempt_count: int | None = None,
        latency_ms: int = 0,
    ) -> None:
        self.final_kind = final_kind
        self.attempt_usage = tuple(attempt_usage)
        self.attempt_count = (
            len(self.attempt_usage) if attempt_count is None else attempt_count
        )
        self.latency_ms = latency_ms
        super().__init__(f"Gemma Arabic OCR keys exhausted ({final_kind}).")


@dataclass(frozen=True)
class ArabicExtractionResult:
    """Published Arabic OCR artifacts and non-secret provenance."""

    output_dir: Path
    extractor: str
    pages: tuple[ArabicOcrPage, ...]
    provider_summary: list[dict]
    usage: OcrUsage
