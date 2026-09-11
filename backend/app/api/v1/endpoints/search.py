"""Search endpoints: external web search.

The vector-search debugging endpoint that used to live here
(`GET /search/vector`) has been removed — it took an arbitrary `document_id`
(or none, scanning every user's chunks) with no ownership check, had no
caller in the frontend, and existed only "for debugging". Every real chunk
search path goes through app.services.retrieval, scoped by the endpoint
that calls it (a single already-owned document, or a study's already-owned
paper list) — never through this router.
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.search.web import search as web_search
from app.search.ranking import rank_results

router = APIRouter()


@router.get("/web")
async def external_search(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    limit: int = Query(default=5, ge=1),
    _current_user: dict = Depends(get_current_user),
):
    """Search the web via the configured provider cascade (see app/search/web.py).

    Requires a session (docs/issues/002 — the providers cost quota) but
    deliberately has NO per-user rate limit and no ceiling on `limit`.
    Khaled's call: this is a private box for a few readers, the provider
    cascade already rotates through several keyed APIs and ends at
    DuckDuckGo, which needs no key at all, so an exhausted quota degrades
    to a free provider rather than to an error. A 20/min cap was added by
    the audit and removed the same week — it throttled the readers it was
    meant to protect while defending a quota nobody is competing for.
    """
    raw = await web_search(q, limit=limit)
    ranked = rank_results(raw, max_results=limit)
    return {"results": ranked, "query": q, "total": len(ranked)}
