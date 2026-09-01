"""Article-fetch provider failure signalling.

Mirrors app/search/errors.py's ProviderError exactly, and for the same
reason: a client must distinguish "I broke" (network error, auth failure,
quota exhausted, a response shape that isn't what was documented) from "I
worked, and this page genuinely can't be fetched" so the circuit breaker in
app/services/article_extraction.py's cascade only trips on the first case.
"""


class FetchProviderError(Exception):
    """An article-fetch provider failed — as opposed to a page that's
    legitimately unreachable (see article_extraction.ArticleExtractionError,
    which is what a caller ultimately sees once every provider is exhausted)."""
