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


# ── Front/back matter grouping ────────────────────────────────────────────────

from app.services.book_outline import group_matter


def _ch(title, start, end, level=1):
    return {"title": title, "level": level, "start_sequence": start, "end_sequence": end}


def test_boilerplate_runs_collapse_at_both_ends():
    out = group_matter([
        _ch("Front matter", 1, 4), _ch("Praise", 5, 29), _ch("Title Page", 30, 37),
        _ch("Copyright", 38, 45), _ch("Contents", 46, 62),
        _ch("Introduction: Mrs. Chen", 63, 187),
        _ch("1. Listening to the Air", 188, 353),
        _ch("Epilogue: Putting it to Work", 354, 400),
        _ch("Acknowledgments", 401, 413), _ch("Notes", 414, 436), _ch("Index", 437, 592),
    ])
    titles = [c["title"] for c in out]
    assert titles[0].startswith("Front matter — Praise, Title Page, Copyright")
    assert titles[-1].startswith("End matter — Acknowledgments, Notes, Index")
    # Real reading content is untouched, one entry each.
    assert titles[1:-1] == [
        "Introduction: Mrs. Chen", "1. Listening to the Air",
        "Epilogue: Putting it to Work",
    ]
    # The collapsed runs still span the full original sequence range.
    assert (out[0]["start_sequence"], out[0]["end_sequence"]) == (1, 62)
    assert (out[-1]["start_sequence"], out[-1]["end_sequence"]) == (401, 592)


def test_introduction_and_epilogue_are_never_treated_as_boilerplate():
    """They read like front/back matter but are real chapters."""
    out = group_matter([
        _ch("Introduction", 1, 10), _ch("1. Ch", 11, 20), _ch("Epilogue", 21, 30),
    ])
    assert [c["title"] for c in out] == ["Introduction", "1. Ch", "Epilogue"]


def test_a_single_boilerplate_entry_keeps_its_own_name():
    """Relabelling one page 'Front matter' tells the reader strictly less."""
    out = group_matter([_ch("Contents", 1, 5), _ch("1. Ch", 6, 20)])
    assert [c["title"] for c in out] == ["Contents", "1. Ch"]


def test_an_all_boilerplate_document_is_left_alone():
    src = [_ch("Contents", 1, 5), _ch("Index", 6, 20)]
    assert group_matter(src) == src
