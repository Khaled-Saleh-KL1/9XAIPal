"""_make_section_id: Arabic (and any non-ASCII) headings used to strip to an
empty slug — `re.sub(r"[^a-z0-9]+", "-", ...)` only kept ASCII letters/digits
— so every Arabic section got the id "h1-", and since rows are upserted on
the unique key (document_id, section_id, model), each new Arabic section
summary overwrote the previous one (a 10-story book kept only 1). Long
English headings that share their first 60 characters collided the same
way. Fixed by a Unicode-aware slug plus a short stable hash of
level/sequence_start/heading_path appended to guarantee uniqueness.
"""

from app.summarization.section_summarizer_sync import (
    _make_section_id,
    group_chunks_into_sections,
)


def test_two_different_arabic_headings_get_different_ids():
    id1 = _make_section_id(["أول أبريل"], 1, 100)
    id2 = _make_section_id(["ثمن زوجة"], 1, 200)

    assert id1 != id2
    assert "أول" in id1 or "ابريل" in id1 or "أول-أبريل" in id1
    # the Arabic text itself must survive into the slug, not be stripped away
    assert any("؀" <= ch <= "ۿ" for ch in id1)
    assert any("؀" <= ch <= "ۿ" for ch in id2)


def test_same_heading_different_sequence_start_gives_different_ids():
    id1 = _make_section_id(["أول أبريل"], 1, 100)
    id2 = _make_section_id(["أول أبريل"], 1, 200)
    assert id1 != id2


def test_same_inputs_give_the_same_id():
    id1 = _make_section_id(["أول أبريل"], 1, 100)
    id2 = _make_section_id(["أول أبريل"], 1, 100)
    assert id1 == id2


def test_english_headings_sharing_a_60_char_prefix_get_different_ids():
    long_prefix = "A Very Long Heading About Something Long Enough To Collide "
    heading_a = long_prefix + "Part One"
    heading_b = long_prefix + "Part Two"
    assert heading_a[:70] != heading_b[:70]
    assert heading_a[:60] == heading_b[:60]

    id1 = _make_section_id([heading_a], 1, 1)
    id2 = _make_section_id([heading_b], 1, 1)
    assert id1 != id2


def test_empty_heading_path_is_level_root():
    assert _make_section_id([], 1, 1) == "level1-root"
    assert _make_section_id([], 2, 1) == "level2-root"


def _heading_chunk(text_val, sequence_id):
    return {
        "chunk_type": "heading",
        "heading_path": [text_val],
        "markdown": f"# {text_val}",
        "plain_text": text_val,
        "sequence_id": sequence_id,
        "id": f"chunk-{sequence_id}",
    }


def _body_chunk(text_val, sequence_id):
    return {
        "chunk_type": "text",
        "heading_path": None,
        "markdown": text_val,
        "plain_text": text_val,
        "sequence_id": sequence_id,
        "id": f"chunk-{sequence_id}",
    }


def test_three_arabic_level1_headings_produce_three_distinct_section_ids():
    chunks = [
        _heading_chunk("أول أبريل", 1),
        _body_chunk("نص أول", 2),
        _heading_chunk("ثمن زوجة", 3),
        _body_chunk("نص ثاني", 4),
        _heading_chunk("الفصل الثالث", 5),
        _body_chunk("نص ثالث", 6),
    ]

    sections = group_chunks_into_sections(chunks)

    assert len(sections) == 3
    ids = [s["section_id"] for s in sections]
    assert len(set(ids)) == 3


def test_repeated_arabic_h1_page_headers_stay_inside_one_story_section():
    chunks = [
        _heading_chunk("قصتان متوازيتان", 1),
        _body_chunk("تمهيد يتحدث عن قصتين متوازيتين.", 2),
        _heading_chunk("القيء", 3),
        _body_chunk("نص الصفحة الأولى من القصة.", 4),
        _heading_chunk("القيء", 5),  # Repeated OCR page header.
        _body_chunk("نص الصفحة الثانية من القصة.", 6),
        _heading_chunk("الخاتمة", 7),
        _body_chunk("نهاية الكتاب.", 8),
    ]

    sections = group_chunks_into_sections(chunks)

    story_sections = [section for section in sections if section["heading_text"] == "القيء"]
    assert len(story_sections) == 1
    assert "نص الصفحة الأولى من القصة." in story_sections[0]["text"]
    assert "نص الصفحة الثانية من القصة." in story_sections[0]["text"]
    assert "chunk-5" not in story_sections[0]["source_chunk_ids"]
    assert [section["heading_text"] for section in sections] == [
        "قصتان متوازيتان",
        "القيء",
        "الخاتمة",
    ]


def test_english_grouping_keeps_repeated_h1_boundaries_unchanged():
    chunks = [
        _heading_chunk("Introduction", 1),
        _body_chunk("Opening paragraph.", 2),
        _heading_chunk("Introduction", 3),
        _body_chunk("Continued paragraph.", 4),
        _heading_chunk("Methods", 5),
        _body_chunk("Method details.", 6),
    ]

    sections = group_chunks_into_sections(chunks)

    assert [section["heading_text"] for section in sections] == [
        "Introduction",
        "Introduction",
        "Methods",
    ]
