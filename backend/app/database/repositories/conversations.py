"""Conversation repository: chat turns and ask traces."""

import json
from uuid import UUID
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_turn(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    document_id: Optional[UUID],
    role: str,
    content: str,
    context_type: Optional[str] = None,
    router_reason: Optional[str] = None,
    model: Optional[str] = None,
    citations: Optional[Any] = None,
    # NEW for nested sub-threads: NULL for main linear chat turns.
    # Non-NULL = this turn belongs to a sub-thread and is a reply to the given parent.
    parent_turn_id: Optional[UUID] = None,
) -> dict:
    """Record a conversation turn.

    ``citations`` may be a dict or a list (e.g. of citation dicts) and is
    serialized to JSON for the JSONB column.

    parent_turn_id: When provided, this turn belongs to a sub-thread rooted at
    that user turn (or chained inside the sub-thread). Main chat turns must
    always pass None so they remain visible in the primary linear history.
    """
    citations_payload = json.dumps(citations) if citations is not None else None
    stmt = text("""
        INSERT INTO conversation_turns
            (conversation_id, document_id, role, content, context_type, router_reason, model, citations, parent_turn_id)
        VALUES
            (:conversation_id, :document_id, :role, :content, :context_type, :router_reason, :model, CAST(:citations AS JSONB), :parent_turn_id)
        RETURNING id, conversation_id, role, created_at, parent_turn_id
    """)
    result = await session.execute(
        stmt,
        {
            "conversation_id": conversation_id,
            "document_id": document_id,
            "role": role,
            "content": content,
            "context_type": context_type,
            "router_reason": router_reason,
            "model": model,
            "citations": citations_payload,
            "parent_turn_id": parent_turn_id,
        },
    )
    return dict(result.mappings().one())


