"""Evidence-based, Gemma-only cleanup for printed Arabic OCR output."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from app.extraction.arabic_adapter import _has_large_repetition_loop
from app.extraction.arabic_types import ArabicOcrPage, GemmaOutputInvalid

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_ARABIC_LETTER = re.compile(r"\AARABIC LETTER\b")
_CONTEXT_CHARS = 16
_MIN_CONTEXT_LETTERS = 6
_MIN_SOURCE_ARABIC_LETTERS = 12
_MIN_ALIGNMENT_RATIO = 0.90


def repair_gemma_page(page: ArabicOcrPage, source_text: str) -> ArabicOcrPage:
    """Normalize only Gemma output; use PDF text only for unique exact spans."""

    if page.provider != "gemma4_arabic_fallback":
        return page

    cleaned = _normalize_safe_unicode(page.markdown)
    if _has_large_repetition_loop(cleaned):
        raise GemmaOutputInvalid(
            "Gemma OCR produced a repeated text loop; retry the page instead of deleting it."
        )

    if source_text:
        cleaned = _repair_from_source(cleaned, _normalize_safe_unicode(source_text))

    changed = cleaned != page.markdown
    if not changed and page.repaired:
        return page
    return replace(page, markdown=cleaned, repaired=page.repaired or changed)


def _normalize_safe_unicode(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    output: list[str] = []
    previous_zero_width = ""
    for character in normalized:
        if character in _ZERO_WIDTH:
            if character == previous_zero_width:
                continue
            previous_zero_width = character
            output.append(character)
            continue
        previous_zero_width = ""
        if unicodedata.category(character) == "Cc" and character not in "\n\r\t":
            continue
        output.append(character)
    return "".join(output)


def _repair_from_source(ocr_text: str, source_text: str) -> str:
    if _arabic_letter_count(source_text) < _MIN_SOURCE_ARABIC_LETTERS:
        return ocr_text

    matcher = SequenceMatcher(None, ocr_text, source_text, autojunk=False)
    if matcher.ratio() < _MIN_ALIGNMENT_RATIO:
        return ocr_text

    opcodes = matcher.get_opcodes()
    replacements: list[tuple[int, int, str]] = []
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            continue
        if index == 0 or index + 1 >= len(opcodes):
            continue
        previous = opcodes[index - 1]
        following = opcodes[index + 1]
        if previous[0] != "equal" or following[0] != "equal":
            continue

        left_anchor = ocr_text[max(previous[1], previous[2] - _CONTEXT_CHARS) : previous[2]]
        right_anchor = ocr_text[following[1] : min(following[2], following[1] + _CONTEXT_CHARS)]
        if (
            _letter_count(left_anchor) < _MIN_CONTEXT_LETTERS
            or _letter_count(right_anchor) < _MIN_CONTEXT_LETTERS
        ):
            continue

        source_candidate = source_text[j1:j2]
        full_context = left_anchor + source_candidate + right_anchor
        if source_text.count(full_context) != 1:
            continue
        replacements.append((i1, i2, source_candidate))

    repaired = ocr_text
    for start, end, replacement in reversed(replacements):
        repaired = repaired[:start] + replacement + repaired[end:]
    return repaired


def _arabic_letter_count(text: str) -> int:
    return sum(
        1
        for character in text
        if unicodedata.category(character).startswith("L")
        and _ARABIC_LETTER.match(unicodedata.name(character, ""))
    )


def _letter_count(text: str) -> int:
    return sum(unicodedata.category(character).startswith("L") for character in text)
