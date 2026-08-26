"""Studies: named groups of papers that scope an answer, and their chats.

A study is the unit of "what may this question be answered from". It is
deliberately not a folder — a paper can belong to several studies at once, and
removing it from one takes nothing away from the library or from the others.

⚠ **`study_id IS NULL` on a conversation turn is a scope, not a missing value.**
It means the library-wide chat, which sees every paper. Code that treats NULL
here as "unassigned" and tries to repair it will delete the reader's main
conversation.
"""

import json
from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Studies ─────────────────────────────────────────────────────────────────

async def list_studies(session: AsyncSession) -> list[dict]:
    """Every study, newest first, each with how many papers it holds."""
    result = await session.execute(
        text("""
            SELECT s.*, COUNT(sp.document_id) AS paper_count
            FROM studies s
            LEFT JOIN study_papers sp ON sp.study_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """)
    )
    return [dict(r) for r in result.mappings().all()]


async def get_study(session: AsyncSession, study_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM studies WHERE id = :id"), {"id": study_id}
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_study(
    session: AsyncSession, *, name: str, description: Optional[str] = None
) -> dict:
    result = await session.execute(
        text("""
            INSERT INTO studies (name, description)
            VALUES (:name, :description)
            RETURNING *
        """),
        {"name": name, "description": description},
    )
    return dict(result.mappings().one())


async def update_study(
    session: AsyncSession,
    study_id: UUID,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[dict]:
    """Patch a study. Omitted fields are left alone, not cleared."""
    sets = ["updated_at = NOW()"]
    params: dict = {"id": study_id}
    if name is not None:
        sets.append("name = :name")
        params["name"] = name
    if description is not None:
        sets.append("description = :description")
        params["description"] = description
    result = await session.execute(
        text(f"UPDATE studies SET {', '.join(sets)} WHERE id = :id RETURNING *"),
        params,
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_study(session: AsyncSession, study_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM studies WHERE id = :id"), {"id": study_id}
    )
    return (result.rowcount or 0) > 0


# ── Membership ──────────────────────────────────────────────────────────────

async def list_study_papers(session: AsyncSession, study_id: UUID) -> list[dict]:
    """The study's papers in citation order, joined to their document rows.

    ⚠ Ordered by ``position``, never by title or date. The agent cites papers
    as P1/P2/P3 by their index in this list, so a re-ordering that looks
    cosmetic silently repoints every citation the reader has already read.
    """
    result = await session.execute(
        text("""
            SELECT d.*, sp.position
            FROM study_papers sp
            JOIN documents d ON d.id = sp.document_id
            WHERE sp.study_id = :study_id
            ORDER BY sp.position ASC, sp.added_at ASC
        """),
        {"study_id": study_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def set_study_papers(
    session: AsyncSession, study_id: UUID, document_ids: list[UUID]
) -> list[dict]:
    """Replace the study's membership wholesale.

    Whole-collection, like note decks: the client sends the list it wants, and
    ``position`` comes from the list order. Add/remove calls would need the
    client to reconcile ordering across two round trips, and a dropped request
    would leave a study whose citation numbering no longer matches what the
    reader is looking at.
    """
    await session.execute(
        text("DELETE FROM study_papers WHERE study_id = :study_id"),
        {"study_id": study_id},
    )
    for position, document_id in enumerate(document_ids):
        await session.execute(
            text("""
                INSERT INTO study_papers (study_id, document_id, position)
                VALUES (:study_id, :document_id, :position)
                ON CONFLICT (study_id, document_id) DO UPDATE
                    SET position = EXCLUDED.position
            """),
            {"study_id": study_id, "document_id": document_id, "position": position},
        )
    await session.execute(
        text("UPDATE studies SET updated_at = NOW() WHERE id = :id"), {"id": study_id}
    )
    return await list_study_papers(session, study_id)


async def studies_for_paper(session: AsyncSession, document_id: UUID) -> list[dict]:
    """Every study this paper belongs to. Used to preselect a scope."""
    result = await session.execute(
        text("""
            SELECT s.*
            FROM studies s
            JOIN study_papers sp ON sp.study_id = s.id
            WHERE sp.document_id = :document_id
            ORDER BY s.created_at DESC
        """),
        {"document_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


# ── The chat ────────────────────────────────────────────────────────────────
#
# Turns live in conversation_turns, the table the book chat already uses. A
# desk turn is the same artifact — a rolling transcript with a model and
# citations — and giving it its own table would duplicate every column and
# split the one place chat history is read from.

async def list_turns(session: AsyncSession, study_id: Optional[UUID]) -> list[dict]:
    """The scope's transcript, oldest first.

    ⚠ ``IS NOT DISTINCT FROM`` rather than ``=``: the library-wide chat is
    keyed by NULL, and ``study_id = NULL`` matches no rows at all.
    """
    result = await session.execute(
        text("""
            SELECT * FROM conversation_turns
            WHERE study_id IS NOT DISTINCT FROM :study_id
              AND parent_turn_id IS NULL
            ORDER BY created_at ASC
        """),
        {"study_id": study_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def add_turn(
    session: AsyncSession,
    *,
    study_id: Optional[UUID],
    conversation_id: UUID,
    role: str,
    content: str,
    model: Optional[str] = None,
    citations: Optional[list[dict]] = None,
    agent_steps: Optional[list[dict]] = None,
) -> dict:
    result = await session.execute(
        text("""
            INSERT INTO conversation_turns
                (conversation_id, study_id, role, content, model,
                 citations, agent_steps, context_type)
            VALUES
                (:conversation_id, :study_id, :role, :content, :model,
                 CAST(:citations AS jsonb), CAST(:agent_steps AS jsonb), 'STUDY')
            RETURNING *
        """),
        {
            "conversation_id": conversation_id,
            "study_id": study_id,
            "role": role,
            "content": content,
            "model": model,
            "citations": json.dumps(citations) if citations else None,
            "agent_steps": json.dumps(agent_steps) if agent_steps else None,
        },
    )
    return dict(result.mappings().one())


async def latest_conversation_id(
    session: AsyncSession, study_id: Optional[UUID]
) -> Optional[UUID]:
    """The conversation this scope is already using, if it has one."""
    result = await session.execute(
        text("""
            SELECT conversation_id FROM conversation_turns
            WHERE study_id IS NOT DISTINCT FROM :study_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"study_id": study_id},
    )
    row = result.mappings().first()
    return row["conversation_id"] if row else None


async def clear_turns(session: AsyncSession, study_id: Optional[UUID]) -> int:
    result = await session.execute(
        text("""
            DELETE FROM conversation_turns
            WHERE study_id IS NOT DISTINCT FROM :study_id
        """),
        {"study_id": study_id},
    )
    return result.rowcount or 0


async def delete_turn(session: AsyncSession, turn_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM conversation_turns WHERE id = :id"), {"id": turn_id}
    )
    return (result.rowcount or 0) > 0
