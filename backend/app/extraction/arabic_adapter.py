"""Strict page-boundary parsing and Markdown-to-artifact conversion for Arabic OCR."""

from __future__ import annotations

import re
from collections.abc import Sequence
from html import unescape
from typing import Any

from markdown_it import MarkdownIt

from app.extraction.arabic_types import ArabicOcrPage, ParsedPagePrefix

_MARKER_CANDIDATE_RE = re.compile(
    r"<!--\s*(?:END_)?PAGE\b.*?-->", re.IGNORECASE | re.DOTALL
)
_TABLE_RE = re.compile(r"<table\b[\s\S]*?</table\s*>", re.IGNORECASE)
_CAPTION_RE = re.compile(r"<caption\b[^>]*>([\s\S]*?)</caption\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# One display-math block. The body may not contain its own closing delimiter,
# so "$$a$$ prose $$b$$" is two formulas around prose, not one formula.
_DISPLAY_MATH_RE = re.compile(
    r"\$\$(?:(?!\$\$)[\s\S])+\$\$|\\\[(?:(?!\\\])[\s\S])+\\\]|"
    r"\\begin\{(?:equation\*?|align\*?|gather\*?|displaymath)\}"
    r"(?:(?!\\(?:begin|end)\{(?:equation\*?|align\*?|gather\*?|displaymath)\})[\s\S])+"
    r"\\end\{(?:equation\*?|align\*?|gather\*?|displaymath)\}",
)
_EQUATION_ENV_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|displaymath)\}"
    r"([\s\S]+?)\\end\{\1\}\Z"
)
_MATH_FENCE_LANGUAGES = {"math", "latex", "tex", "equation", "display-math"}
_MARKDOWN = MarkdownIt("gfm-like", {"html": True, "linkify": False})


def parse_complete_page_prefix(
    raw_response: str,
    expected_pages: Sequence[int],
    provider: str,
    model: str,
) -> ParsedPagePrefix:
    """Commit only a strict, non-empty, non-repeating contiguous page prefix."""

    expected = tuple(expected_pages)
    if not expected or any(
        isinstance(number, bool) or not isinstance(number, int) or number < 1
        for number in expected
    ):
        raise ValueError("expected_pages must contain positive 1-based integers")
    if tuple(sorted(set(expected))) != expected:
        raise ValueError("expected_pages must be unique and strictly increasing")

    text = raw_response if isinstance(raw_response, str) else ""
    pages: list[ArabicOcrPage] = []
    cursor = 0
    first_uncommitted: int | None = None

    for expected_number in expected:
        start_at = _skip_whitespace(text, cursor)
        start_marker = _MARKER_CANDIDATE_RE.match(text, start_at)
        expected_start = f"<!-- PAGE:{expected_number} -->"
        if start_marker is None or start_marker.group(0) != expected_start:
            first_uncommitted = expected_number
            break

        end_marker = _MARKER_CANDIDATE_RE.search(text, start_marker.end())
        expected_end = f"<!-- END_PAGE:{expected_number} -->"
        if end_marker is None or end_marker.group(0) != expected_end:
            first_uncommitted = expected_number
            break

        page_markdown = text[start_marker.end() : end_marker.start()].strip()
        if not page_markdown or _has_large_repetition_loop(page_markdown):
            first_uncommitted = expected_number
            break

        pages.append(
            ArabicOcrPage(
                page_number=expected_number,
                raw_markdown=page_markdown,
                markdown=page_markdown,
                provider=provider,
                model=model,
            )
        )
        cursor = end_marker.end()

    if first_uncommitted is None:
        trailing = text[cursor:].strip()
        if trailing:
            # Text, malformed markers, or a repeated page after the requested
            # sequence make the final boundary ambiguous; do not commit that
            # last page as complete.
            first_uncommitted = pages[-1].page_number
            pages.pop()

    return ParsedPagePrefix(
        pages=tuple(pages),
        first_uncommitted_page=first_uncommitted,
        is_complete=first_uncommitted is None and len(pages) == len(expected),
    )


def pages_to_content_list(pages: Sequence[ArabicOcrPage]) -> list[dict[str, Any]]:
    """Convert committed page Markdown into ordered MinerU-compatible blocks."""

    content_list: list[dict[str, Any]] = []
    for page in pages:
        if page.page_number < 1:
            raise ValueError("Arabic OCR page numbers must be 1-based")
        for block in _markdown_blocks(page.markdown):
            if not _has_block_content(block):
                continue
            content_list.append(
                {
                    **block,
                    "page_idx": page.page_number - 1,
                    "ocr_provider": page.provider,
                    "ocr_model": page.model,
                    "ocr_repaired": page.repaired,
                }
            )
    return content_list


