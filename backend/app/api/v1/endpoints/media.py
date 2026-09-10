"""Authenticated media endpoints for private, durable research images."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.core.paths import research_images_dir

router = APIRouter()


@router.get("/research/{conversation_id}/{filename}")
async def get_research_image(
    conversation_id: UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Serve a persisted research image only to a conversation participant."""
    if not filename or filename != Path(filename).name:
        raise HTTPException(status_code=404, detail="Media not found")

    result = await db.execute(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM conversation_turns "
            "WHERE conversation_id = :conversation_id AND user_id = :user_id"
            ")"
        ),
        {"conversation_id": conversation_id, "user_id": current_user["id"]},
    )
    if not result.scalar_one():
        raise HTTPException(status_code=404, detail="Media not found")

    path = research_images_dir(conversation_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path=str(path))
