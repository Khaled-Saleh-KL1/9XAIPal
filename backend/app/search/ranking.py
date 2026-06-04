"""Search result ranking and filtering."""


def rank_results(results: list[dict], max_results: int = 5) -> list[dict]:
    """Rank and filter search results."""
    if not results:
        return []

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique = []
    for r in results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(r)

    # Sort by score if available
    unique.sort(key=lambda x: x.get("score") or 0, reverse=True)

    # Trim snippets
    for r in unique:
        snippet = r.get("snippet", "")
        if len(snippet) > 500:
            r["snippet"] = snippet[:500] + "..."

    return unique[:max_results]

