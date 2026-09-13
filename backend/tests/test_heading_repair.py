"""MinerU's heading mistakes, repaired before anything reads them —
app/extraction/heading_repair.py. Shapes taken from the live library on
2026-09-13 (see the module docstring for the measurements)."""

from app.extraction.heading_repair import (
    heading_level, is_not_a_heading, normalize_title, repair_headings,
)


def _h(seq, text, level=2, page=1):
    return {"sequence_id": seq, "chunk_type": "heading", "markdown": f"{'#' * level} {text}", "plain_text": text, "page_start": page, "heading_path": None}


def _t(seq, text, page=1):
    return {"sequence_id": seq, "chunk_type": "text", "markdown": text, "plain_text": text, "page_start": page, "heading_path": None}


def test_normalize_title_strips_every_numbering_shape_seen_live():
    assert normalize_title("1. Listening to the Air") == "listening to the air"
    assert normalize_title("LISTENING TO THE AIR") == "listening to the air"
    assert normalize_title("Chapter 3: Engineering Prompts") == "engineering prompts"
    assert normalize_title("3.2.1 Scaled Dot-Product Attention") == "scaled dot product attention"
    assert normalize_title("4 Why Self-Attention") == "why self attention"
    assert normalize_title("IV. Later") == "later"
    assert normalize_title("Part 1: \nAmazon Bedrock Foundations") == "amazon bedrock foundations"
    assert normalize_title("2D case") == "2d case"          # not a number-then-space


def test_what_is_not_a_heading():
    assert is_not_a_heading("* * *") == "no letters"
    assert is_not_a_heading("12") == "no letters"
    assert is_not_a_heading("FIGURE I.1.") == "caption"
    assert is_not_a_heading("Figure 5-22. The system prompt of Search-R1") == "caption"
    assert is_not_a_heading("documentation.") == "wrapped-line tail"
    assert is_not_a_heading("effectively handle?") == "wrapped-line tail"
    assert is_not_a_heading("When I arrived at my hotel in New Delhi, I was hot and, more importantly, hungry.") == "sentence"
    # Real headings that a cruder rule would have eaten.
    for t in ("add_tool", "wav2vec 2.0", "packtpub.com", "s1: Simple test-time scaling",
              "Outcome Evaluation: Did the Agent Get the Right Output?", "Who Decides, and How?",
              "Summary", "1 Introduction", "Chapter 2. Large Language Models"):
        assert is_not_a_heading(t) is None, t


def test_levels_follow_the_pdf_outline_and_subsections_nest_under_their_chapter():
    # MinerU: everything level 2, as on the live books.
    chunks = [
        _h(1, "Generative AI with Amazon Bedrock", 1, 1),
        _h(2, "Preface", 2, 14),
        _h(3, "Chapter 1: Exploring Amazon Bedrock", 2, 22),
        _h(4, "What are FMs?", 2, 24),
        _h(5, "Amazon Titan FMs", 2, 28),
        _t(6, "Body.", 28),
        _h(7, "Summary", 2, 45),
        _h(8, "Chapter 2: Accessing Models", 2, 48),
        _h(9, "Chat playground", 2, 51),
    ]
    outline = [
        {"level": 1, "title": "Preface", "page": 14},
        {"level": 1, "title": "Chapter 1: Exploring Amazon Bedrock", "page": 22},
        {"level": 2, "title": "What are FMs?", "page": 24},
        {"level": 3, "title": "Amazon Titan FMs", "page": 28},
        {"level": 1, "title": "Chapter 2: Accessing Models", "page": 48},
    ]
    report = repair_headings(chunks, outline)
    levels = {c["plain_text"]: heading_level(c) for c in chunks if c["chunk_type"] == "heading"}
    assert levels["Preface"] == 1 and levels["Chapter 1: Exploring Amazon Bedrock"] == 1 and levels["Chapter 2: Accessing Models"] == 1
    assert levels["What are FMs?"] == 2 and levels["Amazon Titan FMs"] == 3
    # Unmatched headings sit under the last matched one, never beside it.
    assert levels["Summary"] == 4          # after a level-3 match: at least 4
    assert levels["Chat playground"] == 2  # after the level-1 Chapter 2
    assert report.outline_matched == 5 and report.relevelled >= 4
    # heading_path is rebuilt from the corrected levels.
    assert chunks[5]["heading_path"] == ["Chapter 1: Exploring Amazon Bedrock", "What are FMs?", "Amazon Titan FMs"]
    assert chunks[8]["heading_path"] == ["Chapter 2: Accessing Models", "Chat playground"]


