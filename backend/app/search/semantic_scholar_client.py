"""Semantic Scholar client: resolve a paper's own bibliography entry to a
real, addable paper.

⚠ **This provider is a network egress.** The reference's raw citation text —
never the paper's own content — leaves for ``api.semanticscholar.org``.

Distinct from ``app/search/*`` and ``app/scraping/*``: those answer "find web
pages/results for a query" for the chat agent. This answers "what paper is
citation [12]", using the one endpoint Semantic Scholar built for exactly
that — fuzzy-matching a raw citation string to one paper, rather than a
ranked list a caller has to disambiguate itself.

⚠ **Verified live (2026-09-09): the unauthenticated tier is already
rate-limited from this box** — every call, immediately and after backing
off, came back 429. A free key
(https://www.semanticscholar.org/product/api) is effectively required, not
optional, for this to work at all here. Never raises past this module: a
missing key, a 429, or a network error all come back as ``None`` (indistinct
from "no match" to the caller by design — the caller decides how to word
that, see references.py) plus a status the caller can log.
"""

from enum import Enum
from typing import NamedTuple, Optional

import httpx

from app.core import circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Unresolved(Enum):
    """Why `match_reference` came back with nothing — two outcomes the
    caller (endpoints/chunks.py) shows very differently: NO_MATCH is final
    ("this app couldn't find that paper"), UNAVAILABLE is transient/setup
    ("no key configured, rate-limited, or the API is down right now") and is
    worth trying again later rather than caching as a dead end.
    """
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"

_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
_FIELDS = "title,authors,year,externalIds,openAccessPdf"
_TIMEOUT = 15.0
_BREAKER_ID = "semantic_scholar"

# A raw reference string is the paper's whole bibliography entry (author
# list, title, venue, year) — plenty to match on; Semantic Scholar's own
# query length limit is generous, but nothing about this needs the full
# string past a few hundred characters and a shorter query is a smaller
# request.
_MAX_QUERY_CHARS = 300


class ReferenceMatch(NamedTuple):
    title: str
    authors: str  # "Cho, Bahdanau, Bengio" — joined for display, not a list
    year: Optional[int]
    arxiv_id: Optional[str]
    doi: Optional[str]
    pdf_url: Optional[str]  # a direct, fetchable open-access PDF, when one exists


def _headers() -> dict:
    headers = {"Accept": "application/json"}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key
    return headers


async def match_reference(raw_text: str) -> ReferenceMatch | Unresolved:
    """Resolve one bibliography entry's raw text to a paper.

    Never raises — every failure mode is a value, not an exception, since a
    resolution miss is an expected, common outcome here, not exceptional.
    """
    query = (raw_text or "").strip()[:_MAX_QUERY_CHARS]
    if not query:
        return Unresolved.UNAVAILABLE

    if not settings.semantic_scholar_api_key:
        logger.debug("Semantic Scholar match skipped: SEMANTIC_SCHOLAR_API_KEY is empty")
        return Unresolved.UNAVAILABLE

    if circuit_breaker.is_open(_BREAKER_ID):
        return Unresolved.UNAVAILABLE

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                _MATCH_URL,
                params={"query": query, "fields": _FIELDS},
                headers=_headers(),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 404:
            # The match endpoint's own "no paper found" response, not a
            # failure — nothing to trip the breaker over.
            circuit_breaker.record_success(_BREAKER_ID)
            return Unresolved.NO_MATCH
        if status == 429:
            logger.warning("Semantic Scholar rate-limited (429) — backing off")
            circuit_breaker.record_failure(_BREAKER_ID)
            return Unresolved.UNAVAILABLE
        logger.warning("Semantic Scholar match failed: HTTP %s", status)
        circuit_breaker.record_failure(_BREAKER_ID)
        return Unresolved.UNAVAILABLE
    except httpx.RequestError as e:
        logger.warning("Semantic Scholar match network error: %s", e)
        circuit_breaker.record_failure(_BREAKER_ID)
        return Unresolved.UNAVAILABLE

    circuit_breaker.record_success(_BREAKER_ID)
    data = response.json()
    # /search/match wraps its single best hit in `data`, same envelope shape
    # as /search's list endpoint, just with (at most) one element.
    hits = data.get("data") or []
    if not hits:
        return Unresolved.NO_MATCH
    return _to_match(hits[0])


def _to_match(hit: dict) -> ReferenceMatch:
    authors = ", ".join(a.get("name", "") for a in (hit.get("authors") or []) if a.get("name"))
    external = hit.get("externalIds") or {}
    open_pdf = hit.get("openAccessPdf") or {}
    return ReferenceMatch(
        title=hit.get("title") or "",
        authors=authors,
        year=hit.get("year"),
        arxiv_id=external.get("ArXiv"),
        doi=external.get("DOI"),
        pdf_url=open_pdf.get("url") or None,
    )
