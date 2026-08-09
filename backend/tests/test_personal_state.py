"""Personal reading state: bookmarks, the reader's own notes, and decks.

These cover the invariants that moved from the browser into the database when
this state stopped living in localStorage:

- one bookmark per block, so re-marking a place updates rather than duplicates
- a personal note anchors by sequence id and survives its chunk going away
- a card belongs to at most one deck, enforced by a unique index rather than
  by trusting the client
- a deck of fewer than two cards is not a deck and is pruned
- deleting a note takes it out of its deck automatically, via the cascade
"""

import pytest
from uuid import uuid4

from sqlalchemy import text

from app.database.repositories import personal as personal_repo


async def _document(db_session) -> str:
    doc_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'paper.pdf', 'paper.pdf', 'complete')"
        ),
        {"id": doc_id},
    )
    await db_session.commit()
    return doc_id


async def _ai_note(db_session, doc_id, sequence_id: int = 1) -> str:
    result = await db_session.execute(
        text("""
            INSERT INTO paper_notes (document_id, anchor_sequence_id, question)
            VALUES (:d, :seq, 'why?')
            RETURNING id
        """),
        {"d": doc_id, "seq": sequence_id},
    )
    await db_session.commit()
    return result.scalar_one()


# ── Bookmarks ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bookmarking_the_same_block_twice_updates_in_place(db_session):
    """The UI treats a second press as 'refresh this mark', never 'add another'."""
    doc_id = await _document(db_session)

    first = await personal_repo.upsert_bookmark(
        db_session, document_id=doc_id, sequence_id=7, snippet="first", page=2
    )
    second = await personal_repo.upsert_bookmark(
        db_session, document_id=doc_id, sequence_id=7, snippet="second", page=3
    )
    await db_session.commit()

    assert first["id"] == second["id"]
    assert second["snippet"] == "second"
    assert second["page"] == 3

    rows = await personal_repo.list_bookmarks(db_session, doc_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_bookmarks_come_back_in_reading_order(db_session):
    doc_id = await _document(db_session)
    for seq in (14, 3, 9):
        await personal_repo.upsert_bookmark(db_session, document_id=doc_id, sequence_id=seq)
    await db_session.commit()

    rows = await personal_repo.list_bookmarks(db_session, doc_id)
    assert [r["sequence_id"] for r in rows] == [3, 9, 14]


@pytest.mark.asyncio
async def test_upsert_keeps_an_existing_label_when_none_is_supplied(db_session):
    """Re-bookmarking from the article must not wipe a name set in the panel."""
    doc_id = await _document(db_session)
    row = await personal_repo.upsert_bookmark(
        db_session, document_id=doc_id, sequence_id=4, label="The result table"
    )
    await personal_repo.upsert_bookmark(
        db_session, document_id=doc_id, sequence_id=4, snippet="refreshed"
    )
    await db_session.commit()

    again = await personal_repo.get_bookmark(db_session, row["id"])
    assert again["label"] == "The result table"
    assert again["snippet"] == "refreshed"


# ── Personal notes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_personal_note_round_trips_and_patches_independently(db_session):
    doc_id = await _document(db_session)
    note = await personal_repo.create_personal_note(
        db_session,
        document_id=doc_id,
        anchor_sequence_id=12,
        body="check this against the appendix",
        anchor_quote="multi-head attention",
        margin_side="left",
    )
    await db_session.commit()

    # Editing the text must not disturb which margin the card sits in.
    edited = await personal_repo.update_personal_note(
        db_session, note["id"], body="checked — it holds"
    )
    assert edited["body"] == "checked — it holds"
    assert edited["margin_side"] == "left"

    # And moving it must not disturb the text.
    moved = await personal_repo.update_personal_note(
        db_session, note["id"], margin_side="right"
    )
    assert moved["margin_side"] == "right"
    assert moved["body"] == "checked — it holds"
    assert moved["anchor_quote"] == "multi-head attention"


@pytest.mark.asyncio
async def test_personal_notes_are_ordered_by_anchor(db_session):
    doc_id = await _document(db_session)
    for seq in (20, 2, 11):
        await personal_repo.create_personal_note(
            db_session, document_id=doc_id, anchor_sequence_id=seq, body=f"note {seq}"
        )
    await db_session.commit()

    rows = await personal_repo.list_personal_notes(db_session, doc_id)
    assert [r["anchor_sequence_id"] for r in rows] == [2, 11, 20]


# ── Decks ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replace_decks_round_trips_members_in_order(db_session):
    doc_id = await _document(db_session)
    ai = await _ai_note(db_session, doc_id, 3)
    personal = await personal_repo.create_personal_note(
        db_session, document_id=doc_id, anchor_sequence_id=5, body="mine"
    )
    await db_session.commit()

    deck_id = uuid4()
    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{
            "id": deck_id,
            "label": "Attention",
            "top_index": 1,
            "margin_side": "left",
            "study": True,
            "members": [
                {"kind": "ai", "id": ai},
                {"kind": "personal", "id": personal["id"]},
            ],
        }],
    )
    await db_session.commit()

    decks = await personal_repo.list_decks(db_session, doc_id)
    assert len(decks) == 1
    assert decks[0]["label"] == "Attention"
    assert decks[0]["top_index"] == 1
    assert decks[0]["margin_side"] == "left"
    assert decks[0]["study"] is True
    assert [m["kind"] for m in decks[0]["members"]] == ["ai", "personal"]
    assert decks[0]["members"][0]["id"] == ai


