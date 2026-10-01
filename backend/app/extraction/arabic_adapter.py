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
    r"^\s*(?:الشكل|شكل|صورة|Figure|Fig\.?)\s*[\d٠-٩]+[^\n]*$",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:Table|جدول)\s*[\d٠-٩]+[^\n]*$", re.IGNORECASE
)
_VECTOR_FIGURE_MIN_DRAWINGS = 16
_FIGURE_CAPTION_NEARBY_PT = 72.0
_FIGURE_REGION_MARGIN_PT = 5.0
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
    """Render embedded images and captioned vector figures as image blocks."""
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

            text_lines, has_text_layer = _pdf_text_lines(page, fitz)
            pdf_captions = [line for line in text_lines if line["kind"]]
            image_rects = _embedded_image_rects(page, fitz, page_rect)
            image_regions = _embedded_image_regions(
                image_rects,
                page_area,
                page,
                matrix,
                dpi,
                merge_tiles=has_text_layer,
            )

            vector_regions = []
            if has_text_layer:
                drawing_rects = _pdf_drawing_rects(page, fitz, page_rect)
                for caption in pdf_captions:
                    if caption["kind"] != "figure":
                        continue
                    region = _vector_region_for_caption(
                        page_rect,
                        caption,
                        pdf_captions,
                        text_lines,
                        drawing_rects,
                        image_rects,
                        image_regions,
                        dpi,
                        fitz,
                    )
                    if region is not None:
                        vector_regions.append(region)

            regions = vector_regions + [
                region
                for region in image_regions
                if not any(
                    _rect_intersection_ratio(region["bbox"], vector["bbox"]) >= 0.5
                    for vector in vector_regions
                )
            ]
            regions.sort(key=lambda region: (region["bbox"].y0, -region["bbox"].x1))
            if not regions:
                continue

            ocr_captions = _ocr_caption_lines(ocr_page.markdown)
            has_pdf_captions = bool(pdf_captions)
            if has_pdf_captions:
                caption_assignments = _pair_regions_with_pdf_captions(regions, pdf_captions)
            else:
                caption_assignments = [
                    ocr_captions[index] if index < len(ocr_captions) else None
                    for index in range(len(regions))
                ]

            page_figures = []
            image_dir.mkdir(parents=True, exist_ok=True)
            for image_number, (region, caption) in enumerate(
                zip(regions, caption_assignments), start=1
            ):
                if caption is not None and caption["kind"] == "table":
                    continue
                rect = region["bbox"]
                pixmap = page.get_pixmap(matrix=matrix, clip=rect)
                if pixmap.width < 80 or pixmap.height < 80:
                    continue
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
                        "img_caption": [caption["text"]]
                        if caption is not None and caption["kind"] == "figure"
                        else [],
                    }
                )
            if page_figures:
                figures_by_page[ocr_page.page_number] = page_figures

    return figures_by_page


def _pdf_text_lines(page: Any, fitz: Any) -> tuple[list[dict[str, Any]], bool]:
    """Read caption candidates and body lines from the PDF text layer."""
    lines: list[dict[str, Any]] = []
    has_text_layer = False
    rotation = page.rotation_matrix
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            has_text_layer = True
            bbox = (fitz.Rect(line["bbox"]) * rotation) & page.rect
            kind = (
                "figure"
                if _FIGURE_CAPTION_RE.match(text)
                else "table"
                if _TABLE_CAPTION_RE.match(text)
                else None
            )
            lines.append({"text": text, "bbox": bbox, "kind": kind})
    return lines, has_text_layer


def _ocr_caption_lines(markdown: str) -> list[dict[str, Any]]:
    captions = []
    for line in markdown.splitlines():
        text = line.strip()
        if _FIGURE_CAPTION_RE.match(text):
            captions.append({"text": text, "bbox": None, "kind": "figure"})
        elif _TABLE_CAPTION_RE.match(text):
            captions.append({"text": text, "bbox": None, "kind": "table"})
    return captions


