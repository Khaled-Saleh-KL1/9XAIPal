import json
from pathlib import Path

import fitz
import pytest
from markdown_it import MarkdownIt

from app.extraction.arabic_adapter import (
    pages_to_content_list,
    parse_complete_page_prefix,
)
from app.extraction.arabic_types import ArabicOcrPage
from app.extraction.chunker import create_chunks_from_content_list


def page_section(number: int, markdown: str) -> str:
    return f"<!-- PAGE:{number} -->\n{markdown}\n<!-- END_PAGE:{number} -->"


def ocr_page(number: int, markdown: str, *, repaired: bool = False) -> ArabicOcrPage:
    return ArabicOcrPage(
        page_number=number,
        raw_markdown=markdown,
        markdown=markdown,
        provider="gemini_arabic_flash",
        model="gemini-3.7-flash",
        repaired=repaired,
    )


def _png_bytes(width: int, height: int, color: int = 0) -> bytes:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pixmap.clear_with(color)
    return pixmap.tobytes("png")


def _figure_pdf(path: Path, image_rects: list[tuple[fitz.Rect, bytes]]) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 60), "Arabic text page")
    for rect, image_bytes in image_rects:
        page.insert_image(rect, stream=image_bytes)
    document.save(path)
    document.close()
    return path


def _draw_vector_chart(page, rect: fitz.Rect, count: int = 24) -> None:
    """Draw enough distinct vector items to represent a chart region."""
    for index in range(count):
        x = rect.x0 + 12 + index * (rect.width - 24) / max(count - 1, 1)
        color = (0.1 + (index % 20) * 0.025, 0.3, 0.5)
        if index % 2:
            page.draw_line(
                fitz.Point(x, rect.y0 + 8),
                fitz.Point(x, rect.y1 - 8),
                color=color,
                width=0.7,
            )
        else:
            bar_height = 25 + (index % 5) * 9
            page.draw_rect(
                fitz.Rect(x - 3, rect.y1 - bar_height, x + 3, rect.y1),
                color=color,
                fill=(0.5, 0.65, 0.8),
                width=0.7,
            )
    for index in range(4):
        y = rect.y0 + 15 + index * (rect.height - 30) / 3
        page.draw_line(
            fitz.Point(rect.x0, y),
            fitz.Point(rect.x1, y),
            color=(0.2 + index * 0.1, 0.1, 0.7),
            width=0.35 + index * 0.07,
        )
    assert len(page.get_drawings()) >= 20


def _insert_actual_text_caption(
    pdf_path: Path, anchor: str, caption: str
) -> None:
    """Give a generated PDF a Unicode text-layer caption without a font fixture."""
    pending_path = pdf_path.with_name(f"{pdf_path.stem}-actual-text.pdf")
    document = fitz.open(pdf_path)
    page = document[0]
    actual_text = caption.encode("utf-16-be").hex().upper()
    anchor_marker = f"<{anchor.encode('ascii').hex()}>".encode("ascii")
    for xref in page.get_contents():
        stream = document.xref_stream(xref)
        if anchor_marker not in stream:
            continue
        wrapped = (
            f"/Span << /ActualText <FEFF{actual_text}> >> BDC\n".encode("ascii")
            + stream
            + b"\nEMC"
        )
        document.update_stream(xref, wrapped)
        document.save(pending_path)
        document.close()
        pending_path.replace(pdf_path)
        return
    document.close()
    raise AssertionError("inserted caption text stream was not found")


def test_complete_prefix_survives_truncated_last_page():
    raw = """<!-- PAGE:5 -->
# عنوان
نص
<!-- END_PAGE:5 -->
<!-- PAGE:6 -->
نص غير مكتمل"""

    parsed = parse_complete_page_prefix(
        raw,
        expected_pages=[5, 6],
        provider="gemini_arabic_flash",
        model="m",
    )

    assert [page.page_number for page in parsed.pages] == [5]
    assert parsed.first_uncommitted_page == 6
    assert parsed.is_complete is False


def test_gap_or_duplicate_never_commits_later_pages():
    raw = page_section(1, "أ") + "\n" + page_section(3, "ج")

    parsed = parse_complete_page_prefix(raw, [1, 2, 3], "gemini", "m")

    assert [page.page_number for page in parsed.pages] == [1]
    assert parsed.first_uncommitted_page == 2

    repeated = "\n".join(
        [page_section(1, "أول"), page_section(1, "أول مرة أخرى"), page_section(2, "ثان")]
    )
    duplicate_parsed = parse_complete_page_prefix(
        repeated, [1, 2], "gemini", "m"
    )
    assert [page.page_number for page in duplicate_parsed.pages] == [1]
    assert duplicate_parsed.first_uncommitted_page == 2


