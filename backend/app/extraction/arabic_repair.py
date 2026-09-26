"""Evidence-based, Gemma-only cleanup for printed Arabic OCR output."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher

from markdown_it import MarkdownIt

from app.extraction.arabic_adapter import _has_large_repetition_loop
from app.extraction.arabic_types import ArabicOcrPage, GemmaOutputInvalid

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_ARABIC_LETTER = re.compile(r"\AARABIC LETTER\b")
_CONTEXT_CHARS = 16
_MIN_CONTEXT_LETTERS = 6
_MIN_SOURCE_ARABIC_LETTERS = 12
_MIN_ALIGNMENT_RATIO = 0.90
_INLINE_MARKERS = re.compile(r"(\*\*|__|~~|\*|_)")
_STRUCTURED_INLINE = re.compile(r"`|\$\$|\\|!\[|\]\(|<|\|")
_LIST_OR_QUOTE = re.compile(r"^\s*(?:[-+*]\s|\d+[.)]\s|>\s)")
_MARKDOWN = MarkdownIt("gfm-like", {"html": True, "linkify": False})


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
    normalized = "".join(
        unicodedata.normalize("NFKC", character)
        if (
            0xFB50 <= ord(character) <= 0xFDFF
            or 0xFE70 <= ord(character) <= 0xFEFF
        ) and not (0xFDF0 <= ord(character) <= 0xFDFD)
        else character
        for character in text
    )
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
    """Align only text in standalone prose paragraphs, preserving all syntax."""
    if _arabic_letter_count(source_text) < _MIN_SOURCE_ARABIC_LETTERS:
        return ocr_text

    source_lines = ocr_text.splitlines(keepends=True)
    offsets = [0]
    for line in source_lines:
        offsets.append(offsets[-1] + len(line))

    replacements: list[tuple[int, int, str]] = []
    structural_depth = 0
    for token in _MARKDOWN.parse(ocr_text):
        if token.type in {"bullet_list_open", "ordered_list_open", "blockquote_open", "table_open"}:
            structural_depth += 1
        if token.type in {"bullet_list_close", "ordered_list_close", "blockquote_close", "table_close"}:
            structural_depth -= 1
        if token.type != "paragraph_open" or token.map is None or structural_depth:
            continue
        first_line, last_line = token.map
        start, end = offsets[first_line], offsets[last_line]
        raw = ocr_text[start:end]
        body = raw.rstrip("\r\n")
        if not body or _LIST_OR_QUOTE.match(body) or _STRUCTURED_INLINE.search(body):
            continue
        parts = _INLINE_MARKERS.split(body)
        repaired_parts: list[str] = []
        for part in parts:
            if _INLINE_MARKERS.fullmatch(part):
                repaired_parts.append(part)
            else:
                repaired_parts.append(_repair_prose_segment(part, source_text))
        repaired_body = "".join(repaired_parts)
        if repaired_body != body:
            replacements.append((start, start + len(body), repaired_body))

    repaired = ocr_text
    for start, end, replacement in reversed(replacements):
        repaired = repaired[:start] + replacement + repaired[end:]
    return repaired


def _repair_prose_segment(ocr_text: str, source_text: str) -> str:
    if len(ocr_text) < 2 * _CONTEXT_CHARS:
        return ocr_text
    left = ocr_text[:_CONTEXT_CHARS]
    right = ocr_text[-_CONTEXT_CHARS:]
    if source_text.count(left) != 1 or source_text.count(right) != 1:
        return ocr_text
    source_start = source_text.find(left)
    source_end = source_text.find(right, source_start + len(left))
    if source_end < 0:
        return ocr_text
    source_slice = source_text[source_start : source_end + len(right)]
    return _align_prose_to_source(ocr_text, source_slice)


def _align_prose_to_source(ocr_text: str, source_text: str) -> str:
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