def _embedded_image_rects(page: Any, fitz: Any, page_rect: Any) -> list[Any]:
    rects = []
    seen_xrefs: set[int] = set()
    for image_info in page.get_images(full=True):
        xref = int(image_info[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        for image_rect in page.get_image_rects(xref):
            # Image placement APIs use unrotated coordinates; clips use the
            # displayed page coordinates used for all output bboxes.
            rect = (fitz.Rect(image_rect) * page.rotation_matrix) & page_rect
            if not rect.is_empty:
                rects.append(rect)
    return rects


def _embedded_image_regions(
    image_rects: list[Any],
    page_area: float,
    page: Any,
    matrix: Any,
    dpi: int,
    *,
    merge_tiles: bool,
) -> list[dict[str, Any]]:
    """Merge adjacent text-page tiles before applying the legacy size filters."""
    import fitz

    if merge_tiles:
        clusters: list[dict[str, Any]] = []
        for rect in image_rects:
            pending = {"bbox": fitz.Rect(rect), "rects": [fitz.Rect(rect)]}
            index = 0
            while index < len(clusters):
                cluster = clusters[index]
                expanded = _expand_rect(pending["bbox"], 3, fitz)
                if any(expanded.intersects(member) for member in cluster["rects"]):
                    pending["bbox"] |= cluster["bbox"]
                    pending["rects"].extend(cluster["rects"])
                    clusters.pop(index)
                    index = 0
                else:
                    index += 1
            clusters.append(pending)
    else:
        clusters = [
            {"bbox": fitz.Rect(rect), "rects": [fitz.Rect(rect)]}
            for rect in image_rects
        ]

    regions = []
    for cluster in clusters:
        rect = cluster["bbox"]
        area_share = (rect.width * rect.height) / page_area
        # Keep the existing icon/decoration and full-page-scan filters. Tiles
        # are assessed by their combined bounds so a split figure can survive.
        if area_share < 0.05 or area_share > 0.90:
            continue
        if rect.width * dpi / 72.0 < 80 or rect.height * dpi / 72.0 < 80:
            continue
        pixmap = page.get_pixmap(matrix=matrix, clip=rect)
        if pixmap.width < 80 or pixmap.height < 80:
            continue
        regions.append({"bbox": rect, "image_rects": cluster["rects"], "kind": "image"})
    return regions


def _pdf_drawing_rects(page: Any, fitz: Any, page_rect: Any) -> list[Any]:
    rects = []
    for drawing in page.get_drawings():
        rect = (fitz.Rect(drawing["rect"]) * page.rotation_matrix) & page_rect
        if rect.width < 1.0:
            rect.x0 -= 0.5
            rect.x1 += 0.5
        if rect.height < 1.0:
            rect.y0 -= 0.5
            rect.y1 += 0.5
        rect &= page_rect
        if not rect.is_empty:
            rects.append(rect)
    return rects


def _rect_intersection_ratio(first: Any, second: Any) -> float:
    intersection = first & second
    first_area = first.width * first.height
    if intersection.is_empty or first_area <= 0:
        return 0.0
    return (intersection.width * intersection.height) / first_area


def _expand_rect(rect: Any, margin: float, fitz: Any) -> Any:
    return fitz.Rect(
        rect.x0 - margin,
        rect.y0 - margin,
        rect.x1 + margin,
        rect.y1 + margin,
    )


def _same_caption_column(first: Any, second: Any) -> bool:
    return max(first.x0, second.x0) < min(first.x1, second.x1)


def _vector_region_for_caption(
    page_rect: Any,
    caption: dict[str, Any],
    captions: list[dict[str, Any]],
    text_lines: list[dict[str, Any]],
    drawing_rects: list[Any],
    image_rects: list[Any],
    existing_image_regions: list[dict[str, Any]],
    dpi: int,
    fitz: Any,
) -> dict[str, Any] | None:
    """Collect substantial vector artwork adjacent to one PDF caption."""
    caption_rect = caption["bbox"]
    column_margin = min(60.0, max(18.0, page_rect.width * 0.10))
    column = fitz.Rect(
        max(page_rect.x0, caption_rect.x0 - column_margin),
        page_rect.y0,
        min(page_rect.x1, caption_rect.x1 + column_margin),
        page_rect.y1,
    )
    previous_caps = [
        other["bbox"].y1
        for other in captions
        if other is not caption
        and other["bbox"].y1 <= caption_rect.y0
        and _same_caption_column(other["bbox"], column)
    ]
    next_caps = [
        other["bbox"].y0
        for other in captions
        if other is not caption
        and other["bbox"].y0 >= caption_rect.y1
        and _same_caption_column(other["bbox"], column)
    ]
    page_before = max([page_rect.y0, *previous_caps])
    page_after = min([page_rect.y1, *next_caps])

    def prepare(direction: str) -> dict[str, Any] | None:
        if direction == "above":
            start, end = page_before, caption_rect.y0
        else:
            start, end = caption_rect.y1, page_after
        if end <= start:
            return None

        def in_window(rect: Any) -> bool:
            return (
                rect.y1 > start
                and rect.y0 < end
                and _same_caption_column(rect, column)
                and not any(rect.intersects(item["bbox"]) for item in captions)
            )

        rough_drawings = [rect for rect in drawing_rects if in_window(rect)]
        if len(rough_drawings) < _VECTOR_FIGURE_MIN_DRAWINGS:
            return None
        rough_bounds = fitz.Rect(rough_drawings[0])
        for rect in rough_drawings[1:]:
            rough_bounds |= rect
        if rough_bounds.width * dpi / 72.0 < 80 or rough_bounds.height * dpi / 72.0 < 80:
            return None

        # Chart labels are text-layer lines too. Exclude lines within the
        # preliminary drawing bounds when finding the body-text boundary.
        body_lines = [
            line
            for line in text_lines
            if line["kind"] is None
            and _same_caption_column(line["bbox"], caption_rect)
            and not line["bbox"].intersects(_expand_rect(rough_bounds, 2, fitz))
        ]
        if direction == "above":
            preceding = [line["bbox"].y1 for line in body_lines if line["bbox"].y1 <= caption_rect.y0]
            start = max([start, *preceding])
        else:
            following = [line["bbox"].y0 for line in body_lines if line["bbox"].y0 >= caption_rect.y1]
            end = min([end, *following])
        if end <= start:
            return None

        drawings = [rect for rect in drawing_rects if in_window(rect)]
        if len(drawings) < _VECTOR_FIGURE_MIN_DRAWINGS:
            return None
        bounds = fitz.Rect(drawings[0])
        for rect in drawings[1:]:
            bounds |= rect

        associated_images = [
            rect
            for rect in image_rects
            if rect.y1 > start
            and rect.y0 < end
            and _same_caption_column(rect, column)
            and rect.intersects(_expand_rect(bounds, 8, fitz))
        ]
        for rect in associated_images:
            bounds |= rect
        region = _expand_rect(bounds, _FIGURE_REGION_MARGIN_PT, fitz) & page_rect
        if region.width * dpi / 72.0 < 80 or region.height * dpi / 72.0 < 80:
            return None
        return {"bbox": region, "image_rects": associated_images, "kind": "vector"}

    above = prepare("above")
    if above is not None:
        return above

    # A nearby raster crop above this caption already represents it. Search
    # below only when there is no such crop; if a below-side vector group is
    # found, its own image overlays are merged into that region.
    near_image_above = any(
        rect.y1 <= caption_rect.y0
        and caption_rect.y0 - rect.y1 <= _FIGURE_CAPTION_NEARBY_PT
        and _same_caption_column(rect, column)
        for rect in (image["bbox"] for image in existing_image_regions)
    )
    if near_image_above:
        return None
    return prepare("below")


def _pair_regions_with_pdf_captions(
    regions: list[dict[str, Any]],
    captions: list[dict[str, Any]],
) -> list[dict[str, Any] | None]:
    available = list(captions)
    assignments = []
    for region in regions:
        rect = region["bbox"]
        candidates = []
        for caption in available:
            caption_rect = caption["bbox"]
            if not _same_caption_column(rect, caption_rect):
                continue
            horizontal_gap = max(0.0, caption_rect.x0 - rect.x1, rect.x0 - caption_rect.x1)
            if horizontal_gap > 18:
                continue
            if caption_rect.y0 >= rect.y1:
                direction, vertical_gap = "below", caption_rect.y0 - rect.y1
            elif caption_rect.y1 <= rect.y0:
                direction, vertical_gap = "above", rect.y0 - caption_rect.y1
            else:
                direction, vertical_gap = "below", 0.0
            if vertical_gap <= _FIGURE_CAPTION_NEARBY_PT:
                candidates.append((direction != "below", vertical_gap, horizontal_gap, caption))
        if not candidates:
            assignments.append(None)
            continue
        candidates.sort(key=lambda candidate: candidate[:3])
        selected = candidates[0][3]
        available.remove(selected)
        assignments.append(selected)
    return assignments


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