def test_out_of_order_page_stops_at_first_expected_number():
    raw = "\n".join(
        [page_section(1, "أ"), page_section(3, "ج"), page_section(2, "ب")]
    )

    parsed = parse_complete_page_prefix(raw, [1, 2, 3], "gemini", "m")

    assert [page.page_number for page in parsed.pages] == [1]
    assert parsed.first_uncommitted_page == 2


@pytest.mark.parametrize(
    "raw",
    [
        "missing all markers",
        "<!-- PAGE:1 -->\ntext without an end marker",
        "<!-- PAGE:1 -->\n\n<!-- END_PAGE:1 -->",
        "<!-- PAGE:1 -->\ntext\n<!-- END_PAGE:2 -->",
        "<!-- PAGE: 1 -->\ntext\n<!-- END_PAGE:1 -->",
    ],
)
def test_missing_malformed_mismatched_or_empty_page_is_not_committed(raw):
    parsed = parse_complete_page_prefix(raw, [1], "gemini", "m")

    assert parsed.pages == ()
    assert parsed.first_uncommitted_page == 1
    assert parsed.is_complete is False


def test_duplicate_after_requested_prefix_does_not_mark_complete():
    raw = page_section(1, "النص الصحيح") + "\n" + page_section(1, "تكرار")

    parsed = parse_complete_page_prefix(raw, [1], "gemini", "m")

    assert parsed.pages == ()
    assert parsed.first_uncommitted_page == 1
    assert parsed.is_complete is False


def test_repetition_loop_is_incomplete_not_silently_removed():
    repeated = "هذه فقرة مكررة وواضحة بشكل كامل للتجربة"
    parsed = parse_complete_page_prefix(
        page_section(1, "\n\n".join([repeated] * 3)), [1], "gemini", "m"
    )

    assert parsed.pages == ()
    assert parsed.first_uncommitted_page == 1


def test_markdown_maps_to_page_indexed_content_list():
    markdown = "# عنوان\n\n- أول\n- ثان\n\n| أ | ب |\n|---|---|\n| ١ | ٢ |"
    blocks = pages_to_content_list([ocr_page(2, markdown, repaired=True)])

    assert {block["type"] for block in blocks} >= {"text", "list", "table"}
    assert {block["page_idx"] for block in blocks} == {1}
    assert all(block["ocr_provider"] == "gemini_arabic_flash" for block in blocks)
    assert all(block["ocr_model"] == "gemini-3.7-flash" for block in blocks)
    assert all(block["ocr_repaired"] is True for block in blocks)
    heading = next(block for block in blocks if block.get("text_level"))
    assert heading["text_level"] == 1
    assert heading["text"] == "عنوان"
    list_block = next(block for block in blocks if block["type"] == "list")
    assert list_block["list_items"] == ["أول", "ثان"]
    table_block = next(block for block in blocks if block["type"] == "table")
    assert "<table" in table_block["table_body"]
    assert "١" in table_block["table_body"]


def test_html_table_is_kept_as_table_body():
    html = "<table><tr><th>عنوان</th></tr><tr><td>قيمة</td></tr></table>"

    blocks = pages_to_content_list([ocr_page(1, html)])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "table"
    assert blocks[0]["table_body"] == html


def test_html_table_caption_is_attached_to_table_for_chunker():
    html = "<table><caption>جدول النتائج</caption><tr><td>قيمة</td></tr></table>"

    blocks = pages_to_content_list([ocr_page(1, html)])

    assert [block["type"] for block in blocks] == ["table"]
    assert blocks[0]["table_caption"] == ["جدول النتائج"]
    assert "<caption>" not in blocks[0]["table_body"]


def test_inline_display_math_does_not_swallow_surrounding_arabic_prose():
    blocks = pages_to_content_list([
        ocr_page(1, "هذا نص عربي قبل المعادلة $$x=1$$ ثم يستمر النص العربي بعدها.")
    ])

    assert [block["type"] for block in blocks] == ["text"]
    assert blocks[0]["text"] == "هذا نص عربي قبل المعادلة $$x=1$$ ثم يستمر النص العربي بعدها."


