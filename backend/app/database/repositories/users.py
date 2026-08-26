"""User repository: accounts."""

from uuid import UUID
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str,
    display_name: Optional[str] = None,
) -> dict:
    """Insert a new user. Caller must normalize/lowercase `email` first —
    the DB enforces case-insensitive uniqueness via a functional index, but
    normalizing here too keeps what's stored matching what's displayed."""
    result = await session.execute(
        text("""
            INSERT INTO users (email, password_hash, display_name)
            VALUES (:email, :password_hash, :display_name)
            RETURNING id, email, display_name, created_at
        """),
        {"email": email, "password_hash": password_hash, "display_name": display_name},
    )
    return dict(result.mappings().one())


async def get_user_by_email(session: AsyncSession, email: str) -> Optional[dict]:
    """Case-insensitive lookup — matches the functional unique index."""
    result = await session.execute(
        text("SELECT * FROM users WHERE LOWER(email) = LOWER(:email)"),
        {"email": email},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> Optional[dict]:
    result = await session.execute(
        text("SELECT id, email, display_name, created_at FROM users WHERE id = :id"),
        {"id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None
