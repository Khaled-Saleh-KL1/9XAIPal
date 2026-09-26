import json

import pytest

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


def test_content_list_chunker_keeps_ordered_absolute_page_numbers(tmp_path):
    pages = [ocr_page(2, "# عنوان\n\nمحتوى الصفحة الثانية"), ocr_page(3, "نص الصفحة الثالثة")]
    entries = pages_to_content_list(pages)
    content_list = tmp_path / "content_list.json"
    content_list.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    chunks = create_chunks_from_content_list(content_list)

    page_numbers = [chunk["page_start"] for chunk in chunks]
    assert page_numbers == [2, 2, 3]


@pytest.mark.parametrize("expected_pages", [[], [0], [2, 1], [1, 1]])
def test_invalid_requested_page_numbers_are_rejected(expected_pages):
    with pytest.raises(ValueError):
        parse_complete_page_prefix("", expected_pages, "gemini", "m")
