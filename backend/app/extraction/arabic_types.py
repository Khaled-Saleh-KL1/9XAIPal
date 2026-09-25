"""Shared domain types for the isolated Arabic document pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class DocumentRoute(str, Enum):
    ENGLISH = "english"
    ARABIC_PRINTED = "arabic_printed"
    ARABIC_HANDWRITTEN = "arabic_handwritten"
    ARABIC_STYLE_UNCERTAIN = "arabic_style_uncertain"


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
    ) -> None:
        self.final_kind = final_kind
        self.best_partial = best_partial
        self.attempt_usage = tuple(attempt_usage)
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