@pytest.mark.asyncio
async def test_two_cards_can_swap_decks_in_one_write(db_session):
    """The regression this guards: the unique index that stops a card being in
    two decks does not care that the row it collides with is one the same
    statement is about to delete. Swapping members between two decks fails
    unless membership is cleared for the whole document first."""
    doc_id = await _document(db_session)
    a, b, c, d = [await _ai_note(db_session, doc_id, i) for i in (1, 2, 3, 4)]
    one, two = uuid4(), uuid4()

    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [
            {"id": one, "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": b}]},
            {"id": two, "members": [{"kind": "ai", "id": c}, {"kind": "ai", "id": d}]},
        ],
    )
    await db_session.commit()

    # b and c trade places.
    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [
            {"id": one, "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": c}]},
            {"id": two, "members": [{"kind": "ai", "id": b}, {"kind": "ai", "id": d}]},
        ],
    )
    await db_session.commit()

    decks = {deck["id"]: deck for deck in await personal_repo.list_decks(db_session, doc_id)}
    assert [m["id"] for m in decks[one]["members"]] == [a, c]
    assert [m["id"] for m in decks[two]["members"]] == [b, d]


@pytest.mark.asyncio
async def test_a_card_listed_in_two_decks_is_kept_by_the_first(db_session):
    """A client round-off must not reach the unique index as a 500. The first
    deck to claim a card keeps it, and a deck left holding one is dropped."""
    doc_id = await _document(db_session)
    a, b, c = [await _ai_note(db_session, doc_id, i) for i in (1, 2, 3)]
    one, two = uuid4(), uuid4()

    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [
            {"id": one, "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": b}]},
            {"id": two, "members": [{"kind": "ai", "id": c}, {"kind": "ai", "id": a}]},
        ],
    )
    await db_session.commit()

    decks = await personal_repo.list_decks(db_session, doc_id)
    assert len(decks) == 1
    assert decks[0]["id"] == one
    assert [m["id"] for m in decks[0]["members"]] == [a, b]


@pytest.mark.asyncio
async def test_top_index_is_clamped_to_the_members_that_survived(db_session):
    doc_id = await _document(db_session)
    a, b = [await _ai_note(db_session, doc_id, i) for i in (1, 2)]

    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{
            "id": uuid4(),
            "top_index": 9,
            "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": b}],
        }],
    )
    await db_session.commit()

    decks = await personal_repo.list_decks(db_session, doc_id)
    assert decks[0]["top_index"] == 1


