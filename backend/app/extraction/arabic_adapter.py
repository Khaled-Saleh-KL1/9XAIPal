"""Strict page-boundary parsing and Markdown-to-artifact conversion for Arabic OCR."""

from __future__ import annotations

import re
from collections.abc import Sequence
from html import unescape
from pathlib import Path
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
_FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:الشكل|شكل|Figure|Fig\.?)\s*[\d٠-٩]+[^\n]*$",
    re.IGNORECASE,
)
_MARKDOWN = MarkdownIt("gfm-like", {"html": True, "linkify": False})


NO_TEXT_MARKER = "[NO_TEXT]"


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
        # ⚠ A cover picture, blank page or full-page image has no text. The
        # prompts ask for an explicit NO_TEXT_MARKER there; without it every
        # provider returned an empty page 1 for a real book (2026-09-30), the
        # page was refused, and the whole document failed. A page that comes
        # back silently empty is still refused.
        if page_markdown.upper() == NO_TEXT_MARKER:
            pages.append(
                ArabicOcrPage(
                    page_number=expected_number,
                    raw_markdown=page_markdown,
                    markdown="",
                    provider=provider,
                    model=model,
                )
            )
            cursor = end_marker.end()
            continue
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


def pages_to_content_list(
    pages: Sequence[ArabicOcrPage],
    *,
    pdf_path: Path | None = None,
    output_dir: Path | None = None,
    dpi: int = 200,
) -> list[dict[str, Any]]:
    """Convert committed page Markdown into ordered MinerU-compatible blocks."""

    if (pdf_path is None) != (output_dir is None):
        raise ValueError("pdf_path and output_dir must be provided together")
    figures_by_page = (
        _extract_embedded_pdf_figures(pages, pdf_path, output_dir, dpi)
        if pdf_path is not None and output_dir is not None
        else {}
    )

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
        for figure in figures_by_page.get(page.page_number, []):
            content_list.append(
                {
                    **figure,
                    "page_idx": page.page_number - 1,
                    "ocr_provider": page.provider,
                    "ocr_model": page.model,
                    "ocr_repaired": page.repaired,
                }
            )
    return content_list


def _extract_embedded_pdf_figures(
    pages: Sequence[ArabicOcrPage],
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
) -> dict[int, list[dict[str, Any]]]:
    """Render substantial embedded page images as MinerU-compatible figures."""
    if dpi < 1:
        raise ValueError("dpi must be positive")
    if not pages:
        return {}

    import fitz

    image_dir = Path(output_dir) / "images"
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    figures_by_page: dict[int, list[dict[str, Any]]] = {}
    with fitz.open(str(pdf_path)) as document:
        for ocr_page in pages:
            page_index = ocr_page.page_number - 1
            if page_index < 0 or page_index >= document.page_count:
                continue
            page = document[page_index]
            page_rect = page.rect
            page_area = float(page_rect.width * page_rect.height)
            if page_area <= 0:
                continue

            candidates = []
            seen_xrefs: set[int] = set()
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                for image_rect in page.get_image_rects(xref):
                    # get_image_rects reports unrotated page coordinates;
                    # page.rect and get_pixmap clips use displayed coordinates.
                    rect = (fitz.Rect(image_rect) * page.rotation_matrix) & page_rect
                    area = float(rect.width * rect.height)
                    area_share = area / page_area
                    if area_share < 0.05:
                        continue
                    # A full-page scan is page content, not a separate figure.
                    # Scanned documents with only these images are out of scope.
                    if area_share > 0.90:
                        continue
                    if (
                        rect.width * dpi / 72.0 < 80
                        or rect.height * dpi / 72.0 < 80
                    ):
                        continue
                    pixmap = page.get_pixmap(matrix=matrix, clip=rect)
                    if pixmap.width < 80 or pixmap.height < 80:
                        continue
                    candidates.append((rect, pixmap))

            # Arabic OCR pages read top-to-bottom, then right-to-left.
            candidates.sort(
                key=lambda candidate: (candidate[0].y0, -candidate[0].x1)
            )
            if not candidates:
                continue

            captions = [
                line.strip()
                for line in ocr_page.markdown.splitlines()
                if _FIGURE_CAPTION_RE.match(line)
            ]
            page_figures = []
            image_dir.mkdir(parents=True, exist_ok=True)
            for image_number, (rect, pixmap) in enumerate(candidates, start=1):
                filename = (
                    f"arabic-page-{ocr_page.page_number:04d}-"
                    f"figure-{image_number:03d}.png"
                )
                pixmap.save(str(image_dir / filename))
                page_figures.append(
                    {
                        "type": "image",
                        "img_path": f"images/{filename}",
                        "bbox": [
                            float(rect.x0),
                            float(rect.y0),
                            float(rect.x1),
                            float(rect.y1),
                        ],
                        "img_caption": (
                            [captions[image_number - 1]]
                            if image_number <= len(captions)
                            else []
                        ),
                    }
                )
            figures_by_page[ocr_page.page_number] = page_figures

    return figures_by_page


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
                items = _list_items(tokens[index + 1 : closing], token)
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


def _list_items(tokens: Sequence[Any], root_list: Any) -> list[str]:
    items: list[str] = []
    list_stack = [_list_frame(root_list)]
    item_prefix: str | None = None
    for token in tokens:
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            parent = list_stack[-1]
            list_stack.append(
                _list_frame(
                    token,
                    base_indent=parent["base_indent"] + parent["item_marker_width"],
                )
            )
            continue
        if token.type in {"bullet_list_close", "ordered_list_close"}:
            if len(list_stack) > 1:
                list_stack.pop()
            continue
        if token.type == "list_item_open":
            frame = list_stack[-1]
            if frame["ordered"]:
                marker = f"{frame['next']}. "
                frame["item_marker_width"] = len(marker)
                item_prefix = f"{' ' * frame['base_indent']}{marker}"
                frame["next"] += 1
            elif any(frame["ordered"] for frame in list_stack):
                marker = "- "
                frame["item_marker_width"] = len(marker)
                item_prefix = f"{' ' * frame['base_indent']}{marker}"
            else:
                # The chunker adds the marker to unordered items. Record its
                # width here so any ordered child still aligns under content.
                frame["item_marker_width"] = 2
                item_prefix = ""
            continue
        if token.type == "list_item_close":
            item_prefix = None
            continue
        if token.type == "inline" and token.content.strip():
            items.append(f"{item_prefix or ''}{token.content.strip()}")
            item_prefix = None
    return items


def _list_frame(token: Any, *, base_indent: int = 0) -> dict[str, Any]:
    ordered = token.type == "ordered_list_open"
    start = token.attrGet("start") if ordered else None
    try:
        next_number = int(start) if start is not None else 1
    except (TypeError, ValueError):
        next_number = 1
    return {
        "ordered": ordered,
        "next": next_number,
        "base_indent": base_indent,
        "item_marker_width": 0,
    }


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
