# Search Design

## Purpose

The `search` directory owns external web retrieval through SearXNG.

External search is used only when the context router selects `External`.

## Files

### `searxng_client.py`

Wraps SearXNG HTTP calls, applies timeouts, normalizes results, and returns title, URL, snippet, source engine, and score.

### `ranking.py`

Ranks and filters search results before they are passed to the LLM. It removes duplicate URLs, prefers authoritative sources, trims noisy snippets, and limits token footprint.

## Data Dependencies

`search` is used by `chat.external_context`.

`search` should not call the database directly unless cached web search is explicitly added later.