@pytest.mark.parametrize(
    "source",
    [
        "$$a=1$$ وهذا نص عربي بين معادلتين $$b=2$$",
        r"\[a=1\] وهذا نص عربي بين معادلتين \[b=2\]",
    ],
)
def test_prose_between_two_display_equations_stays_text(source):
    blocks = pages_to_content_list([ocr_page(1, source)])

    assert [block["type"] for block in blocks] == ["text"]
    assert blocks[0]["text"] == source


@pytest.mark.parametrize(
    "source",
    [r"\[x=1\]", r"\begin{equation}x=1\end{equation}"],
)
def test_display_math_delimiters_are_normalized_for_chunker(source):
    blocks = pages_to_content_list([ocr_page(1, source)])

    assert [block["type"] for block in blocks] == ["equation"]
    assert blocks[0]["text"] == "$$\nx=1\n$$"


def test_display_environment_with_nested_cases_is_one_equation():
    source = r"\begin{align}f(x) = \begin{cases} 1 & x > 0 \\ 0 \end{cases}\end{align}"

    blocks = pages_to_content_list([ocr_page(1, source)])

    assert [block["type"] for block in blocks] == ["equation"]
    assert "\\begin{cases}" in blocks[0]["text"]


def test_nested_list_text_is_not_lost():
    blocks = pages_to_content_list(
        [ocr_page(1, "- عنصر رئيسي\n  - عنصر فرعي\n- العنصر الأخير")]
    )

    assert blocks[0]["type"] == "list"
    assert blocks[0]["list_items"] == ["عنصر رئيسي", "عنصر فرعي", "العنصر الأخير"]


def test_ordered_list_numbers_and_nested_levels_survive_adapter_and_chunker(tmp_path):
    markdown = (
        "2. العنصر الأول\n"
        "   1. العنصر الفرعي الأول\n"
        "   2. العنصر الفرعي الثاني\n"
        "3. العنصر الأخير"
    )
    entries = pages_to_content_list([ocr_page(1, markdown)])
    list_block = next(block for block in entries if block["type"] == "list")

    assert list_block["list_items"] == [
        "2. العنصر الأول",
        "   1. العنصر الفرعي الأول",
        "   2. العنصر الفرعي الثاني",
        "3. العنصر الأخير",
    ]

    content_list = tmp_path / "content_list.json"
    content_list.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    chunks = create_chunks_from_content_list(content_list)

    assert chunks[0]["markdown"] == "\n".join(list_block["list_items"])
    list_depths = []
    list_stack = []
    for token in MarkdownIt("gfm-like", {"linkify": False}).parse(chunks[0]["markdown"]):
        if token.type in {"ordered_list_open", "bullet_list_open"}:
            list_stack.append(token.type)
        elif token.type in {"ordered_list_close", "bullet_list_close"}:
            list_stack.pop()
        elif token.type == "list_item_open":
            list_depths.append(len(list_stack))
    assert list_depths == [1, 2, 2, 1]


def test_multi_digit_ordered_parent_and_nested_bullet_keep_markers(tmp_path):
    markdown = (
        "10. العنصر العاشر\n"
        "    1. عنصر فرعي مرتب\n"
        "    - عنصر فرعي نقطي\n"
        "11. العنصر الحادي عشر"
    )
    entries = pages_to_content_list([ocr_page(1, markdown)])
    list_block = next(block for block in entries if block["type"] == "list")

    assert list_block["list_items"] == [
        "10. العنصر العاشر",
        "    1. عنصر فرعي مرتب",
        "    - عنصر فرعي نقطي",
        "11. العنصر الحادي عشر",
    ]

    content_list = tmp_path / "content_list.json"
    content_list.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    chunks = create_chunks_from_content_list(content_list)

    assert chunks[0]["markdown"] == "\n".join(list_block["list_items"])
    assert "- - عنصر فرعي نقطي" not in chunks[0]["markdown"]
    list_depths = []
    list_stack = []
    for token in MarkdownIt("gfm-like", {"linkify": False}).parse(chunks[0]["markdown"]):
        if token.type in {"ordered_list_open", "bullet_list_open"}:
            list_stack.append(token.type)
        elif token.type in {"ordered_list_close", "bullet_list_close"}:
            list_stack.pop()
        elif token.type == "list_item_open":
            list_depths.append(len(list_stack))
    assert list_depths == [1, 2, 2, 1]


