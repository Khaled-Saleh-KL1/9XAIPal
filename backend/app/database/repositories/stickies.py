"""Sticky notes: what the reader — and the assistant — want kept in front of them.

Two boards:

    board='chat'       beside one conversation, keyed by study_id
                       (NULL study_id = the library-wide chat)
    board='universal'  the standalone board, tied to no conversation

⚠ **`board` is not redundant with `study_id`.** `study_id IS NULL` already means
the library-wide *chat*, so without the column a note on that chat and a note on
the universal board would be the same row.

⚠ **Deliberately not `personal_notes`.** A personal note is anchored to a block
in one document and lives in that document's margin; a sticky has no anchor and
lives on a board. Sharing a table would give every sticky an
`anchor_sequence_id` that means nothing.

⚠ **There is no assistant-facing delete.** The reader is the only one who
removes a note. That is enforced structurally rather than by a flag:
:func:`delete_sticky` exists for the HTTP endpoint the UI calls, and
``study_agent`` never imports it.
"""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The UI maps these to CSS variables so they survive the light/dark switch;
# storing a hex here would not. Anything else falls back to 'yellow'.
COLORS = ("yellow", "blue", "green", "pink", "orange", "plain")

BOARDS = ("chat", "universal")


def normalize_color(color: Optional[str]) -> str:
    c = (color or "").strip().lower()
    return c if c in COLORS else "yellow"


def normalize_board(board: Optional[str]) -> str:
    b = (board or "").strip().lower()
    return b if b in BOARDS else "universal"


async def _attach_papers(session: AsyncSession, rows: list[dict]) -> list[dict]:
    """Fold each sticky's referenced papers onto it in one extra query, not N."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    result = await session.execute(
        text("""
            SELECT snp.sticky_id, snp.document_id,
                   COALESCE(NULLIF(TRIM(d.title), ''), d.original_filename) AS label
            FROM sticky_note_papers snp
            JOIN documents d ON d.id = snp.document_id
            WHERE snp.sticky_id = ANY(:ids)
        """),
        {"ids": ids},
    )
    by_sticky: dict = {}
    for r in result.mappings().all():
        by_sticky.setdefault(r["sticky_id"], []).append(
            {"document_id": r["document_id"], "label": r["label"]}
        )
    for row in rows:
        row["papers"] = by_sticky.get(row["id"], [])
    return rows


async def list_stickies(
    session: AsyncSession,
    *,
    board: str = "universal",
    study_id: Optional[UUID] = None,
) -> list[dict]:
    """One board's notes, pinned first then newest.

    ⚠ ``IS NOT DISTINCT FROM`` rather than ``=`` for ``study_id``: the
    library-wide chat is keyed by NULL, and ``study_id = NULL`` matches nothing.
    """
    board = normalize_board(board)
    if board == "universal":
        result = await session.execute(
            text("""
                SELECT * FROM sticky_notes
                WHERE board = 'universal'
                ORDER BY pinned DESC, updated_at DESC
            """)
        )
    else:
        result = await session.execute(
            text("""
                SELECT * FROM sticky_notes
                WHERE board = 'chat'
                  AND study_id IS NOT DISTINCT FROM :study_id
                ORDER BY pinned DESC, updated_at DESC
            """),
            {"study_id": study_id},
        )
    return await _attach_papers(session, [dict(r) for r in result.mappings().all()])


async def get_sticky(session: AsyncSession, sticky_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT * FROM sticky_notes WHERE id = :id"), {"id": sticky_id}
    )
    row = result.mappings().first()
    if not row:
        return None
    return (await _attach_papers(session, [dict(row)]))[0]


async def _set_papers(
    session: AsyncSession, sticky_id: UUID, document_ids: list[UUID]
) -> None:
    await session.execute(
        text("DELETE FROM sticky_note_papers WHERE sticky_id = :id"), {"id": sticky_id}
    )
    for document_id in dict.fromkeys(document_ids):  # de-dupe, keep order
        await session.execute(
            text("""
                INSERT INTO sticky_note_papers (sticky_id, document_id)
                VALUES (:sticky_id, :document_id)
                ON CONFLICT DO NOTHING
            """),
            {"sticky_id": sticky_id, "document_id": document_id},
        )


async def create_sticky(
    session: AsyncSession,
    *,
    body: str,
    board: str = "universal",
    study_id: Optional[UUID] = None,
    color: Optional[str] = None,
    pinned: bool = False,
    origin: str = "user",
    author_model: Optional[str] = None,
    document_ids: Optional[list[UUID]] = None,
) -> dict:
    board = normalize_board(board)
    result = await session.execute(
        text("""
            INSERT INTO sticky_notes
                (body, board, study_id, color, pinned, origin, author_model)
            VALUES
                (:body, :board, :study_id, :color, :pinned, :origin, :author_model)
            RETURNING *
        """),
        {
            "body": body,
            "board": board,
            # A universal note is tied to no conversation, so the scope is
            # dropped rather than carried along where nothing would read it.
            "study_id": study_id if board == "chat" else None,
            "color": normalize_color(color),
            "pinned": pinned,
            "origin": "assistant" if origin == "assistant" else "user",
            "author_model": author_model if origin == "assistant" else None,
        },
    )
    row = dict(result.mappings().one())
    await _set_papers(session, row["id"], document_ids or [])
    return (await _attach_papers(session, [row]))[0]


async def update_sticky(
    session: AsyncSession,
    sticky_id: UUID,
    *,
    body: Optional[str] = None,
    color: Optional[str] = None,
    pinned: Optional[bool] = None,
    board: Optional[str] = None,
    study_id: Optional[UUID] = None,
    move_scope: bool = False,
    document_ids: Optional[list[UUID]] = None,
) -> Optional[dict]:
    """Patch a sticky. Omitted fields are left alone.

    ⚠ ``origin`` is never patchable. A note the assistant wrote stays marked as
    the assistant's however many times the reader edits it — the marker records
    where the claim came from, and letting an edit launder it would make the
    badge worthless.

    ⚠ Moving between boards needs ``move_scope=True`` as well as ``board``,
    because ``study_id=None`` is a legitimate destination (the library chat) and
    is otherwise indistinguishable from "not supplied".
    """
    sets = ["updated_at = NOW()"]
    params: dict = {"id": sticky_id}
    if body is not None:
        sets.append("body = :body")
        params["body"] = body
    if color is not None:
        sets.append("color = :color")
        params["color"] = normalize_color(color)
    if pinned is not None:
        sets.append("pinned = :pinned")
        params["pinned"] = pinned
    if move_scope:
        target = normalize_board(board)
        sets.append("board = :board")
        params["board"] = target
        sets.append("study_id = :study_id")
        params["study_id"] = study_id if target == "chat" else None

    result = await session.execute(
        text(f"UPDATE sticky_notes SET {', '.join(sets)} WHERE id = :id RETURNING *"),
        params,
    )
    row = result.mappings().first()
    if not row:
        return None
    if document_ids is not None:
        await _set_papers(session, sticky_id, document_ids)
    return (await _attach_papers(session, [dict(row)]))[0]


async def delete_sticky(session: AsyncSession, sticky_id: UUID) -> bool:
    """Remove a note.

    ⚠ **Reader-only.** Reached from `DELETE /stickies/{id}`, which the UI calls
    and nothing else does. ``study_agent`` must never import this: the
    assistant writes and edits notes, and removing one is the reader's call.
    """
    result = await session.execute(
        text("DELETE FROM sticky_notes WHERE id = :id"), {"id": sticky_id}
    )
    return (result.rowcount or 0) > 0
