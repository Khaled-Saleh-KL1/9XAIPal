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

async def list_studies(session: AsyncSession, user_id: UUID) -> list[dict]:
    """Every study this user owns, newest first, each with how many papers it holds."""
    result = await session.execute(
        text("""
            SELECT s.*, COUNT(sp.document_id) AS paper_count
            FROM studies s
            LEFT JOIN study_papers sp ON sp.study_id = s.id
            WHERE s.user_id = :user_id
            GROUP BY s.id
            ORDER BY s.created_at DESC
        """),
        {"user_id": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_study(session: AsyncSession, study_id: UUID, user_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM studies WHERE id = :id AND user_id = :user_id"),
        {"id": study_id, "user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_study(
    session: AsyncSession, *, user_id: UUID, name: str, description: Optional[str] = None
) -> dict:
    result = await session.execute(
        text("""
            INSERT INTO studies (user_id, name, description)
            VALUES (:user_id, :name, :description)
            RETURNING *
        """),
        {"user_id": user_id, "name": name, "description": description},
    )
    return dict(result.mappings().one())


async def update_study(
    session: AsyncSession,
    study_id: UUID,
    user_id: UUID,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[dict]:
    """Patch a study. Omitted fields are left alone, not cleared."""
    sets = ["updated_at = NOW()"]
    params: dict = {"id": study_id, "user_id": user_id}
    if name is not None:
        sets.append("name = :name")
        params["name"] = name
    if description is not None:
        sets.append("description = :description")
        params["description"] = description
    result = await session.execute(
        text(f"UPDATE studies SET {', '.join(sets)} WHERE id = :id AND user_id = :user_id RETURNING *"),
        params,
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def delete_study(session: AsyncSession, study_id: UUID, user_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM studies WHERE id = :id AND user_id = :user_id"),
        {"id": study_id, "user_id": user_id},
    )
    return (result.rowcount or 0) > 0


# ── Membership ──────────────────────────────────────────────────────────────
#
# Not independently user-scoped: the study itself is verified as owned once,
# at the endpoint boundary (get_study / a 404 before these are ever called),
# so re-filtering every membership query would just repeat that check. What
# these queries must still guard against — enforced by the endpoint layer via
# documents.filter_owned_document_ids — is a caller naming a document_id that
# belongs to someone else.

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

    Whole-collection, like note decks. ``document_ids`` must already be
    filtered to ones the caller owns (documents.filter_owned_document_ids at
    the endpoint layer) — this function trusts its input.
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


async def studies_for_paper(session: AsyncSession, document_id: UUID, user_id: UUID) -> list[dict]:
    """Every study this user owns that this paper belongs to. Used to preselect a scope."""
    result = await session.execute(
        text("""
            SELECT s.*
            FROM studies s
            JOIN study_papers sp ON sp.study_id = s.id
            WHERE sp.document_id = :document_id AND s.user_id = :user_id
            ORDER BY s.created_at DESC
        """),
        {"document_id": document_id, "user_id": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


# ── The chat ────────────────────────────────────────────────────────────────
#
# Turns live in conversation_turns, the table the book chat already uses. A
# desk turn is the same artifact — a rolling transcript with a model and
# citations — and giving it its own table would duplicate every column and
# split the one place chat history is read from.
#
# user_id is required here directly (not derived from study_id) because the
# pure library-wide chat has study_id IS NULL — no parent row to derive
# ownership from transitively.

async def list_turns(session: AsyncSession, user_id: UUID, study_id: Optional[UUID]) -> list[dict]:
    """The scope's transcript, oldest first.

    ⚠ ``IS NOT DISTINCT FROM`` rather than ``=``: the library-wide chat is
    keyed by NULL, and ``study_id = NULL`` matches no rows at all.
    """
    result = await session.execute(
        text("""
            SELECT * FROM conversation_turns
            WHERE user_id = :user_id
              AND study_id IS NOT DISTINCT FROM :study_id
              AND parent_turn_id IS NULL
            ORDER BY created_at ASC
        """),
        {"user_id": user_id, "study_id": study_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def add_turn(
    session: AsyncSession,
    *,
    user_id: UUID,
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
                (user_id, conversation_id, study_id, role, content, model,
                 citations, agent_steps, context_type)
            VALUES
                (:user_id, :conversation_id, :study_id, :role, :content, :model,
                 CAST(:citations AS jsonb), CAST(:agent_steps AS jsonb), 'STUDY')
            RETURNING *
        """),
        {
            "user_id": user_id,
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
    session: AsyncSession, user_id: UUID, study_id: Optional[UUID]
) -> Optional[UUID]:
    """The conversation this scope is already using, if it has one."""
    result = await session.execute(
        text("""
            SELECT conversation_id FROM conversation_turns
            WHERE user_id = :user_id AND study_id IS NOT DISTINCT FROM :study_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"user_id": user_id, "study_id": study_id},
    )
    row = result.mappings().first()
    return row["conversation_id"] if row else None


async def clear_turns(session: AsyncSession, user_id: UUID, study_id: Optional[UUID]) -> int:
    result = await session.execute(
        text("""
            DELETE FROM conversation_turns
            WHERE user_id = :user_id AND study_id IS NOT DISTINCT FROM :study_id
        """),
        {"user_id": user_id, "study_id": study_id},
    )
    return result.rowcount or 0


async def delete_turn(session: AsyncSession, turn_id: UUID, user_id: UUID) -> bool:
    result = await session.execute(
        text("DELETE FROM conversation_turns WHERE id = :id AND user_id = :user_id"),
        {"id": turn_id, "user_id": user_id},
    )
    return (result.rowcount or 0) > 0