def test_fenced_display_math_maps_to_equation():
    blocks = pages_to_content_list(
        [ocr_page(1, "```math\n\\alpha + \\beta = 1\n```")]
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "equation"
    assert "\\alpha" in blocks[0]["text"]


def test_captions_and_ambiguous_markdown_are_preserved_as_text():
    markdown = "![وصف الشكل](figure.png)\n\n> نص مقتبس\n\nنص غير معروف محفوظ"

    blocks = pages_to_content_list([ocr_page(1, markdown)])

    text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
    assert "وصف الشكل" in text
    assert "نص مقتبس" in text
    assert "نص غير معروف محفوظ" in text


def test_empty_markdown_produces_no_empty_artifact_blocks():
    assert pages_to_content_list([ocr_page(1, " \n\n ")]) == []


def test_embedded_figure_is_cropped_and_captioned_from_arabic_ocr(tmp_path):
    output_dir = tmp_path / "extract"
    rect = fitz.Rect(80, 180, 380, 380)
    pdf_path = _figure_pdf(
        tmp_path / "arabic-figure.pdf",
        [(rect, _png_bytes(300, 200))],
    )

    blocks = pages_to_content_list(
        [ocr_page(1, "نص عربي عن النظام\n\nالشكل 1: مخطط النظام")],
        pdf_path=pdf_path,
        output_dir=output_dir,
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 1
    figure = figures[0]
    assert figure["img_path"].startswith("images/")
    assert figure["page_idx"] == 0
    assert figure["bbox"] == pytest.approx([80, 180, 380, 380])
    assert figure["img_caption"] == ["الشكل 1: مخطط النظام"]
    crop_path = output_dir / figure["img_path"]
    assert crop_path.is_file()
    crop = fitz.Pixmap(str(crop_path))
    assert crop.width >= 80
    assert crop.height >= 80


def test_tiny_icon_and_full_page_scan_are_skipped(tmp_path):
    document = fitz.open()
    icon_page = document.new_page()
    icon_page.insert_image(
        fitz.Rect(20, 20, 40, 40), stream=_png_bytes(20, 20)
    )
    scan_page = document.new_page()
    scan_page.insert_image(
        scan_page.rect, stream=_png_bytes(500, 700)
    )
    pdf_path = tmp_path / "icons-and-scan.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, "نص مع أيقونة صغيرة"), ocr_page(2, "صفحة ممسوحة")],
        pdf_path=pdf_path,
        output_dir=tmp_path / "extract",
        dpi=200,
    )

    assert not any(block["type"] == "image" for block in blocks)


def test_two_figures_pair_with_caption_lines_in_page_order(tmp_path):
    first_rect = fitz.Rect(60, 130, 360, 330)
    second_rect = fitz.Rect(100, 480, 400, 680)
    pdf_path = _figure_pdf(
        tmp_path / "two-figures.pdf",
        [
            (first_rect, _png_bytes(300, 200, 80)),
            (second_rect, _png_bytes(300, 200, 160)),
        ],
    )

    blocks = pages_to_content_list(
        [
            ocr_page(
                1,
                "مقدمة\nالشكل ١: البنية العامة\nتفاصيل\nFig. 2: المقارنة",
            )
        ],
        pdf_path=pdf_path,
        output_dir=tmp_path / "extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert [figure["img_caption"] for figure in figures] == [
        ["الشكل ١: البنية العامة"],
        ["Fig. 2: المقارنة"],
    ]
    assert [figure["bbox"][1] for figure in figures] == [130, 480]
    assert len({figure["img_path"] for figure in figures}) == 2


def test_vector_chart_above_pdf_caption_is_emitted_with_its_region(tmp_path):
    caption = "Figure 1: Vector chart"
    document = fitz.open()
    page = document.new_page()
    _draw_vector_chart(page, fitz.Rect(120, 160, 420, 350))
    page.insert_text((120, 390), caption, fontsize=11)
    drawing_bounds = fitz.Rect()
    for drawing in page.get_drawings():
        drawing_bounds |= drawing["rect"]
    pdf_path = tmp_path / "vector-chart.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, caption)],
        pdf_path=pdf_path,
        output_dir=tmp_path / "vector-chart-extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 1
    figure = figures[0]
    assert figure["img_caption"] == [caption]
    assert figure["bbox"][0] <= drawing_bounds.x0
    assert figure["bbox"][1] <= drawing_bounds.y0
    assert figure["bbox"][2] >= drawing_bounds.x1
    assert figure["bbox"][3] >= drawing_bounds.y1


def test_vector_chart_below_pdf_caption_is_emitted(tmp_path):
    caption = "Fig. 1: Chart below its caption"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((100, 130), caption, fontsize=11)
    _draw_vector_chart(page, fitz.Rect(120, 170, 420, 350))
    drawing_bounds = fitz.Rect()
    for drawing in page.get_drawings():
        drawing_bounds |= drawing["rect"]
    pdf_path = tmp_path / "caption-before-vector-chart.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, caption)],
        pdf_path=pdf_path,
        output_dir=tmp_path / "caption-before-vector-chart-extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 1
    assert figures[0]["img_caption"] == [caption]
    assert figures[0]["bbox"][1] <= drawing_bounds.y0
    assert figures[0]["bbox"][3] >= drawing_bounds.y1


def test_table_rules_and_table_caption_do_not_create_a_figure(tmp_path):
    document = fitz.open()
    page = document.new_page()
    for row in range(12):
        y = 150 + row * 12
        page.draw_line(fitz.Point(90, y), fitz.Point(500, y), width=0.6)
    for column in range(4):
        x = 90 + column * 130
        page.draw_line(fitz.Point(x, 150), fitz.Point(x, 282), width=0.6)
    page.insert_text((90, 320), "Table 1: Benchmark scores", fontsize=11)
    pdf_path = tmp_path / "table-rules.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, "Table 1: Benchmark scores")],
        pdf_path=pdf_path,
        output_dir=tmp_path / "table-rules-extract",
        dpi=200,
    )

    assert not any(block["type"] == "image" for block in blocks)


