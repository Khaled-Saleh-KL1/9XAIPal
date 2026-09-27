"""_is_plausible_chapter_heading: `re.search(r"[A-Za-z0-9]", ...)` treated any
heading with no ASCII letter/digit as an ornamental divider, so every Arabic
chapter title (having no ASCII letters at all) was rejected and the reader
fell back to showing only "Full document" for Arabic books. Fixed by
requiring at least one Unicode letter or digit (`[^\\W_]`) instead of an
ASCII one.
"""

from app.api.v1.endpoints.chunks import _is_plausible_chapter_heading


def test_arabic_chapter_headings_are_plausible():
    assert _is_plausible_chapter_heading("ثمن زوجة")
    assert _is_plausible_chapter_heading("الفصل الأول")


def test_english_chapter_heading_is_plausible():
    assert _is_plausible_chapter_heading("Chapter 1")


def test_ornamental_dividers_are_not_plausible():
    assert not _is_plausible_chapter_heading("* * *")
    assert not _is_plausible_chapter_heading("\\* \\* \\*")
    assert not _is_plausible_chapter_heading("---")
    assert not _is_plausible_chapter_heading("")


def test_speech_attribution_is_not_plausible():
    """Unchanged rule: a short line ending in ':' reads as speech
    attribution ("Ben laughed:"), not a chapter title."""
    assert not _is_plausible_chapter_heading("Ben laughed:")
