"""Chapter derivation from a PDF's embedded outline.

The collapse rule is the subtle part and the reason this file exists: a book
opens a chapter with a title *and* a subtitle on one page, while a dense paper
opens four unrelated sections on one page. Collapsing on "same page" alone
welds the paper's four into one nonsense heading — which an earlier draft of
this code did, verified against the real `Attention Is All You Need` outline.
"""
from app.services.book_outline import collapse_outline, outline_to_chapters


def test_subtitle_on_the_same_page_is_absorbed_into_its_chapter():
    """A lone deeper entry sharing its parent's page is a subtitle."""
    out = collapse_outline([
        {"level": 1, "title": "4. How Much Respect Do You Want?", "page": 128},
        {"level": 2, "title": "Leadership, Hierarchy, and Power", "page": 128},
        {"level": 1, "title": "5. Big D or Little d", "page": 156},
        {"level": 2, "title": "Who Decides, and How?", "page": 156},
    ])
    assert [e["title"] for e in out] == [
        "4. How Much Respect Do You Want? Leadership, Hierarchy, and Power",
        "5. Big D or Little d: Who Decides, and How?",
    ]


def test_several_sections_sharing_a_page_are_kept_separate():
    """The real regression: a paper packs many real sections onto one page."""
    out = collapse_outline([
        {"level": 1, "title": "Training", "page": 7},
        {"level": 2, "title": "Training Data and Batching", "page": 7},
        {"level": 2, "title": "Hardware and Schedule", "page": 7},
        {"level": 2, "title": "Optimizer", "page": 7},
        {"level": 1, "title": "Results", "page": 8},
    ])
    assert [e["title"] for e in out] == [
        "Training", "Training Data and Batching",
        "Hardware and Schedule", "Optimizer", "Results",
    ]


def test_a_lone_child_on_a_different_page_is_a_real_subsection():
    """Same-page is required — a child that opens later stays navigable."""
    out = collapse_outline([
        {"level": 1, "title": "Results", "page": 8},
        {"level": 2, "title": "English Constituency Parsing", "page": 9},
    ])
    assert [e["title"] for e in out] == ["Results", "English Constituency Parsing"]


def test_pages_map_to_the_first_chunk_at_or_after_them():
    page_starts = [(1, 1), (2, 1), (3, 5), (4, 9), (5, 12)]
    chapters = outline_to_chapters(
        [{"level": 1, "title": "One", "page": 5}, {"level": 1, "title": "Two", "page": 9}],
        page_starts, lo=1, hi=5,
    )
    # Content before the first outline target is kept as front matter rather
    # than silently dropped.
    assert [(c["title"], c["start_sequence"], c["end_sequence"]) for c in chapters] == [
        ("Front matter", 1, 2), ("One", 3, 3), ("Two", 4, 5),
    ]


def test_entries_landing_on_the_same_chunk_are_dropped():
    """Two chapters starting on one block would make one of them empty."""
    page_starts = [(1, 1), (2, 4)]
    chapters = outline_to_chapters(
        [{"level": 1, "title": "A", "page": 2}, {"level": 1, "title": "B", "page": 3}],
        page_starts, lo=1, hi=2,
    )
    assert [c["title"] for c in chapters] == ["Front matter", "A"]


def test_entries_past_the_last_chunk_are_dropped():
    chapters = outline_to_chapters(
        [{"level": 1, "title": "A", "page": 1}, {"level": 1, "title": "Ghost", "page": 999}],
        [(1, 1), (2, 2)], lo=1, hi=2,
    )
    assert [c["title"] for c in chapters] == ["A"]


def test_no_entries_yields_no_chapters():
    assert outline_to_chapters([], [(1, 1)], lo=1, hi=1) == []
