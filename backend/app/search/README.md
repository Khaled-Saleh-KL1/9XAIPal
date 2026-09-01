# Search Design

## Purpose

The `search` directory owns external web retrieval through a 6-provider cascade: google, tavily,
linkup, exa, serpapi, then duckduckgo. `web.py` is the only door callers use — never a provider
module directly.

External search is used only when the context router selects `External`, or the paper agent's
`WEB` tool fires.

## Files

### `web.py`

The cascade itself: tries each configured provider in priority order, falls through to the next
on any exception or an empty result list. `duckduckgo` needs no API key and is always eligible, so
`is_configured()` is true under "auto" even with every key blank — the only way to fully disable
web search is `WEB_SEARCH_PROVIDER=none`.

### `google_client.py`, `tavily_client.py`, `linkup_client.py`, `exa_client.py`, `serpapi_client.py`, `duckduckgo_client.py`

One client per provider, each exposing the same three functions (`search`, `search_images`,
`is_available`) and the same normalized return shape (`title`, `url`, `snippet`, `source_engine`,
`score` for text; `img_url`, `thumbnail`, `title`, `source_url`, `source_engine` for images) so
`web.py`'s cascade can treat all six identically. `duckduckgo_client.py` wraps the synchronous
`ddgs` library via `asyncio.to_thread` rather than calling an HTTP API directly — there is no
official DuckDuckGo search API. `exa_client.py`'s `search_images` is always `[]` (Exa is a
document index, not a SERP); `google_client.py`'s is too (Search grounding is text-only).

### `ranking.py`

Ranks and filters search results before they are passed to the LLM. It removes duplicate URLs,
sorts by score when a provider supplies a comparable one, trims noisy snippets, and limits token
footprint.

⚠ Do not add a category/engine-group filter to a `search()` call. One used to bias SearXNG toward
"it"/"science" engines and was removed 2026-08-31 after it was found to make results *worse*:
SearXNG's own `score` field is a per-engine position weight, not a value comparable across the
several engines a category filter turns on, so results were effectively ranked at random among
Docker Hub, GitHub, and the actual paper. None of the six current providers have an engine-group
concept anyway — the domain bias lives entirely in `chat.external_context.rewrite_query_for_papers`.

## Data Dependencies

`search` is used by `chat.external_context` and `chat.agent_tools.run_web`.

`search` should not call the database directly unless cached web search is explicitly added later.