def _markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    if not markdown.strip():
        return []

    tokens = _MARKDOWN.parse(markdown)
    source_lines = markdown.splitlines()
    result: list[dict[str, Any]] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.type == "heading_open":
            inline = _next_inline(tokens, index + 1)
            if inline is not None:
                level = int(token.tag[1:])
                result.append(
                    {"type": "text", "text_level": level, "text": inline.content.strip()}
                )
                index += 3
                continue

        if token.type == "paragraph_open":
            inline = _next_inline(tokens, index + 1)
            if inline is not None:
                content = inline.content.strip()
                if content:
                    result.extend(_paragraph_blocks(content))
                index += 3
                continue

        if token.type in {"bullet_list_open", "ordered_list_open"}:
            closing = _matching_close(tokens, index, token.type)
            if closing is not None:
                items = _list_items(tokens[index + 1 : closing])
                if items:
                    result.append({"type": "list", "list_items": items})
                index = closing + 1
                continue

        if token.type == "table_open":
            closing = _matching_close(tokens, index, "table_open")
            if closing is not None:
                table_body = _MARKDOWN.renderer.render(
                    tokens[index : closing + 1], _MARKDOWN.options, {}
                ).strip()
                if table_body:
                    result.append({"type": "table", "table_body": table_body})
                index = closing + 1
                continue

        if token.type == "html_block":
            result.extend(_html_blocks(token.content))
            index += 1
            continue

        if token.type == "fence":
            content = token.content.strip()
            language = token.info.strip().split(maxsplit=1)[0].lower() if token.info.strip() else ""
            normalized_math = _canonical_display_math(content)
            if content and (language in _MATH_FENCE_LANGUAGES or normalized_math):
                result.append({"type": "equation", "text": normalized_math or content})
            else:
                raw = _source_for_token(token.map, source_lines)
                if raw:
                    result.append({"type": "text", "text": raw})
            index += 1
            continue

        if token.type == "code_block":
            raw = _source_for_token(token.map, source_lines)
            if raw:
                result.append({"type": "text", "text": raw})
            index += 1
            continue

        if token.type == "inline" and token.content.strip():
            result.extend(_paragraph_blocks(token.content.strip()))
            index += 1
            continue

        if token.type in {"hr", "html_inline"}:
            raw = _source_for_token(token.map, source_lines) or token.content.strip()
            if raw:
                result.append({"type": "text", "text": raw})
        index += 1

    return result


def _paragraph_blocks(content: str) -> list[dict[str, Any]]:
    normalized_math = _canonical_display_math(content)
    if normalized_math is not None:
        return [{"type": "equation", "text": normalized_math}]
    return [{"type": "text", "text": content}] if content else []


def _canonical_display_math(content: str) -> str | None:
    text = content.strip()
    if not _DISPLAY_MATH_RE.fullmatch(text):
        return None
    if text.startswith("$$"):
        return text
    if text.startswith(r"\["):
        body = text[2:-2].strip()
    else:
        match = _EQUATION_ENV_RE.fullmatch(text)
        if match is None:
            return None
        body = match.group(2).strip()
    return f"$$\n{body}\n$$"


def _html_blocks(content: str) -> list[dict[str, Any]]:
    tables = list(_TABLE_RE.finditer(content))
    if not tables:
        text = content.strip()
        return [{"type": "text", "text": text}] if text else []

    blocks: list[dict[str, Any]] = []
    cursor = 0
    for match in tables:
        before = unescape(content[cursor : match.start()].strip())
        if before:
            blocks.append({"type": "text", "text": before})
        table_html = match.group(0).strip()
        captions = _CAPTION_RE.findall(table_html)
        caption_texts = [
            text
            for caption in captions
            if (text := unescape(_HTML_TAG_RE.sub("", caption)).strip())
        ]
        table_body = _CAPTION_RE.sub("", table_html).strip()
        blocks.append({
            "type": "table",
            "table_body": table_body,
            "table_caption": caption_texts,
        })
        cursor = match.end()
    after = unescape(content[cursor:].strip())
    if after:
        blocks.append({"type": "text", "text": after})
    return blocks


def _list_items(tokens: Sequence[Any]) -> list[str]:
    items: list[str] = []
    for token in tokens:
        if token.type == "inline" and token.content.strip():
            # Flatten nested list items instead of losing a parent's text when
            # a nested list opens inside it. The order remains the source order.
            items.append(token.content.strip())
    return items


def _matching_close(tokens: Sequence[Any], start: int, open_type: str) -> int | None:
    close_type = open_type.replace("_open", "_close")
    nesting = 0
    for index in range(start, len(tokens)):
        token_type = tokens[index].type
        if token_type == open_type:
            nesting += 1
        elif token_type == close_type:
            nesting -= 1
            if nesting == 0:
                return index
    return None


def _next_inline(tokens: Sequence[Any], start: int) -> Any | None:
    if start < len(tokens) and tokens[start].type == "inline":
        return tokens[start]
    return None


def _source_for_token(token_map: Sequence[int] | None, lines: Sequence[str]) -> str:
    if not token_map or len(token_map) != 2:
        return ""
    return "\n".join(lines[token_map[0] : token_map[1]]).strip()


def _has_block_content(block: dict[str, Any]) -> bool:
    if block["type"] == "table":
        return bool(str(block.get("table_body", "")).strip())
    if block["type"] == "list":
        return bool(block.get("list_items"))
    return bool(str(block.get("text", "")).strip())


def _skip_whitespace(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def _has_large_repetition_loop(markdown: str) -> bool:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    max_unit_lines = min(4, len(lines) // 3)
    for unit_size in range(1, max_unit_lines + 1):
        for start in range(len(lines) - unit_size * 3 + 1):
            unit = lines[start : start + unit_size]
            if any(line.startswith("|") for line in unit):
                continue
            if sum(len(line) for line in unit) < 36:
                continue
            if lines[start : start + unit_size * 3] == unit * 3:
                return True
    return False
