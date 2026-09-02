"""The reading-progress ceiling: retrieval must never return material from
further on in a document than the reader has actually read.

The bug this exists for is not a crash — it is the app spoiling a book. A
reader part-way through The Culture Map asked what was going on and got an
answer built from chunks they had never seen, including how the argument
ends. Every retrieval path has to respect the ceiling, because the router
picks between them per question and the reader has no idea which one ran:

    LOCAL     -> get_chunk_window, whose window is CENTRED on the current
                 chunk and therefore reaches forward by default
    GLOBAL    -> both legs of the hybrid search (vector and full-text)
    OVERVIEW  -> pre-computed whole-document summaries, the worst offender:
                 a level-0 summary of a whole book IS the ending

`max_sequence_id=None` means "no ceiling" and must preserve the old
behaviour exactly — that is what a finished paper or an imported article
wants, and every existing caller passes nothing.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.chat.overview_context import build_overview_context
from app.database.pgvector import search_chunks_fulltext
from app.database.repositories import chunks as chunk_repo
from app.database.repositories import documents as doc_repo


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _add_chunk(db_session, document_id: str, seq: int, body: str):
    await db_session.execute(
        text("""
            INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text)
            VALUES (:document_id, :seq, 'text', :body, :body)
        """),
        {"document_id": document_id, "seq": seq, "body": body},
    )


async def _add_summary(db_session, document_id: str, level: int, start: int, end: int, body: str):
    await db_session.execute(
        text("""
            INSERT INTO section_summaries
                (document_id, section_id, level, heading_path, sequence_start,
                 sequence_end, summary_markdown, summary_plain,
                 source_chunk_ids, model, prompt_hash)
            VALUES (:doc, :sid, :level, ARRAY[:sid], :start, :end, :body, :body,
                    ARRAY[]::uuid[], 'test-model', 'test-hash')
        """),
        {"doc": document_id, "sid": f"s{level}-{start}", "level": level,
         "start": start, "end": end, "body": body},
    )


async def _book(db_session):
    """A 10-chunk book whose last chunk is the ending."""
    user = await _make_user(db_session)
    doc = await doc_repo.create_document(
        db_session, user_id=user, filename="b.pdf", original_filename="b.pdf"
    )
    doc_id = doc["id"]
    for seq in range(1, 10):
        await _add_chunk(db_session, doc_id, seq, f"chapter body {seq}")
    await _add_chunk(db_session, doc_id, 10, "the butler did it")
    await db_session.commit()
    return doc_id


# ── LOCAL: the centred window reaches forward ────────────────────────────

@pytest.mark.asyncio
async def test_chunk_window_is_clamped_to_the_ceiling(db_session):
    doc_id = await _book(db_session)
    rows = await chunk_repo.get_chunk_window(
        db_session, doc_id, center_sequence_id=5, window_size=3, max_sequence_id=5
    )
    seqs = [r["sequence_id"] for r in rows]
    assert seqs == [2, 3, 4, 5], seqs
    assert max(seqs) <= 5


@pytest.mark.asyncio
async def test_chunk_window_without_a_ceiling_is_unchanged(db_session):
    """Every existing caller passes nothing; they must keep the full window."""
    doc_id = await _book(db_session)
    rows = await chunk_repo.get_chunk_window(
        db_session, doc_id, center_sequence_id=5, window_size=3
    )
    assert [r["sequence_id"] for r in rows] == [2, 3, 4, 5, 6, 7, 8]


# ── GLOBAL: the full-text leg ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fulltext_search_cannot_return_the_ending(db_session):
    doc_id = await _book(db_session)
    # Without a ceiling the ending is reachable...
    unbounded = await search_chunks_fulltext(db_session, "butler", limit=10, document_id=doc_id)
    assert any(r["sequence_id"] == 10 for r in unbounded)

    # ...and with one it is not, even though it is the best match.
    bounded = await search_chunks_fulltext(
        db_session, "butler", limit=10, document_id=doc_id, max_sequence_id=5
    )
    assert bounded == [] or all(r["sequence_id"] <= 5 for r in bounded)


@pytest.mark.asyncio
async def test_fulltext_still_returns_what_has_been_read(db_session):
    """The ceiling must not gut retrieval — material behind it still comes back."""
    doc_id = await _book(db_session)
    rows = await search_chunks_fulltext(
        db_session, "chapter", limit=10, document_id=doc_id, max_sequence_id=4
    )
    assert rows, "clamping removed everything; the ceiling is too aggressive"
    assert all(r["sequence_id"] <= 4 for r in rows)


# ── OVERVIEW: pre-computed whole-document summaries ──────────────────────

@pytest.mark.asyncio
async def test_overview_drops_sections_beyond_the_ceiling(db_session):
    doc_id = await _book(db_session)
    await _add_summary(db_session, doc_id, 1, 1, 3, "early section")
    await _add_summary(db_session, doc_id, 1, 7, 10, "the ending explained")
    await db_session.commit()

    ctx = await build_overview_context(db_session, document_id=doc_id, max_sequence_id=5)
    bodies = [s["summary_plain"] for s in ctx["section_summaries"]]
    assert "early section" in bodies
    assert "the ending explained" not in bodies


@pytest.mark.asyncio
async def test_overview_withholds_the_whole_document_summary_until_the_end(db_session):
    """A level-0 summary covers the entire book, so handing it to a reader on
    page 40 is the spoiler no matter how it is worded."""
    doc_id = await _book(db_session)
    await _add_summary(db_session, doc_id, 0, 1, 10, "the whole book, including the ending")
    await db_session.commit()

    midway = await build_overview_context(db_session, document_id=doc_id, max_sequence_id=5)
    assert midway["paper_overview"] is None

    finished = await build_overview_context(db_session, document_id=doc_id, max_sequence_id=10)
    assert finished["paper_overview"] is not None


@pytest.mark.asyncio
async def test_overview_without_a_ceiling_returns_everything(db_session):
    doc_id = await _book(db_session)
    await _add_summary(db_session, doc_id, 0, 1, 10, "whole book")
    await _add_summary(db_session, doc_id, 1, 7, 10, "the ending explained")
    await db_session.commit()

    ctx = await build_overview_context(db_session, document_id=doc_id)
    assert ctx["paper_overview"] is not None
    assert any(s["summary_plain"] == "the ending explained" for s in ctx["section_summaries"])


@pytest.mark.asyncio
async def test_a_partially_read_section_is_marked_not_dropped(db_session):
    """The reader has started this section, so it stays — but the formatter
    needs to know it is not fully read."""
    doc_id = await _book(db_session)
    await _add_summary(db_session, doc_id, 1, 4, 9, "section spanning the ceiling")
    await db_session.commit()

    ctx = await build_overview_context(db_session, document_id=doc_id, max_sequence_id=6)
    section = ctx["section_summaries"][0]
    assert section["summary_plain"] == "section spanning the ceiling"
    assert section.get("partially_read") is True


# ── The book path, which is NOT the path clamped above ───────────────────
#
# The first version of this ceiling clamped LOCAL/GLOBAL/OVERVIEW and was
# still completely ineffective for a book, because a book question never
# reaches any of them: orchestrator's stream gate sees doc_kind == "book"
# and returns early into the paper agent, which navigates with
# SECTION / SEARCH / READ over the document's own contents index.
#
# The agent derives the contents index, the anchor window and everything
# SECTION/READ can resolve from one list of chunks, so filtering that list
# bounds all three. SEARCH is the exception: it queries the database, so it
# carries the ceiling separately — which is what these cover.

from app.chat.agent_tools import run_search  # noqa: E402
from app.database.repositories import chunks as chunks_repo  # noqa: E402


@pytest.mark.asyncio
async def test_substring_search_respects_the_ceiling(db_session):
    doc_id = await _book(db_session)
    unbounded = await chunks_repo.search_chunks_substring(db_session, doc_id, "butler", 10)
    assert any(r["sequence_id"] == 10 for r in unbounded)

    bounded = await chunks_repo.search_chunks_substring(
        db_session, doc_id, "butler", 10, max_sequence_id=5
    )
    assert bounded == []


@pytest.mark.asyncio
async def test_agent_search_tool_respects_the_ceiling(db_session):
    """The agent's SEARCH tool is how a book question actually reaches the
    text, so this is the one that matters for the reported bug."""
    doc_id = await _book(db_session)
    hits = await run_search(db_session, doc_id, "butler", max_sequence_id=5)
    assert all(h["sequence_id"] <= 5 for h in hits)
    assert not any("butler" in (h.get("plain_text") or "") for h in hits)


@pytest.mark.asyncio
async def test_agent_search_still_finds_what_has_been_read(db_session):
    doc_id = await _book(db_session)
    hits = await run_search(db_session, doc_id, "chapter", max_sequence_id=4)
    assert hits, "the ceiling removed everything; retrieval is over-clamped"
    assert all(h["sequence_id"] <= 4 for h in hits)


@pytest.mark.asyncio
async def test_the_desk_is_never_clamped(db_session):
    """The product rule, encoded so it cannot regress by accident.

    The desk is the opposite surface from the reader: everything added to a
    study is deliberately fully available to the assistant so it can compare
    and link across documents. run_search's multi-document scope IS the desk,
    and it must ignore a ceiling even if one is somehow passed.
    """
    doc_id = await _book(db_session)
    hits = await run_search(db_session, [doc_id], "butler", max_sequence_id=1)
    assert any(h["sequence_id"] == 10 for h in hits), (
        "the desk was clamped by a reading ceiling; the study agent must keep "
        "full access to every document in its scope"
    )
