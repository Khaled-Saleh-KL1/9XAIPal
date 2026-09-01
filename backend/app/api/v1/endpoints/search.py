"""Search endpoints: external web search.

The vector-search debugging endpoint that used to live here
(`GET /search/vector`) has been removed — it took an arbitrary `document_id`
(or none, scanning every user's chunks) with no ownership check, had no
caller in the frontend, and existed only "for debugging". Every real chunk
search path goes through app.services.retrieval, scoped by the endpoint
that calls it (a single already-owned document, or a study's already-owned
paper list) — never through this router.
"""

from fastapi import APIRouter, Query

from app.search.web import search as web_search
from app.search.ranking import rank_results

router = APIRouter()


@router.get("/web")
async def external_search(
    q: str = Query(..., description="Search query"),
    limit: int = 5,
):
    """Search the web via the configured provider cascade (see app/search/web.py)."""
    raw = await web_search(q, limit=limit)
    ranked = rank_results(raw, max_results=limit)
    return {"results": ranked, "query": q, "total": len(ranked)}
