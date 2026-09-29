import pytest

from app.api.v1.endpoints.chunks import _is_plausible_chapter_heading
from app.services.book_outline import (
    _is_matter,
    chapter_entries,
    collapse_outline,
    group_matter,
    is_part_title,
    outline_to_chapters,
)


def _chapter(title, start, end, level=1):
    return {"title": title, "level": level, "start_sequence": start, "end_sequence": end}


def test_english_part_titles_and_front_matter_output_stay_unchanged():
    assert is_part_title("Part I")
    assert is_part_title("Section A")
    assert not is_part_title("Chapter 1: Exploring")
    assert outline_to_chapters(
        [{"level": 1, "title": "Chapter 1", "page": 3}],
        [(1, 1), (2, 3)],
        lo=1,
        hi=2,
    ) == [
        {"title": "Front matter", "level": 1, "start_sequence": 1, "end_sequence": 1},
        {"title": "Chapter 1", "level": 1, "start_sequence": 2, "end_sequence": 2},
    ]


@pytest.mark.parametrize("title", ["الجزء الأول", "الباب الثاني", "القسم ٣"])
def test_arabic_part_titles_are_recognized_and_folded_into_the_next_chapter(title):
    assert is_part_title(title)
    entries = chapter_entries([
        {"level": 1, "title": "مقدمة", "page": 1},
        {"level": 1, "title": title, "page": 3},
        {"level": 1, "title": "الفصل الأول", "page": 4},
    ])

    assert [entry["title"] for entry in entries] == ["مقدمة", "الفصل الأول"]
    assert entries[-1]["page"] == 3


@pytest.mark.parametrize(
    "title",
    ["الفهرس", "المحتويات", "فهرس المحتويات", "الإهداء", "شكر وتقدير", "المراجع", "المصادر", "الملاحق", "نبذة عن المؤلف"],
)
def test_arabic_book_apparatus_titles_are_matter(title):
    assert _is_matter(title)


def test_arabic_matter_runs_keep_the_existing_english_labels():
    out = group_matter([
        _chapter("الفهرس", 1, 2),
        _chapter("المحتويات", 3, 4),
        _chapter("Chapter 1", 5, 10),
        _chapter("الملاحق", 11, 12),
        _chapter("نبذة عن المؤلف", 13, 14),
    ])

    assert [chapter["title"] for chapter in out] == [
        "Front matter — الفهرس, المحتويات",
        "Chapter 1",
        "End matter — الملاحق, نبذة عن المؤلف",
    ]


@pytest.mark.parametrize("caption", ["الشكل ١", "شكل ٢", "الجدول ٣", "جدول ٤", "الشكل ۲"])
def test_arabic_numbered_figure_and_table_captions_are_not_chapters(caption):
    assert not _is_plausible_chapter_heading(caption)


def test_english_captions_and_real_arabic_headings_keep_their_existing_decisions():
    assert not _is_plausible_chapter_heading("Figure 1.1. Communicating")
    assert not _is_plausible_chapter_heading("TABLE 2")
    assert _is_plausible_chapter_heading("Chapter 1")
    assert _is_plausible_chapter_heading("الفصل الأول")


def test_arabic_question_mark_subtitle_uses_a_space_separator():
    out = collapse_outline([
        {"level": 1, "title": "كيف نتعلم؟", "page": 1},
        {"level": 2, "title": "دليل عملي", "page": 1},
    ])

    assert [entry["title"] for entry in out] == ["كيف نتعلم؟ دليل عملي"]
