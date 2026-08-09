"""Model catalog endpoint: what the reader can ask."""

from fastapi import APIRouter

from app.llm.catalog import list_chat_models

router = APIRouter()


@router.get("")
async def get_models():
    """List askable models, local first and cloud-hosted last."""
    return await list_chat_models()
