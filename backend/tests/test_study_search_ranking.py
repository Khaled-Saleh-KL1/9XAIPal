"""The Desk's SEARCH must return the BEST matches across a study, not the
ones whose paper happens to have a low UUID.

`run_search` merges two already-ranked legs — full-text (ts_rank DESC) first,
then substring — de-duplicates, and hands the model `limit` blocks as its
evidence for the answer.

It used to sort the merged list by `(document_id, sequence_id)` *before*
truncating. That sort is deliberate and still applied: hits from one paper
reading together beats a rank-interleaved list that looks like one document
changing subject every line (see search_chunks_substring_multi's docstring).
But applied before the slice it stopped being presentation and became
selection — the survivors were chosen by document UUID, which is `uuid4()`.
With study_max_papers=24 and paper_agent_search_limit=8, papers sorting low
took every slot and the top-ranked full-text hits were dropped entirely.

The ids below are fixed rather than random precisely because the bug was
invisible under random ones: it reproduced only when the paper holding the
real matches sorted after the paper holding the weak ones.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.chat.agent_tools import run_search

# Sorts LAST as a string — holds the genuine full-text matches.
DOC_HIGH = UUID("ffffffff-0000-4000-8000-000000000001")
# Sorts FIRST — holds only weak substring matches.
DOC_LOW = UUID("00000000-0000-4000-8000-000000000001")


async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:email, 'x') RETURNING id"),
        {"email": f"{uuid4()}@test.local"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _make_doc(db_session, user_id, doc_id: UUID) -> None:
    await db_session.execute(
        text("""
            INSERT INTO documents (id, user_id, filename, original_filename, status)
            VALUES (:id, :uid, :fn, :fn, 'complete')
        """),
        {"id": doc_id, "uid": user_id, "fn": f"{doc_id}.pdf"},
    )


async def _add_chunk(db_session, document_id: UUID, seq: int, body: str) -> None:
    await db_session.execute(
        text("""
            INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text)
            VALUES (:d, :s, 'text', :b, :b)
        """),
        {"d": document_id, "s": seq, "b": body},
    )


@pytest.fixture
async def study(db_session):
    """Two papers. The low-UUID one mentions the term only inside a larger
    word — `to_tsvector` makes "xtransformerx" its own lexeme, so full-text
    never matches it while ILIKE '%transformer%' does. The high-UUID one uses
    the real word, so it is what full-text ranks and what the reader means."""
    user_id = await _make_user(db_session)
    await _make_doc(db_session, user_id, DOC_LOW)
    await _make_doc(db_session, user_id, DOC_HIGH)

    for seq in range(1, 7):
        await _add_chunk(
            db_session, DOC_LOW, seq,
            f"An unrelated aside about xtransformerx hardware, note {seq}.",
        )
    for seq in range(1, 7):
        await _add_chunk(
            db_session, DOC_HIGH, seq,
            f"The transformer architecture and its attention mechanism, part {seq}. "
            "transformer transformer",
        )
    await db_session.commit()
    return [DOC_LOW, DOC_HIGH]


@pytest.mark.asyncio
async def test_the_best_matches_survive_truncation_not_the_lowest_uuid(study, db_session):
    """The regression. Before the fix this returned six blocks from DOC_LOW
    and none from DOC_HIGH — the paper that actually answers the question."""
    hits = await run_search(db_session, study, "transformer", limit=4)

    assert len(hits) == 4
    from_high = [h for h in hits if h["document_id"] == DOC_HIGH]
    assert from_high, "the paper holding the real full-text matches was dropped entirely"
    assert len(from_high) == 4


@pytest.mark.asyncio
async def test_hits_are_still_grouped_by_paper(study, db_session):
    """The grouping is the reason the sort exists at all, so it must survive
    the fix: a caller reading the formatted result must not see the papers
    interleaved."""
    hits = await run_search(db_session, study, "transformer", limit=8)

    order = [str(h["document_id"]) for h in hits]
    assert order == sorted(order), "hits from one paper must stay contiguous"
    # And within a paper, in reading order.
    per_doc: dict = {}
    for h in hits:
        per_doc.setdefault(h["document_id"], []).append(h["sequence_id"])
    for seqs in per_doc.values():
        assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_a_generous_limit_still_reaches_both_papers(study, db_session):
    """With room for everything, neither paper is excluded — this is what
    made the bug hard to see: it only bites once the limit binds."""
    hits = await run_search(db_session, study, "transformer", limit=20)
    assert {h["document_id"] for h in hits} == {DOC_LOW, DOC_HIGH}
