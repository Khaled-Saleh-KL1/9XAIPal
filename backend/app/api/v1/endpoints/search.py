"""Search endpoints: vector and external search for debugging."""

from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services.retrieval import search_chunks
from app.search.searxng_client import search as web_search
from app.search.ranking import rank_results

router = APIRouter()


@router.get("/vector")
async def vector_search(
    q: str = Query(..., description="Search query"),
    document_id: Optional[UUID] = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    """Search chunks by vector similarity."""
    results = await search_chunks(db, q, limit=limit, document_id=document_id)
    return {"results": results, "query": q, "total": len(results)}


@router.get("/web")
async def external_search(
    q: str = Query(..., description="Search query"),
    limit: int = 5,
):
    """Search the web via SearXNG."""
    raw = await web_search(q, limit=limit)
    ranked = rank_results(raw, max_results=limit)
    return {"results": ranked, "query": q, "total": len(ranked)}

