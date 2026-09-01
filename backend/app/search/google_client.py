"""Google web search client — Gemini's built-in Search-grounding tool.

⚠ Not the Custom Search JSON API. That one was tried first and rejected the
key outright: "API keys are not supported by this API. Expected OAuth2
access token." (Custom Search wants an OAuth principal or a different,
older key style, and additionally needs a Programmable Search Engine `cx`
ID this app has none of.) Search grounding is Google's current API-key-auth
web-search surface: a normal ``generateContent`` call with
``tools: [{"google_search": {}}]`` makes the model run a live Google search
and ground its answer in the results, returned as ``groundingMetadata``
rather than a plain results list.

⚠ **This shape was never exercised end-to-end.** The key itself is
confirmed valid (auth succeeds — errors are 429 RESOURCE_EXHAUSTED, which
only fires post-auth, never 401/403), but every live grounding call made
while building this hit that same quota wall, so the parsing below is
written from Google's documented response shape, not a captured real
response. First in the cascade, but the moment quota/billing allows a real
call through, watch its logs for a parse coming back empty when it
shouldn't.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The model Google's own API pointed at when gemini-2.5-flash came back
# "no longer available to new users" during testing (2026-08-31).
_MODEL = "gemini-3.6-flash"
_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"
_TIMEOUT = 20.0


def _snippets_by_chunk(grounding_supports: list[dict]) -> dict[int, list[str]]:
    """Map each groundingChunk index to the answer text segments that cite it.

    Grounding gives no meta-description snippet the way a SERP would — the
    closest real substitute is the model's own generated text for whichever
    segments it grounded on that chunk, which is arguably more useful (it's
    already a read-and-summarized excerpt, not a raw page fragment).
    """
    by_chunk: dict[int, list[str]] = {}
    for support in grounding_supports:
        text = ((support.get("segment") or {}).get("text") or "").strip()
        if not text:
            continue
        for idx in support.get("groundingChunkIndices") or []:
            by_chunk.setdefault(idx, []).append(text)
    return by_chunk


async def search(
    query: str,
    *,
    categories: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Search via Gemini grounding and return normalized results.

    ``categories`` is accepted and ignored, matching every other client —
    Google Search grounding has no engine-group concept.
    """
    if not settings.google_api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                _URL,
                params={"key": settings.google_api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": query}]}],
                    "tools": [{"google_search": {}}],
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error(f"Google search (Gemini grounding) failed: {e}")
        return []

    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        grounding = candidates[0].get("groundingMetadata") or {}
        chunks = grounding.get("groundingChunks") or []
        if not chunks:
            return []
        snippets = _snippets_by_chunk(grounding.get("groundingSupports") or [])

        results = []
        for i, chunk in enumerate(chunks[:limit]):
            web = chunk.get("web") or {}
            url = web.get("uri") or ""
            if not url:
                continue
            results.append({
                "title": web.get("title") or "",
                "url": url,
                "snippet": " ".join(snippets.get(i, []))[:500],
                "source_engine": "google",
                "score": None,
            })
        return results
    except Exception as e:
        # Parsing, not the network call, failed — the shape assumed above
        # didn't hold. Degrade to [] like every other failure path rather
        # than take down the request.
        logger.error(f"Google search (Gemini grounding) response parsing failed: {e}")
        return []


async def search_images(query: str, *, limit: int = 4) -> list[dict]:
    """Search grounding is text-only — no image results. Always ``[]``."""
    return []


async def is_available() -> bool:
    """Whether Google is usable — key presence only, no network call.

    Same reasoning as every other provider: billed/quota-limited, no free
    health endpoint, and this is polled by /health every 15-30s forever.
    """
    return bool(settings.google_api_key)
