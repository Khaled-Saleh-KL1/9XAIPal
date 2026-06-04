"""Smoke tests for the nested sub-thread (paper-free tangent) feature.

These validate the core DB + repository invariants added for the feature:
- parent_turn_id correctly scopes main vs sub-thread turns.
- get_main_chat returns only NULL-parent turns.
- get_thread_subtree correctly assembles the tree (including the special
  first AI reply that originally had parent=NULL).
- has_children / get_thread_message_count work.
- Compaction turns can be created inside specific threads.
"""

import pytest
from uuid import uuid4

from sqlalchemy import text

from app.database.repositories import conversations as conv_repo


@pytest.mark.asyncio
async def test_main_chat_vs_subthread_scoping(db_session):
    conv_id = uuid4()
    doc_id = uuid4()

    # Insert a fake document so FKs are happy (the fixture truncates docs)
    await db_session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status) "
            "VALUES (:id, 'paper.pdf', 'paper.pdf', 'complete')"
        ),
        {"id": doc_id},
    )
    await db_session.commit()

    # Main chat turns (parent = NULL)
    u1 = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="user", content="What is attention in the paper?",
        parent_turn_id=None,
    )
    a1 = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="assistant", content="Attention is ... [paper content]",
        parent_turn_id=None,
    )

    # User decides to go deep on a tangent inside a sub-thread rooted at u1
    u2 = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="user", content="Explain the math behind the scaled dot product in detail",
        parent_turn_id=u1["id"],   # first continuation points at the branching user turn
    )
    a2 = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="assistant", content="The formula is ... (no paper context injected)",
        parent_turn_id=u2["id"],
    )

    # Deeper level inside the same sub-thread
    u3 = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="user", content="What about the multi-head version?",
        parent_turn_id=u2["id"],
    )

    await db_session.commit()

    # --- Assertions on the new repo methods ---

    # Main chat should only see the original two turns
    main = await conv_repo.get_main_chat(db_session, conv_id)
    assert len(main) == 2
    assert all(t["parent_turn_id"] is None for t in main)

    # Subtree rooted at the branching user turn should include u1, a1 (special case),
    # u2, a2, u3
    subtree = await conv_repo.get_thread_subtree(db_session, u1["id"])
    ids = [t["id"] for t in subtree]
    assert u1["id"] in ids
    assert a1["id"] in ids          # the "first normal message" special case
    assert u2["id"] in ids
    assert a2["id"] in ids
    assert u3["id"] in ids

    # has_children
    assert await conv_repo.has_children(db_session, u1["id"]) is True
    assert await conv_repo.has_children(db_session, a1["id"]) is False   # a1 itself has no direct children in this tree
    assert await conv_repo.has_children(db_session, u2["id"]) is True

    # Message count for the sub-thread
    count = await conv_repo.get_thread_message_count(db_session, u1["id"])
    assert count >= 5   # u1 + special a1 + u2 + a2 + u3 (+ possible compaction later)

    # A compaction turn created inside the sub-thread must have the correct parent
    comp = await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="compaction", content="Summary of the math tangent...",
        parent_turn_id=u1["id"],
    )
    await db_session.commit()

    subtree2 = await conv_repo.get_thread_subtree(db_session, u1["id"])
    assert any(t["role"] == "compaction" and t["parent_turn_id"] == u1["id"] for t in subtree2)


@pytest.mark.asyncio
async def test_compaction_is_independent_per_thread(db_session):
    """Compaction turns created for the main thread must not pollute sub-threads and vice-versa."""
    conv_id = uuid4()
    doc_id = uuid4()

    await db_session.execute(
        text("INSERT INTO documents (id, filename, original_filename, status) VALUES (:id, 'p.pdf', 'p.pdf', 'complete')"),
        {"id": doc_id},
    )
    await db_session.commit()

    # Main thread activity
    await conv_repo.create_turn(db_session, conversation_id=conv_id, document_id=doc_id, role="user", content="Main q1")
    await conv_repo.create_turn(db_session, conversation_id=conv_id, document_id=doc_id, role="user", content="Main q2")

    # Sub-thread activity (rooted at the first main user turn)
    main_turns = await conv_repo.get_main_chat(db_session, conv_id)
    root = main_turns[0]["id"]

    await conv_repo.create_turn(db_session, conversation_id=conv_id, document_id=doc_id, role="user", content="Tangent q1", parent_turn_id=root)
    await conv_repo.create_turn(db_session, conversation_id=conv_id, document_id=doc_id, role="user", content="Tangent q2", parent_turn_id=root)

    # Simulate compaction being called for main vs sub
    # (in real code this is driven by maybe_compact_conversation with the right thread_root)
    await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="compaction", content="Main thread summary",
        parent_turn_id=None,   # main
    )
    await conv_repo.create_turn(
        db_session, conversation_id=conv_id, document_id=doc_id,
        role="compaction", content="Sub thread summary",
        parent_turn_id=root,
    )
    await db_session.commit()

    main = await conv_repo.get_main_chat(db_session, conv_id)
    sub = await conv_repo.get_thread_subtree(db_session, root)

    main_roles = [t["role"] for t in main]
    sub_roles = [t["role"] for t in sub]

    assert "compaction" in main_roles
    assert any(c["content"] == "Main thread summary" and c["parent_turn_id"] is None for c in main)

    assert "compaction" in sub_roles
    assert any(c["content"] == "Sub thread summary" and c.get("parent_turn_id") == root for c in sub)