@pytest.mark.asyncio
async def test_deleting_a_note_removes_it_from_its_deck(db_session):
    """The cascade is the point of two real foreign keys rather than one
    polymorphic id: the client never has to notice a dangling member."""
    doc_id = await _document(db_session)
    a = await _ai_note(db_session, doc_id, 1)
    b = await _ai_note(db_session, doc_id, 2)
    c = await _ai_note(db_session, doc_id, 3)

    deck_id = uuid4()
    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{
            "id": deck_id,
            "members": [
                {"kind": "ai", "id": a},
                {"kind": "ai", "id": b},
                {"kind": "ai", "id": c},
            ],
        }],
    )
    await db_session.commit()

    await db_session.execute(text("DELETE FROM paper_notes WHERE id = :id"), {"id": b})
    await db_session.commit()

    decks = await personal_repo.list_decks(db_session, doc_id)
    assert [m["id"] for m in decks[0]["members"]] == [a, c]


@pytest.mark.asyncio
async def test_a_deck_reduced_to_one_card_is_pruned(db_session):
    doc_id = await _document(db_session)
    a = await _ai_note(db_session, doc_id, 1)
    b = await _ai_note(db_session, doc_id, 2)

    deck_id = uuid4()
    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{"id": deck_id, "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": b}]}],
    )
    await db_session.commit()

    await db_session.execute(text("DELETE FROM paper_notes WHERE id = :id"), {"id": b})
    await db_session.commit()

    removed = await personal_repo.prune_thin_decks(db_session, doc_id)
    await db_session.commit()
    assert removed == 1
    assert await personal_repo.list_decks(db_session, doc_id) == []


@pytest.mark.asyncio
async def test_replacing_with_an_empty_arrangement_clears_every_deck(db_session):
    """Spreading the last deck has to actually delete it, not leave an orphan."""
    doc_id = await _document(db_session)
    a = await _ai_note(db_session, doc_id, 1)
    b = await _ai_note(db_session, doc_id, 2)

    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{"id": uuid4(), "members": [{"kind": "ai", "id": a}, {"kind": "ai", "id": b}]}],
    )
    await db_session.commit()

    await personal_repo.replace_decks(db_session, doc_id, [])
    await db_session.commit()

    assert await personal_repo.list_decks(db_session, doc_id) == []
    # The notes themselves are untouched — a deck owns nothing.
    remaining = await db_session.execute(
        text("SELECT COUNT(*) FROM paper_notes WHERE document_id = :d"), {"d": doc_id}
    )
    assert remaining.scalar_one() == 2


@pytest.mark.asyncio
async def test_owned_note_ids_scopes_to_the_document(db_session):
    """Deck members from another paper must not survive validation."""
    mine = await _document(db_session)
    theirs = await _document(db_session)

    a = await _ai_note(db_session, mine, 1)
    b = await _ai_note(db_session, theirs, 1)
    p = await personal_repo.create_personal_note(
        db_session, document_id=mine, anchor_sequence_id=2, body="mine"
    )
    await db_session.commit()

    ai_ids, personal_ids = await personal_repo.owned_note_ids(db_session, mine)
    assert a in ai_ids
    assert b not in ai_ids
    assert p["id"] in personal_ids


@pytest.mark.asyncio
async def test_personal_state_is_removed_with_its_document(db_session):
    doc_id = await _document(db_session)
    a = await _ai_note(db_session, doc_id, 1)
    p = await personal_repo.create_personal_note(
        db_session, document_id=doc_id, anchor_sequence_id=2, body="mine"
    )
    await personal_repo.upsert_bookmark(db_session, document_id=doc_id, sequence_id=3)
    await personal_repo.replace_decks(
        db_session,
        doc_id,
        [{
            "id": uuid4(),
            "members": [{"kind": "ai", "id": a}, {"kind": "personal", "id": p["id"]}],
        }],
    )
    await db_session.commit()

    await db_session.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_id})
    await db_session.commit()

    for table in ("reading_bookmarks", "personal_notes", "note_decks"):
        left = await db_session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE document_id = :d"), {"d": doc_id}
        )
        assert left.scalar_one() == 0, f"{table} kept rows after the document went"
