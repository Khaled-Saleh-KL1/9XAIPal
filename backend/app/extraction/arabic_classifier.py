"""Local-only Arabic document language and writing-style classification.

Pages with a substantial English text layer and no substantive Arabic text are
authoritatively English for language routing. Pages with Arabic body text still
receive local style classification; only pages with missing or sparse text
need visual language classification. Sparse-page Arabic votes require a
high-confidence detail confirmation. Any unresolved style disagreement
abstains instead of guessing handwritten.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

import fitz
import httpx

from app.core.config import settings
from app.extraction.arabic_types import (
    ClassificationDecision,
    DocumentRoute,
    PageStyleVote,
)

LANGUAGES = frozenset({"english", "arabic", "mixed", "unknown"})
WRITING_STYLES = frozenset({"printed", "handwritten", "mixed", "unknown"})
_SCREEN_DPI = 72
_DETAIL_DPI = 168
_ROUTER_TIMEOUT_SECONDS = 45.0
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_WORD_RE = re.compile(r"[^\W\d_]+", flags=re.UNICODE)


@dataclass(frozen=True)
class ClassificationImage:
    page_idx: int
    region: str
    image_bytes: bytes


@dataclass(frozen=True)
class TextPageEvidence:
    page_idx: int
    arabic_chars: int
    latin_chars: int

    @property
    def has_substantive_arabic_body(self) -> bool:
        letters = self.arabic_chars + self.latin_chars
        return (
            self.arabic_chars >= settings.arabic_min_body_char_count
            and letters > 0
            and self.arabic_chars / letters >= settings.arabic_min_body_letter_share
        )

    @property
    def has_substantive_latin_body(self) -> bool:
        return self.latin_chars >= settings.arabic_min_body_char_count

    @property
    def has_clear_english_body(self) -> bool:
        return self.has_substantive_latin_body and not self.has_substantive_arabic_body


@dataclass(frozen=True)
class DocumentTextEvidence:
    pages: tuple[TextPageEvidence, ...]

    @property
    def arabic_char_count(self) -> int:
        return sum(page.arabic_chars for page in self.pages)

    @property
    def latin_char_count(self) -> int:
        return sum(page.latin_chars for page in self.pages)

    @property
    def has_arabic_body(self) -> bool:
        return any(page.has_substantive_arabic_body for page in self.pages)

    @property
    def has_latin_body(self) -> bool:
        return any(page.has_substantive_latin_body for page in self.pages)

    @property
    def all_pages_are_clear_english(self) -> bool:
        return all(page.has_clear_english_body for page in self.pages)

    @property
    def is_english_without_vision(self) -> bool:
        """English by text layer alone, for when the local classifier is down.

        No page may carry substantive Arabic, and clear English pages must be
        the configured share of the document; the remainder (blank, figure, or
        scanned pages) cannot hide enough content to change the route.
        """
        if not self.pages or self.has_arabic_body:
            return False
        english_pages = sum(page.has_clear_english_body for page in self.pages)
        return (
            english_pages / len(self.pages)
            >= settings.arabic_classifier_outage_english_page_share
        )

    @property
    def language(self) -> str:
        if self.has_arabic_body and self.has_latin_body:
            return "mixed"
        if self.has_arabic_body:
            return "arabic"
        if self.has_latin_body:
            return "english"
        return "unknown"


VisionCall = Callable[[Sequence[ClassificationImage], str], Sequence[PageStyleVote]]


class ArabicClassifierInputError(ValueError):
    """The uploaded PDF cannot be inspected as a document."""


class ArabicClassifierUnavailable(RuntimeError):
    """The configured local classifier endpoint did not complete the request."""


class ArabicClassifierResponseInvalid(ValueError):
    """The local classifier returned data outside its strict response contract."""


def count_arabic_letters(text: str) -> int:
    """Count Arabic-script letters, excluding digits, marks, and punctuation."""
    text = _URL_RE.sub(" ", text)
    return sum(
        1
        for char in text
        if unicodedata.category(char).startswith("L")
        and unicodedata.name(char, "").startswith("ARABIC LETTER")
    )


def count_latin_letters(text: str) -> int:
    """Count letters in Latin words, excluding URLs and isolated formula symbols."""
    text = _URL_RE.sub(" ", text)
    count = 0
    for word in _WORD_RE.findall(text):
        letters = [
            char for char in word
            if unicodedata.category(char).startswith("L")
            and unicodedata.name(char, "").startswith("LATIN")
        ]
        if len(letters) >= 2 and len(letters) == len(word):
            count += len(letters)
    return count


def inspect_text_layers(pdf_path: Path) -> DocumentTextEvidence:
    """Inspect every PDF text layer using script letters, not total character count."""
    try:
        with fitz.open(pdf_path) as document:
            pages = tuple(
                TextPageEvidence(
                    page_idx=page_idx,
                    arabic_chars=count_arabic_letters(page.get_text("text")),
                    latin_chars=count_latin_letters(page.get_text("text")),
                )
                for page_idx, page in enumerate(document, start=1)
            )
    except Exception as exc:
        raise ArabicClassifierInputError("The document could not be read as a PDF") from exc
    if not pages:
        raise ArabicClassifierInputError("The PDF contains no pages")
    return DocumentTextEvidence(pages)


def _render_page(
    page: fitz.Page,
    page_idx: int,
    region: str,
    *,
    dpi: int,
    clip=None,
) -> ClassificationImage:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(dpi / 72, dpi / 72),
        clip=clip,
        colorspace=fitz.csRGB,
        alpha=False,
    )
    return ClassificationImage(page_idx, region, pixmap.tobytes("png"))


def render_page_images(
    pdf_path: Path,
    page_indices: Iterable[int],
    *,
    include_regions: bool = False,
) -> list[ClassificationImage]:
    """Render RGB thumbnails and, when requested, overlapping body strips."""
    indices = list(page_indices)
    rendered: list[ClassificationImage] = []
    try:
        with fitz.open(pdf_path) as document:
            for page_idx in indices:
                if page_idx < 1 or page_idx > document.page_count:
                    raise ArabicClassifierInputError("A requested PDF page is out of range")
                page = document.load_page(page_idx - 1)
                dpi = _DETAIL_DPI if include_regions else _SCREEN_DPI
                rendered.append(_render_page(page, page_idx, "page", dpi=dpi))
                if not include_regions:
                    continue
                bounds = page.rect
                for name, top, bottom in (
                    ("top", 0.05, 0.60),
                    ("middle", 0.22, 0.78),
                    ("bottom", 0.40, 0.95),
                ):
                    clip = fitz.Rect(
                        bounds.x0 + bounds.width * 0.03,
                        bounds.y0 + bounds.height * top,
                        bounds.x1 - bounds.width * 0.03,
                        bounds.y0 + bounds.height * bottom,
                    )
                    rendered.append(_render_page(page, page_idx, name, dpi=dpi, clip=clip))
    except ArabicClassifierInputError:
        raise
    except Exception as exc:
        raise ArabicClassifierInputError("The document pages could not be rendered") from exc
    return rendered


def render_all_page_thumbnails(pdf_path: Path) -> list[ClassificationImage]:
    evidence = inspect_text_layers(pdf_path)
    return render_page_images(pdf_path, (page.page_idx for page in evidence.pages))


def render_suspicious_pages(
    pdf_path: Path,
    screen_votes: Sequence[PageStyleVote],
    *,
    force_pages: Iterable[int] = (),
) -> list[ClassificationImage]:
    """Render a second-pass view for uncertain, mixed, or handwriting-like pages."""
    return render_page_images(
        pdf_path,
        _suspicious_page_indices(screen_votes, force_pages=force_pages),
        include_regions=True,
    )


def _suspicious_page_indices(
    screen_votes: Sequence[PageStyleVote],
    *,
    force_pages: Iterable[int] = (),
) -> list[int]:
    forced = set(force_pages)
    suspicious = set(forced)
    for vote in screen_votes:
        if vote.region != "page":
            continue
        if (
            vote.writing_style != "printed"
            or vote.confidence < settings.arabic_classifier_confidence_min
        ):
            suspicious.add(vote.page_idx)
    return sorted(suspicious)


def _request_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "votes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "page_idx": {"type": "integer", "minimum": 1},
                        "region": {"type": "string"},
                        "language": {"type": "string", "enum": sorted(LANGUAGES)},
                        "writing_style": {
                            "type": "string",
                            "enum": sorted(WRITING_STYLES),
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "primary_content": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": [
                        "page_idx", "region", "language", "writing_style",
                        "confidence", "primary_content", "evidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["votes"],
        "additionalProperties": False,
    }


def _parse_votes(content: object, images: Sequence[ClassificationImage]) -> list[PageStyleVote]:
    if not isinstance(content, str):
        raise ArabicClassifierResponseInvalid("Classifier response content must be JSON text")
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ArabicClassifierResponseInvalid("Classifier response is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"votes"}:
        raise ArabicClassifierResponseInvalid("Classifier response must contain only a votes array")
    raw_votes = payload["votes"]
    if not isinstance(raw_votes, list):
        raise ArabicClassifierResponseInvalid("Classifier votes must be an array")

    expected = {(image.page_idx, image.region) for image in images}
    parsed: list[PageStyleVote] = []
    seen: set[tuple[int, str]] = set()
    required_fields = {
        "page_idx", "region", "language", "writing_style", "confidence",
        "primary_content", "evidence",
    }
    for item in raw_votes:
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ArabicClassifierResponseInvalid("A classifier vote has an invalid shape")
        page_idx = item["page_idx"]
        region = item["region"]
        language = item["language"]
        style = item["writing_style"]
        confidence = item["confidence"]
        primary = item["primary_content"]
        evidence = item["evidence"]
        if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 1:
            raise ArabicClassifierResponseInvalid("Classifier page indices must be positive integers")
        if not isinstance(region, str) or not region:
            raise ArabicClassifierResponseInvalid("Classifier region labels must be non-empty text")
        if (
            not isinstance(language, str)
            or not isinstance(style, str)
            or language not in LANGUAGES
            or style not in WRITING_STYLES
        ):
            raise ArabicClassifierResponseInvalid("Classifier returned an unknown category")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ArabicClassifierResponseInvalid("Classifier confidence must be a number from 0 to 1")
        if not isinstance(primary, bool) or not isinstance(evidence, str):
            raise ArabicClassifierResponseInvalid("Classifier vote fields have invalid types")
        key = (page_idx, region)
        if key not in expected or key in seen:
            raise ArabicClassifierResponseInvalid("Classifier returned an unexpected or duplicate page region")
        seen.add(key)
        parsed.append(PageStyleVote(
            page_idx=page_idx,
            language=language,
            writing_style=style,
            confidence=float(confidence),
            evidence=evidence[:500],
            region=region,
            primary_content=primary,
        ))
    if seen != expected:
        raise ArabicClassifierResponseInvalid("Classifier omitted one or more requested page regions")
    return parsed


def call_local_router(
    images: Sequence[ClassificationImage],
    phase: str,
) -> list[PageStyleVote]:
    """Call only the configured local Ollama endpoint; never try a cloud provider."""
    if not images:
        return []
    if phase not in {"page_screen", "detail"}:
        raise ValueError("Unknown classifier phase")
    parsed_endpoint = urlsplit(settings.arabic_router_base_url)
    hostname = (parsed_endpoint.hostname or "").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
        is_local = address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        is_local = hostname in {"localhost", "host.docker.internal"}
    if (
        parsed_endpoint.scheme not in {"http", "https"}
        or not hostname
        or not is_local
        or parsed_endpoint.username
        or parsed_endpoint.password
        or parsed_endpoint.query
        or parsed_endpoint.fragment
    ):
        raise ArabicClassifierUnavailable("Arabic classification is restricted to a local Ollama endpoint")
    labels = ", ".join(f"page {image.page_idx} region {image.region}" for image in images)
    detail = (
        "For detail images, mark a crop primary_content=true only when it contains the main body text. "
        "Isolated signatures, marginal notes, stamps, headers, and blank areas are not primary body text."
        if phase == "detail"
        else "Classify the dominant main text body, not an isolated signature or marginal annotation."
    )
    prompt = (
        "Classify the visible language and dominant writing style of each supplied PDF image. "
        "Do not transcribe the document. Use only language=english|arabic|mixed|unknown and "
        "writing_style=printed|handwritten|mixed|unknown. If evidence is ambiguous, use unknown. "
        f"{detail} The images are in this order: {labels}. "
        "Return exactly one vote per image, keyed by its page_idx and region, as JSON. "
        "Include confidence from 0 to 1, primary_content as a boolean, and a short visual evidence note."
    )
    body = {
        "model": settings.arabic_router_model,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [base64.b64encode(image.image_bytes).decode("ascii") for image in images],
        }],
        "format": _request_schema(),
        "stream": False,
        "options": {"temperature": 0, "num_predict": 4096},
    }
    endpoint = f"{settings.arabic_router_base_url.rstrip('/')}/api/chat"
    try:
        response = httpx.post(
            endpoint,
            json=body,
            timeout=_ROUTER_TIMEOUT_SECONDS,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ArabicClassifierUnavailable("The local Arabic classifier is unavailable") from exc
    try:
        response_body = response.json()
        content = response_body["message"]["content"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ArabicClassifierResponseInvalid("Classifier response is missing message content") from exc
    return _parse_votes(content, images)


def _validate_call_result(
    images: Sequence[ClassificationImage],
    votes: Sequence[PageStyleVote],
) -> list[PageStyleVote]:
    expected = {(image.page_idx, image.region) for image in images}
    actual = [(vote.page_idx, vote.region) for vote in votes]
    if any(not isinstance(vote, PageStyleVote) for vote in votes):
        raise ArabicClassifierResponseInvalid("Injected classifier must return PageStyleVote values")
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ArabicClassifierResponseInvalid("Classifier omitted, duplicated, or added a page region")
    if any(
        not isinstance(vote.language, str)
        or not isinstance(vote.writing_style, str)
        or vote.language not in LANGUAGES
        or vote.writing_style not in WRITING_STYLES
        or not isinstance(vote.confidence, (int, float))
        or isinstance(vote.confidence, bool)
        or not 0 <= vote.confidence <= 1
        for vote in votes
    ):
        raise ArabicClassifierResponseInvalid("Classifier returned an invalid vote")
    return list(votes)


def call_in_batches(
    images: Sequence[ClassificationImage],
    batch_size: int,
    call: VisionCall,
    phase: str,
) -> list[PageStyleVote]:
    """Call the classifier in bounded batches without splitting detail page groups."""
    if batch_size < 1:
        raise ValueError("Classifier batch size must be positive")
    groups: list[list[ClassificationImage]] = []
    if phase == "detail":
        by_page: dict[int, list[ClassificationImage]] = {}
        for image in images:
            by_page.setdefault(image.page_idx, []).append(image)
        page_ids = list(by_page)
        for start in range(0, len(page_ids), batch_size):
            groups.append([
                image
                for page_idx in page_ids[start:start + batch_size]
                for image in by_page[page_idx]
            ])
    else:
        groups = [list(images[start:start + batch_size]) for start in range(0, len(images), batch_size)]
    result: list[PageStyleVote] = []
    for group in groups:
        if group:
            result.extend(_validate_call_result(group, call(group, phase)))
    return result


def _derive_language(
    evidence: DocumentTextEvidence,
    confirmed_visual_languages: dict[int, str],
) -> str:
    page_languages: list[str] = []
    for page in evidence.pages:
        if page.has_substantive_arabic_body:
            page_languages.append(
                "mixed" if page.has_substantive_latin_body else "arabic"
            )
        elif page.has_clear_english_body:
            page_languages.append("english")
        elif page.page_idx in confirmed_visual_languages:
            page_languages.append(confirmed_visual_languages[page.page_idx])

    has_arabic = any(language in {"arabic", "mixed"} for language in page_languages)
    has_latin = any(language in {"english", "mixed"} for language in page_languages)
    if has_arabic and has_latin:
        return "mixed"
    if has_arabic:
        return "arabic"
    if has_latin:
        return "english"
    return "unknown"


def _confirmed_visual_languages(
    evidence: DocumentTextEvidence,
    screen_votes: Sequence[PageStyleVote],
    detail_votes: Sequence[PageStyleVote],
) -> dict[int, str]:
    """Use visual language votes only for pages without authoritative text."""
    screen_by_page = {
        vote.page_idx: vote
        for vote in screen_votes
        if vote.region == "page"
    }
    detail_by_page: dict[int, list[PageStyleVote]] = {}
    for vote in detail_votes:
        detail_by_page.setdefault(vote.page_idx, []).append(vote)

    confirmed: dict[int, str] = {}
    min_confidence = settings.arabic_classifier_confidence_min
    for page in evidence.pages:
        if page.has_clear_english_body or page.has_substantive_arabic_body:
            continue
        screen = screen_by_page.get(page.page_idx)
        if screen is None or screen.confidence < min_confidence:
            continue
        details = detail_by_page.get(page.page_idx, [])
        detail_page = next((vote for vote in details if vote.region == "page"), None)
        primary_regions = [
            vote for vote in details
            if vote.region != "page" and vote.primary_content
        ]
        if (
            detail_page is None
            or not detail_page.primary_content
            or detail_page.confidence < min_confidence
        ):
            continue

        if screen.language in {"arabic", "mixed"}:
            matching_regions = [
                vote for vote in primary_regions
                if vote.language in {"arabic", "mixed"}
                and vote.confidence >= min_confidence
            ]
            if (
                detail_page.language not in {"arabic", "mixed"}
                or not matching_regions
            ):
                continue
            confirmed[page.page_idx] = (
                "mixed"
                if detail_page.language == "mixed"
                or any(vote.language == "mixed" for vote in matching_regions)
                else "arabic"
            )
        elif screen.language in {"english", "mixed"}:
            matching_regions = [
                vote for vote in primary_regions
                if vote.language in {"english", "mixed"}
                and vote.confidence >= min_confidence
            ]
            if (
                detail_page.language not in {"english", "mixed"}
                or not matching_regions
            ):
                continue
            confirmed[page.page_idx] = (
                "mixed"
                if detail_page.language == "mixed"
                or any(vote.language == "mixed" for vote in matching_regions)
                else "english"
            )
    return confirmed


def aggregate_votes(
    evidence: DocumentTextEvidence,
    screen_votes: Sequence[PageStyleVote],
    detail_votes: Sequence[PageStyleVote],
    *,
    style_page_indices: Iterable[int] | None = None,
) -> ClassificationDecision:
    all_votes = tuple([*screen_votes, *detail_votes])
    confirmed_languages = _confirmed_visual_languages(
        evidence, screen_votes, detail_votes
    )
    language = _derive_language(evidence, confirmed_languages)
    page_ids = {page.page_idx for page in evidence.pages}
    style_page_ids = (
        page_ids if style_page_indices is None else set(style_page_indices)
    )
    screen_by_page = {vote.page_idx: vote for vote in screen_votes if vote.region == "page"}
    detail_by_page: dict[int, list[PageStyleVote]] = {}
    for vote in detail_votes:
        detail_by_page.setdefault(vote.page_idx, []).append(vote)

    resolved_styles: list[str] = []
    confidence_votes: list[PageStyleVote] = []
    handwritten_consensus = True
    uncertain = False
    for page_idx in sorted(style_page_ids):
        screen = screen_by_page.get(page_idx)
        if screen is None:
            uncertain = True
            handwritten_consensus = False
            continue
        details = detail_by_page.get(page_idx, [])
        detail_page = next((vote for vote in details if vote.region == "page"), None)
        primary_regions = [
            vote for vote in details
            if vote.region != "page" and vote.primary_content
        ]
        if details and (detail_page is None or not primary_regions):
            uncertain = True

        # A strong, direct disagreement between printed and handwritten full-page
        # votes is never settled by choosing whichever answer is more convenient.
        if (
            detail_page is not None
            and screen.confidence >= settings.arabic_classifier_confidence_min
            and screen.writing_style in {"printed", "handwritten"}
            and detail_page.writing_style in {"printed", "handwritten"}
            and screen.writing_style != detail_page.writing_style
        ):
            uncertain = True

        page_vote = detail_page or screen
        confidence_votes.append(page_vote)
        style = page_vote.writing_style
        if style not in {"printed", "handwritten"}:
            uncertain = True
        resolved_styles.append(style)

        region_styles = {vote.writing_style for vote in primary_regions}
        if len(region_styles) > 1:
            uncertain = True
        if region_styles and style not in region_styles:
            uncertain = True
        confidence_votes.extend(primary_regions)

        # The handwritten route requires all three independent views: screening,
        # the full-page detail vote, and every main-content region vote.
        if not (
            screen.writing_style == "handwritten"
            and screen.confidence >= settings.arabic_handwritten_confidence_min
            and detail_page is not None
            and detail_page.writing_style == "handwritten"
            and detail_page.confidence >= settings.arabic_handwritten_confidence_min
            and primary_regions
            and all(
                vote.writing_style == "handwritten"
                and vote.confidence >= settings.arabic_handwritten_confidence_min
                for vote in primary_regions
            )
        ):
            handwritten_consensus = False

    distinct_styles = set(resolved_styles)
    if len(distinct_styles) > 1:
        uncertain = True
    style = (
        next(iter(distinct_styles))
        if len(distinct_styles) == 1
        else "mixed" if distinct_styles else "unknown"
    )
    if style == "printed" and any(
        vote.confidence < settings.arabic_classifier_confidence_min
        for vote in confidence_votes
    ):
        uncertain = True
    if style == "handwritten" and not handwritten_consensus:
        uncertain = True

    if language == "english":
        route = DocumentRoute.ENGLISH
        direction = "ltr"
    elif language in {"arabic", "mixed"} and style == "printed" and not uncertain:
        route = DocumentRoute.ARABIC_PRINTED
        direction = "rtl"
    elif language in {"arabic", "mixed"} and style == "handwritten" and not uncertain:
        route = DocumentRoute.ARABIC_HANDWRITTEN
        direction = "rtl"
    else:
        route = DocumentRoute.ARABIC_STYLE_UNCERTAIN
        direction = "rtl" if language in {"arabic", "mixed"} else "auto"

    primary_confidences = [vote.confidence for vote in all_votes if vote.primary_content]
    if route == DocumentRoute.ARABIC_STYLE_UNCERTAIN and all_votes:
        confidence = min(primary_confidences) if primary_confidences else 0.0
    elif confidence_votes:
        confidence = min(vote.confidence for vote in confidence_votes)
    elif all_votes:
        confidence = min(vote.confidence for vote in all_votes)
    else:
        confidence = 0.0
    return ClassificationDecision(
        route=route,
        language=language,
        writing_style=style,
        text_direction=direction,
        confidence=confidence,
        classifier_model=settings.arabic_router_model,
        votes=all_votes,
    )


def classify_document(
    pdf_path: Path,
    vision_call: VisionCall | None = None,
) -> ClassificationDecision:
    """Classify every document page, abstaining on unresolved Arabic style."""
    evidence = inspect_text_layers(pdf_path)
    if evidence.all_pages_are_clear_english:
        return ClassificationDecision(
            route=DocumentRoute.ENGLISH,
            language="english",
            writing_style="unknown",
            text_direction="ltr",
            confidence=1.0,
            classifier_model="text_layer",
        )

    call = vision_call or call_local_router
    page_indices = [
        page.page_idx
        for page in evidence.pages
        if not page.has_clear_english_body
    ]
    screen_votes: list[PageStyleVote] = []
    for start in range(0, len(page_indices), settings.arabic_classifier_batch_pages):
        batch_pages = page_indices[start:start + settings.arabic_classifier_batch_pages]
        images = render_page_images(pdf_path, batch_pages)
        screen_votes.extend(call_in_batches(
            images, settings.arabic_classifier_batch_pages, call, "page_screen"
        ))

    arabic_text_pages = [
        page.page_idx for page in evidence.pages
        if page.has_substantive_arabic_body
    ]
    sparse_arabic_pages = [
        vote.page_idx
        for vote in screen_votes
        if vote.region == "page"
        and vote.language in {"arabic", "mixed"}
        and vote.page_idx not in arabic_text_pages
    ]
    force_detail_pages = [*arabic_text_pages, *sparse_arabic_pages]
    if len(evidence.pages) == 1 and page_indices:
        force_detail_pages.append(page_indices[0])
    detail_votes: list[PageStyleVote] = []
    for page_idx in _suspicious_page_indices(
        screen_votes, force_pages=force_detail_pages
    ):
        detail_images = render_page_images(
            pdf_path, [page_idx], include_regions=True
        )
        detail_votes.extend(call_in_batches(detail_images, 1, call, "detail"))
    confirmed_languages = _confirmed_visual_languages(
        evidence, screen_votes, detail_votes
    )
    document_language = _derive_language(evidence, confirmed_languages)
    screen_by_page = {vote.page_idx: vote for vote in screen_votes if vote.region == "page"}
    style_pages = {
        page_idx for page_idx in arabic_text_pages
        if page_idx in screen_by_page and screen_by_page[page_idx].primary_content
    } | {
        page_idx
        for page_idx, language in confirmed_languages.items()
        if language in {"arabic", "mixed"}
    }
    if document_language in {"arabic", "mixed"}:
        style_pages.update(
            vote.page_idx
            for vote in screen_votes
            if vote.region == "page"
            and vote.primary_content
            and vote.language in {"arabic", "mixed"}
            and not next(
                page for page in evidence.pages if page.page_idx == vote.page_idx
            ).has_clear_english_body
        )
    return aggregate_votes(
        evidence,
        screen_votes,
        detail_votes,
        style_page_indices=style_pages,
    )