async def create_trace(
    session: AsyncSession,
    *,
    conversation_turn_id: UUID,
    context_type: str,
    router_reason: Optional[str] = None,
    retrieved_chunk_ids: Optional[list[UUID]] = None,
    model: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> dict:
    """Record an ask trace for debugging."""
    result = await session.execute(
        text("""
            INSERT INTO ask_traces
                (conversation_turn_id, context_type, router_reason, retrieved_chunk_ids,
                 model, prompt_tokens, completion_tokens, latency_ms)
            VALUES
                (:turn_id, :context_type, :router_reason, :chunk_ids,
                 :model, :prompt_tokens, :completion_tokens, :latency_ms)
            RETURNING id, context_type, created_at
        """),
        {
            "turn_id": conversation_turn_id,
            "context_type": context_type,
            "router_reason": router_reason,
            "chunk_ids": retrieved_chunk_ids,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
        },
    )
    return dict(result.mappings().one())


async def get_conversation_history(
    session: AsyncSession, conversation_id: UUID, limit: int = 20
) -> list[dict]:
    """Fetch recent conversation turns."""
    result = await session.execute(
        text("""
            SELECT *, parent_turn_id FROM conversation_turns
            WHERE conversation_id = :cid
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"cid": conversation_id, "limit": limit},
    )
    rows = [dict(r) for r in result.mappings().all()]
    rows.reverse()
    return rows


async def list_turns_by_document(
    session: AsyncSession, document_id: UUID, limit: int = 200
) -> list[dict]:
    """Fetch all conversation turns for a given paper, oldest first."""
    result = await session.execute(
        text("""
            SELECT id, conversation_id, document_id, role, content,
                   context_type, router_reason, model, citations, created_at,
                   parent_turn_id
            FROM conversation_turns
            WHERE document_id = :doc_id
            ORDER BY created_at ASC
            LIMIT :limit
        """),
        {"doc_id": document_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def list_turns_by_conversation(
    session: AsyncSession,
    document_id: UUID,
    conversation_id: UUID,
    limit: int = 200,
) -> list[dict]:
    """Fetch turns for a specific conversation belonging to a paper, oldest first."""
    result = await session.execute(
        text("""
            SELECT id, conversation_id, document_id, role, content,
                   context_type, router_reason, model, citations, created_at,
                   parent_turn_id
            FROM conversation_turns
            WHERE document_id = :doc_id AND conversation_id = :conv_id
            ORDER BY created_at ASC
            LIMIT :limit
        """),
        {"doc_id": document_id, "conv_id": conversation_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings().all()]


async def list_conversations_by_document(
    session: AsyncSession, document_id: UUID
) -> list[dict]:
    """Summarize all distinct conversations for a paper, most-recent first.

    Each row contains conversation_id, the first user message (as a preview),
    turn count, and the timestamps of the first and last turns.
    """
    result = await session.execute(
        text("""
            SELECT
              ct.conversation_id,
              COUNT(*) AS turn_count,
              MIN(ct.created_at) AS started_at,
              MAX(ct.created_at) AS last_at,
              (
                SELECT ct2.content
                FROM conversation_turns ct2
                WHERE ct2.conversation_id = ct.conversation_id
                  AND ct2.role = 'user'
                ORDER BY ct2.created_at ASC
                LIMIT 1
              ) AS first_user_message
            FROM conversation_turns ct
            WHERE ct.document_id = :doc_id
            GROUP BY ct.conversation_id
            ORDER BY MAX(ct.created_at) DESC
        """),
        {"doc_id": document_id},
    )
    return [dict(r) for r in result.mappings().all()]


# =============================================================================
# Sub-thread (nested tangent) support
# All new methods below are part of the "paper-free focus mode" tangent feature.
# =============================================================================

async def get_main_chat(
    session: AsyncSession,
    conversation_id: UUID,
) -> list[dict]:
    """Return ONLY the main linear chat turns for a conversation (parent_turn_id IS NULL).

    These are the turns that appear in the primary paper discussion view.
    Ordered oldest first.
    """
    result = await session.execute(
        text("""
            SELECT id, conversation_id, document_id, role, content,
                   context_type, router_reason, model, citations, created_at,
                   parent_turn_id
            FROM conversation_turns
            WHERE conversation_id = :cid
              AND parent_turn_id IS NULL
            ORDER BY created_at ASC
        """),
        {"cid": conversation_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def get_thread_subtree(
    session: AsyncSession,
    root_turn_id: UUID,
) -> list[dict]:
    """Return the full history of a sub-thread rooted at the given user turn.

    Special rule (per spec + clarification):
    - Always include the root turn.
    - Include the "first normal message": the immediate next assistant turn
      (by created_at, same conversation) that still has parent_turn_id IS NULL.
      This is the AI reply the user saw in the main chat.
    - Then recursively walk all proper descendants (parent_turn_id points into the set).

    Result is ordered chronologically.
    """
    # 1. The root
    root_res = await session.execute(
        text("SELECT * FROM conversation_turns WHERE id = :rid"),
        {"rid": root_turn_id},
    )
    root_row = root_res.mappings().first()
    if not root_row:
        return []

    collected = {root_row["id"]: dict(root_row)}
    conv_id = root_row["conversation_id"]

    # 2. Special first AI reply — ONLY relevant for main-chat sub-thread roots
    # (parent_turn_id IS NULL). The root user turn and its assistant reply are
    # inserted in the same /ask transaction so they share an identical
    # created_at — >root would silently drop the AI reply, hence the >=.
    #
    # For nested sub-thread roots (parent_turn_id IS NOT NULL) this branch
    # MUST NOT run: the AI reply is created with parent_turn_id=root by the
    # orchestrator (assistant_parent = user_turn["id"] when is_sub_thread is
    # true), so it is already collected by the descendant walk below. Running
    # the >= rule for nested roots can pull in unrelated main-chat assistant
    # turns whose timestamps happen to tie with the root.
    if root_row.get("parent_turn_id") is None:
        first_ai_res = await session.execute(
            text("""
                SELECT * FROM conversation_turns
                WHERE conversation_id = :cid
                  AND parent_turn_id IS NULL
                  AND id <> :rid
                  AND role = 'assistant'
                  AND created_at >= (SELECT created_at FROM conversation_turns WHERE id = :rid)
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            """),
            {"cid": conv_id, "rid": root_turn_id},
        )
        first_ai = first_ai_res.mappings().first()
        if first_ai:
            collected[first_ai["id"]] = dict(first_ai)

    # 3. Recursively collect all proper descendants
    to_visit = list(collected.keys())
    while to_visit:
        current = to_visit.pop()
        children_res = await session.execute(
            text("SELECT * FROM conversation_turns WHERE parent_turn_id = :pid"),
            {"pid": current},
        )
        for child in children_res.mappings():
            cid = child["id"]
            if cid not in collected:
                collected[cid] = dict(child)
                to_visit.append(cid)

    # Return sorted by created_at
    rows = list(collected.values())
    rows.sort(key=lambda r: r["created_at"])
    return rows


async def has_children(
    session: AsyncSession,
    turn_id: UUID,
) -> bool:
    """Return True if this turn is the root of (or participates in) a sub-thread
    that has at least one continuation.
    """
    result = await session.execute(
        text("""
            SELECT EXISTS (
                SELECT 1 FROM conversation_turns
                WHERE parent_turn_id = :tid
            ) AS has_children
        """),
        {"tid": turn_id},
    )
    row = result.mappings().one()
    return bool(row["has_children"])


async def compute_turn_depth(
    session: AsyncSession,
    turn_id: UUID,
) -> int:
    """Number of parent_turn_id hops from this turn up to a NULL parent.

    Used to enforce a maximum sub-thread nesting depth.

    Examples:
    - Main-chat turn (parent_turn_id IS NULL) → 0
    - User turn inside a first-level sub-thread (parent = main-chat root) → 1
    - User turn inside a second-level sub-thread → 2
    """
    result = await session.execute(
        text("""
            WITH RECURSIVE chain AS (
                SELECT id, parent_turn_id, 0 AS depth
                FROM conversation_turns
                WHERE id = :tid
                UNION ALL
                SELECT t.id, t.parent_turn_id, c.depth + 1
                FROM conversation_turns t
                JOIN chain c ON t.id = c.parent_turn_id
            )
            SELECT COALESCE(MAX(depth), 0) AS d FROM chain
        """),
        {"tid": turn_id},
    )
    row = result.mappings().first()
    return int(row["d"]) if row else 0


async def get_thread_message_count(
    session: AsyncSession,
    root_turn_id: UUID,
) -> int:
    """Return the total number of messages (including the root user + first AI reply
    + all descendants) that belong to the sub-thread rooted at root_turn_id.
    """
    # Reuse the subtree query logic but only count.
    # special_first_ai is ONLY meaningful when the root has parent_turn_id IS NULL
    # (main-chat sub-thread roots). For nested roots the AI reply already lives
    # in the descendant chain and applying the rule pulls in unrelated turns
    # whose timestamps tie with the root.
    result = await session.execute(
        text("""
            WITH RECURSIVE thread_ids AS (
                SELECT id FROM conversation_turns WHERE id = :root_id
                UNION ALL
                SELECT t.id
                FROM conversation_turns t
                JOIN thread_ids ti ON t.parent_turn_id = ti.id
            ),
            special_first_ai AS (
                -- The original first assistant reply (parent IS NULL) that belongs
                -- visually to this thread. Only applicable for main-chat roots.
                SELECT t.id
                FROM conversation_turns t
                JOIN conversation_turns root ON root.id = :root_id
                WHERE root.parent_turn_id IS NULL
                  AND t.parent_turn_id IS NULL
                  AND t.conversation_id = root.conversation_id
                  AND t.created_at >= root.created_at
                  AND t.id <> root.id
                  AND t.role = 'assistant'
                  AND NOT EXISTS (
                      SELECT 1 FROM conversation_turns t2
                      WHERE t2.conversation_id = t.conversation_id
                        AND t2.parent_turn_id IS NULL
                        AND t2.created_at >= root.created_at
                        AND t2.id <> root.id
                        AND t2.role = 'assistant'
                        AND (t2.created_at < t.created_at
                             OR (t2.created_at = t.created_at AND t2.id < t.id))
                  )
            )
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT id FROM thread_ids
                UNION
                SELECT id FROM special_first_ai
            ) all_ids
        """),
        {"root_id": root_turn_id},
    )
    row = result.mappings().one()
    return int(row["cnt"])