def test_outline_titles_are_matched_by_normalized_text_near_their_page():
    chunks = [_h(1, "1 Introduction", 2, 2), _h(2, "3.2 Attention", 2, 3), _h(3, "Summary", 2, 9), _h(4, "Summary", 2, 40)]
    outline = [{"level": 1, "title": "Introduction", "page": 2}, {"level": 2, "title": "Attention", "page": 3},
               {"level": 1, "title": "Summary", "page": 40}]
    repair_headings(chunks, outline)
    assert heading_level(chunks[0]) == 1 and heading_level(chunks[1]) == 2
    assert heading_level(chunks[3]) == 1          # the Summary on page 40, not the one on page 9
    assert heading_level(chunks[2]) == 3          # unmatched, nested under Attention (level 2)


def test_demotion_never_touches_an_outline_title():
    chunks = [_h(1, "Big D or Little d Who Decides, and How?", 2, 200), _h(2, "* * *", 2, 201), _h(3, "FIGURE 5.2.", 2, 202)]
    outline = [{"level": 1, "title": "5. Big D or Little d", "page": 200}, {"level": 1, "title": "6. Trust", "page": 230}]
    report = repair_headings(chunks, outline)
    assert chunks[0]["chunk_type"] == "heading" and heading_level(chunks[0]) == 1
    assert chunks[1]["chunk_type"] == "text" and chunks[1]["markdown"] == "* * *"
    assert chunks[2]["chunk_type"] == "text"
    assert report.demoted == 2 and report.reasons == {"no letters": 1, "caption": 1}


def test_without_an_outline_only_self_declared_chapters_are_promoted():
    chunks = [_h(1, "Chapter 3: Memory", 2), _h(2, "Types of Memory", 2), _h(3, "4. Tool Usage", 2), _h(4, "Body text.", 2), _t(5, "…")]
    report = repair_headings(chunks, None)
    assert heading_level(chunks[0]) == 1 and heading_level(chunks[2]) == 1
    assert heading_level(chunks[1]) == 2 and heading_level(chunks[3]) == 2
    assert report.promoted_chapters == 2 and report.relevelled == 0
    assert chunks[4]["heading_path"] == ["4. Tool Usage", "Body text."]


# ── In place, on stored rows ────────────────────────────────────────────────

import pytest
from uuid import uuid4
from sqlalchemy import text as sql


@pytest.mark.asyncio
async def test_repair_stored_headings_updates_only_what_changed(db_session):
    from app.database.connection import sync_session_factory
    from app.extraction.heading_repair import repair_stored_headings
    user = (await db_session.execute(sql("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"), {"e": f"{uuid4()}@example.com"})).scalar_one()
    doc = (await db_session.execute(sql("INSERT INTO documents (user_id, filename, original_filename, status, doc_kind) VALUES (:u, 'x.pdf', 'x.pdf', 'complete', 'book') RETURNING id"), {"u": user})).scalar_one()
    rows = [
        (1, "heading", "## Chapter 1: Memory", "Chapter 1: Memory"),
        (2, "heading", "## * * *", "* * *"),
        (3, "text", "Body", "Body"),
        (4, "heading", "## Types of Memory", "Types of Memory"),
    ]
    for seq, t, md, pt in rows:
        await db_session.execute(sql(
            "INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text, page_start, token_count) "
            "VALUES (:d, :s, :t, :md, :pt, 1, 1)"), {"d": doc, "s": seq, "t": t, "md": md, "pt": pt})
    await db_session.commit()

    with sync_session_factory() as s:
        report = repair_stored_headings(s, doc, None)     # no PDF: outline-less rules
        s.commit()
    assert report["demoted"] == 1 and report["promoted_chapters"] == 1
    assert report["rows_updated"] == 4                     # 2 headings changed + 2 paths rebuilt

    got = (await db_session.execute(sql("SELECT sequence_id, chunk_type, markdown, heading_path FROM chunks WHERE document_id = :d ORDER BY sequence_id"), {"d": doc})).all()
    assert got[0][1:] == ("heading", "# Chapter 1: Memory", ["Chapter 1: Memory"])
    assert got[1][1:3] == ("text", "* * *")
    assert got[2][3] == ["Chapter 1: Memory"]
    assert got[3][3] == ["Chapter 1: Memory", "Types of Memory"]