def test_embedded_image_and_vector_figure_pair_with_nearest_pdf_captions(tmp_path):
    document = fitz.open()
    page = document.new_page()
    image_rect = fitz.Rect(70, 120, 330, 290)
    page.insert_image(image_rect, stream=_png_bytes(260, 170, 80))
    image_caption = "Figure 1: Embedded image"
    vector_caption = "Figure 2: Vector chart"
    page.insert_text((70, 320), image_caption, fontsize=11)
    vector_rect = fitz.Rect(120, 420, 420, 610)
    _draw_vector_chart(page, vector_rect)
    drawing_bounds = fitz.Rect()
    for drawing in page.get_drawings():
        drawing_bounds |= drawing["rect"]
    page.insert_text((120, 650), vector_caption, fontsize=11)
    pdf_path = tmp_path / "image-and-vector.pdf"
    document.save(pdf_path)
    document.close()

    # Provider Markdown is intentionally in the opposite order from the PDF
    # geometry. Captions with text-layer bboxes must follow their nearest crop.
    blocks = pages_to_content_list(
        [ocr_page(1, f"{vector_caption}\n{image_caption}")],
        pdf_path=pdf_path,
        output_dir=tmp_path / "image-and-vector-extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 2
    assert [figure["img_caption"] for figure in figures] == [
        [image_caption],
        [vector_caption],
    ]
    assert figures[0]["bbox"] == pytest.approx(list(image_rect))
    assert figures[1]["bbox"][1] <= drawing_bounds.y0
    assert figures[1]["bbox"][3] >= drawing_bounds.y1


def test_arabic_indic_figure_caption_is_found_from_pdf_text_layer(tmp_path):
    caption = "شكل ١: مخطط متجهي"
    anchor = "Arabic caption anchor"
    document = fitz.open()
    page = document.new_page()
    _draw_vector_chart(page, fitz.Rect(120, 160, 420, 350))
    page.insert_text((120, 390), anchor, fontsize=11)
    pdf_path = tmp_path / "arabic-vector-chart.pdf"
    document.save(pdf_path)
    document.close()
    _insert_actual_text_caption(pdf_path, anchor, caption)

    blocks = pages_to_content_list(
        [ocr_page(1, caption)],
        pdf_path=pdf_path,
        output_dir=tmp_path / "arabic-vector-chart-extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 1
    assert figures[0]["img_caption"] == [caption]


@pytest.mark.parametrize(
    ("rotation", "expected_bbox"),
    [
        (90, [22, 80, 192, 380]),
        (270, [650, 215, 820, 515]),
    ],
)
def test_rotated_page_figure_is_cropped_in_display_coordinates(
    tmp_path, rotation, expected_bbox
):
    document = fitz.open()
    page = document.new_page()
    page.insert_image(
        fitz.Rect(80, 650, 380, 820), stream=_png_bytes(300, 170, 80)
    )
    page.set_rotation(rotation)
    pdf_path = tmp_path / f"rotated-{rotation}.pdf"
    document.save(pdf_path)
    document.close()

    output_dir = tmp_path / f"extract-{rotation}"
    blocks = pages_to_content_list(
        [ocr_page(1, "الشكل 1: شكل على صفحة مدارة")],
        pdf_path=pdf_path,
        output_dir=output_dir,
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert len(figures) == 1
    assert figures[0]["bbox"] == pytest.approx(expected_bbox)
    crop = fitz.Pixmap(str(output_dir / figures[0]["img_path"]))
    assert crop.width >= 80
    assert crop.height >= 80
    center = (crop.height // 2 * crop.width + crop.width // 2) * crop.n
    assert max(crop.samples[center : center + 3]) < 200


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_full_page_scan_is_not_emitted_as_a_figure(tmp_path, rotation):
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, stream=_png_bytes(500, 700))
    page.set_rotation(rotation)
    pdf_path = tmp_path / f"rotated-scan-{rotation}.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, "[NO_TEXT]")],
        pdf_path=pdf_path,
        output_dir=tmp_path / f"scan-extract-{rotation}",
        dpi=200,
    )

    assert not any(block["type"] == "image" for block in blocks)


def test_rotated_figures_pair_captions_by_visual_page_order(tmp_path):
    document = fitz.open()
    page = document.new_page()
    # Insert the visual bottom figure first so PDF resource order cannot pair
    # it with the first (topmost) OCR caption.
    page.insert_image(
        fitz.Rect(350, 100, 550, 300), stream=_png_bytes(200, 200, 100)
    )
    page.insert_image(
        fitz.Rect(50, 500, 250, 700), stream=_png_bytes(200, 200, 180)
    )
    page.set_rotation(90)
    pdf_path = tmp_path / "rotated-order.pdf"
    document.save(pdf_path)
    document.close()

    blocks = pages_to_content_list(
        [ocr_page(1, "الشكل ١: العلوي\nالشكل ٢: السفلي")],
        pdf_path=pdf_path,
        output_dir=tmp_path / "rotated-order-extract",
        dpi=200,
    )

    figures = [block for block in blocks if block["type"] == "image"]
    assert [figure["img_caption"] for figure in figures] == [
        ["الشكل ١: العلوي"],
        ["الشكل ٢: السفلي"],
    ]
    assert [figure["bbox"][1] for figure in figures] == [50, 350]


def test_arabic_page_without_embedded_images_keeps_adapter_output_unchanged(tmp_path):
    pdf_path = tmp_path / "text-only.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    page = ocr_page(1, "عنوان\n\nنص عربي محفوظ كما هو")

    current_output = pages_to_content_list([page])
    pdf_aware_output = pages_to_content_list(
        [page],
        pdf_path=pdf_path,
        output_dir=tmp_path / "extract",
        dpi=200,
    )

    assert pdf_aware_output == current_output


def test_content_list_chunker_keeps_ordered_absolute_page_numbers(tmp_path):
    pages = [ocr_page(2, "# عنوان\n\nمحتوى الصفحة الثانية"), ocr_page(3, "نص الصفحة الثالثة")]
    entries = pages_to_content_list(pages)
    content_list = tmp_path / "content_list.json"
    content_list.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    chunks = create_chunks_from_content_list(content_list)

    page_numbers = [chunk["page_start"] for chunk in chunks]
    assert page_numbers == [2, 2, 3]


def test_unannotated_mineru_figure_keeps_its_existing_chunk_shape(tmp_path):
    content_list = tmp_path / "content_list.json"
    content_list.write_text(
        json.dumps(
            [
                {
                    "type": "image",
                    "page_idx": 0,
                    "img_path": "images/mineru-figure.png",
                    "bbox": [10, 20, 110, 120],
                    "img_caption": ["Figure 1: Existing caption"],
                }
            ]
        ),
        encoding="utf-8",
    )

    chunks = create_chunks_from_content_list(content_list)

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "figure"
    assert chunks[0]["page_start"] == 1
    assert chunks[0]["bbox_json"] is None
    assert chunks[0]["image_refs"] == ["mineru-figure.png"]


@pytest.mark.parametrize("expected_pages", [[], [0], [2, 1], [1, 1]])
def test_invalid_requested_page_numbers_are_rejected(expected_pages):
    with pytest.raises(ValueError):
        parse_complete_page_prefix("", expected_pages, "gemini", "m")


# Live (2026-09-30): an Arabic book whose page 1 is a cover picture failed as a
# whole — every provider returned an empty page 1, the parser refused it, and
# no page could ever be committed. A page with no text is now declared with the
# [NO_TEXT] marker; a silently empty page is still refused.
def test_no_text_marker_commits_an_empty_page():
    raw = page_section(1, "[NO_TEXT]") + "\n" + page_section(2, "نص الصفحة الثانية")

    parsed = parse_complete_page_prefix(raw, [1, 2], "gemini", "m")

    assert parsed.is_complete is True
    assert [p.page_number for p in parsed.pages] == [1, 2]
    assert parsed.pages[0].markdown == ""
    assert parsed.pages[1].markdown == "نص الصفحة الثانية"


def test_no_text_page_produces_no_content_blocks():
    parsed = parse_complete_page_prefix(page_section(1, " [NO_TEXT] "), [1], "gemini", "m")

    blocks = pages_to_content_list(parsed.pages)

    assert parsed.is_complete is True
    assert all(block.get("page_idx") != 0 or not str(block.get("text", "")).strip() for block in blocks)
    assert not any("NO_TEXT" in json.dumps(block, ensure_ascii=False) for block in blocks)


def test_ocr_prompts_explain_the_no_text_marker():
    from app.extraction.arabic_fallback import GemmaArabicFallback  # noqa: F401
    from app.extraction import arabic_fallback, gemini_ocr_client

    assert "[NO_TEXT]" in gemini_ocr_client.batch_prompt([])
    assert "[NO_TEXT]" in open(arabic_fallback.__file__, encoding="utf-8").read()


def test_ocr_image_links_are_dropped_from_page_text():
    # OCR models invent image URLs (seen on production: cdn.upstage.ai/...);
    # Arabic-route figures come only from PDF crops, so any OCR image link is fake.
    markdown = (
        "نص الصفحة الأول.\n\n"
        "![Figure 3: Benchmark size](https://cdn.example.org/qimma/figure3.png)\n\n"
        "Figure 3: Benchmark size versus discard rate.\n\n"
        "قبل ![شكل](figure.png) بعد"
    )

    blocks = pages_to_content_list([ocr_page(1, markdown)])
    text = json.dumps(blocks, ensure_ascii=False)

    assert "![" not in text
    assert "cdn.example.org" not in text
    assert "figure.png" not in text
    assert "Figure 3: Benchmark size versus discard rate." in text
    assert "نص الصفحة الأول." in text
    assert "قبل" in text and "بعد" in text


def test_ocr_prompts_forbid_image_links():
    from app.extraction import arabic_fallback, gemini_ocr_client

    assert "image links" in gemini_ocr_client.batch_prompt([])
    from app.core.config import Settings
    from app.extraction.arabic_types import RenderedPage

    payload = arabic_fallback.GemmaArabicFallback(settings=Settings())._payload(
        RenderedPage(page_number=1, png=b"png"), "png"
    )
    assert "image links" in payload["messages"][0]["content"]


@pytest.mark.parametrize("source,caption", [
    ('![Figure 4 [12]](https://cdn.example.org/missing.png)', 'Figure 4 [12]'),
    ('![Figure 4:\ncaption](https://cdn.example.org/missing.png)', 'Figure 4:\ncaption'),
    ('![شكل](https://cdn.example.org/figure(4).png)', 'شكل'),
    (r'![شكل](https://cdn.example.org/figure\(4\).png "a (title)")', 'شكل'),
    ('![Figure 4][1]\n\n[1]: https://cdn.example.org/missing.png', 'Figure 4'),
    ('![Figure 4]\n\n[Figure 4]: https://cdn.example.org/missing.png', 'Figure 4'),
    ('![# Figure 4](x)', '# Figure 4'),
    ('![1. Caption](x)', '1. Caption'),
    ('![$$x=1$$](x)', r'\$\$x=1\$\$'),
    ('![<b> & *caption*](x)', r'&lt;b&gt; &amp; \*caption\*'),
    ('<img src="https://cdn.example.org/missing.png" alt="Figure 4">', 'Figure 4'),
    ("<IMG alt='Figure 4' src='x' />", 'Figure 4'),
    ('before <img alt=Figure src=x> after', 'before Figure after'),
])
def test_parsed_images_become_exact_plain_captions(source, caption):
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "text_level" not in blocks[0]
    assert blocks[0]["text"] == caption
    assert not any(marker in blocks[0]["text"] for marker in ('![', 'https://', '<img'))


@pytest.mark.parametrize("source", ['![](x)', '<img src=x>', '<img alt="" src=x>'])
def test_empty_parsed_images_are_removed(source):
    assert pages_to_content_list([ocr_page(1, source)]) == []


@pytest.mark.parametrize("source,kind,field,expected", [
    ('# Title ![caption [12]](x)', 'text', 'text', 'Title caption [12]'),
    ('- Item ![caption [12]](x)', 'list', 'list_items', ['Item caption [12]']),
    ('| Figure |\n| --- |\n| ![Figure 4][1] |\n\n[1]: https://cdn.example.org/missing.png',
     'table', 'table_body', '<table>\n<thead>\n<tr>\n<th>Figure</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td>Figure 4</td>\n</tr>\n</tbody>\n</table>'),
    ('<table><tr><td><img src=x alt="Figure 4"></td></tr></table>',
     'table', 'table_body', '<table><tr><td>Figure 4</td></tr></table>'),
])
def test_images_preserve_surrounding_block_structure(source, kind, field, expected):
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert len(blocks) == 1
    assert blocks[0]["type"] == kind
    assert blocks[0][field] == expected
    if source.startswith('#'):
        assert blocks[0]['text_level'] == 1


@pytest.mark.parametrize("source,kind", [
    ('```markdown\n![example](https://example.org/image.png)\n```', 'text'),
    ('    ![example](https://example.org/image.png)', 'text'),
    ('`![example](image.png)`', 'text'),
    (r'\![12](https://example.org/paper)', 'text'),
    ('$$n![k](n-k)$$', 'equation'),
    ('$n![k](n-k)$', 'text'),
    ('before $$n![k](n-k)$$ after ![caption](x)', 'text'),
    ('[text](url)', 'text'),
])
def test_non_image_syntax_is_preserved(source, kind):
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert len(blocks) == 1
    assert blocks[0]['type'] == kind
    expected = source.strip().replace('after ![caption](x)', 'after caption')
    assert blocks[0]['text'] == expected


@pytest.mark.parametrize("source,expected", [
    ('before ![one](x) middle ![two](y) after', 'before one middle two after'),
    ('![outer ![inner](y)](x)', 'outer inner'),
    ('![\\[literal\\]](x)', '[literal]'),
    ('<img src=x alt="&lt;b&gt; &amp; *caption*">\n<table><tr><td>value</td></tr></table>',
     r'&lt;b&gt; &amp; \*caption\*'),
])
def test_literal_captions_survive_multiple_nodes_and_html_table_splitting(source, expected):
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert blocks[0]['type'] == 'text'
    assert blocks[0]['text'] == expected


@pytest.mark.parametrize("source", [
    '| Figure |\n| --- |\n| <img src=x alt="*caption* &amp; &lt;b&gt;"> |',
    '<table><tr><td><img src=x alt="*caption* &amp; &lt;b&gt;"></td></tr></table>',
])
def test_html_image_captions_in_tables_stay_literal_after_html_extraction(source):
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert blocks[0]['type'] == 'table'
    assert r'<td>\*caption\* &amp;amp; &amp;lt;b&amp;gt;</td>' in blocks[0]['table_body']
    assert '<img' not in blocks[0]['table_body']


def test_html_image_syntax_inside_math_is_preserved_in_html_blocks():
    source = '<div>$$n<img src=x alt=k>$$</div>'
    blocks = pages_to_content_list([ocr_page(1, source)])
    assert blocks[0]['text'] == source


def test_code_span_dollars_do_not_protect_a_real_image_as_math():
    blocks = pages_to_content_list([ocr_page(1, '`$` ![caption](x) `$`')])
    assert blocks[0]['text'] == '`$` caption `$`'


@pytest.mark.parametrize("source", [
    '| Figure |\n| --- |\n| ![$$x=1$$ *caption*](x) |',
    '| Figure |\n| --- |\n| ![outer ![inner](y)](x) |',
])
def test_markdown_image_table_captions_are_literal_for_the_reader(source):
    blocks = pages_to_content_list([ocr_page(1, source)])
    body = blocks[0]['table_body']
    if '$$' in source:
        assert r'<td>\$\$x=1\$\$ \*caption\*</td>' in body
    else:
        assert '<td>outer inner</td>' in body
    assert '<img' not in body


@pytest.mark.parametrize("source", [
    '<img src=y alt="&lt;img src=x&gt;">\n<table><tr><td>A</td></tr></table>',
    '<table><tr><td>A</td></tr></table>\n<img src=y alt="&lt;img src=x&gt;">',
])
def test_html_table_splitting_does_not_unescape_a_caption_into_an_image(source):
    blocks = pages_to_content_list([ocr_page(1, source)])
    text = next(block['text'] for block in blocks if block['type'] == 'text')
    assert text == '&lt;img src=x&gt;'


def test_html_alt_link_syntax_is_literal_text():
    blocks = pages_to_content_list([ocr_page(1, '<img src=x alt="[caption](destination)">')])
    assert blocks[0]['text'] == r'\[caption\](destination)'
