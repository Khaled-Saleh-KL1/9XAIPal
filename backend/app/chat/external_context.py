"""External context builder: web search via SearXNG.

This app is exclusively about technology / computer-science research papers
(machine learning, NLP, systems, hardware, etc.), so we bias the web search
toward that domain instead of running the raw user query — otherwise an
ambiguous term like "transduction" returns biology/genetics dictionary hits
that are useless for someone reading a Transformer paper.
"""

from typing import Optional

from app.search.searxng_client import search, search_images
from app.search.ranking import rank_results

# Words in the user query that strongly suggest they want a picture.
# When present, we fetch more images and bump them in the prompt so the
# model is invited to embed them inline.
_IMAGE_INTENT_WORDS = {
    "image", "picture", "photo", "diagram", "figure", "illustration",
    "visualize", "visualization", "show me", "draw", "generate a picture",
    "what does it look like", "looks like",
}


def _wants_images(q: str) -> bool:
    ql = q.lower()
    return any(w in ql for w in _IMAGE_INTENT_WORDS)


# Words that signal the user already framed the question as IT/CS/ML, so we
# don't need to re-bias the query.
_TECH_HINT_WORDS = {
    "transformer", "neural", "embedding", "model", "lstm", "rnn", "cnn",
    "attention", "tokenizer", "gradient", "loss", "softmax", "encoder",
    "decoder", "deep learning", "machine learning", "ml ", " ai ", "nlp",
    "gpu", "cuda", "pytorch", "tensorflow", "huggingface", "arxiv",
    "algorithm", "dataset", "benchmark", "compiler", "kernel", "kubernetes",
}


def _looks_techy(q: str) -> bool:
    ql = f" {q.lower()} "
    return any(w in ql for w in _TECH_HINT_WORDS)


def rewrite_query_for_papers(
    query: str,
    *,
    paper_title: Optional[str] = None,
) -> str:
    """Rewrite a user question so a generic web search returns CS/ML hits.

    Strategy:
      - If a paper title is known, anchor the query to it (best signal).
      - Otherwise, append a domain bias clause so dictionary / biology sources
        get out-ranked by CS/ML/AI sources.
      - If the query already contains tech jargon, only add a light bias.
    """
    q = query.strip()
    if paper_title:
        # Strip .pdf and any trailing junk so the title reads naturally.
        title = paper_title.rsplit(".", 1)[0].strip()
        return f'{q} (in the context of the research paper "{title}", machine learning / computer science)'
    if _looks_techy(q):
        return f"{q} machine learning OR deep learning OR computer science"
    return (
        f"{q} in machine learning, deep learning, NLP, or computer science "
        f"(research paper context, not biology / medicine / genetics)"
    )


async def build_external_context(
    query: str,
    *,
    max_results: int = 5,
    paper_title: Optional[str] = None,
) -> dict:
    """Build context from SearXNG web search, biased toward tech research.

    Always fetches a handful of image results in parallel so the prompt can
    invite the model to embed them inline (``![alt](url)``). When the query
    explicitly mentions pictures/diagrams/figures we ask for more images.
    """
    biased_query = rewrite_query_for_papers(query, paper_title=paper_title)

    # Text search first (with category bias, fall back to plain).
    raw_results = await search(biased_query, categories=["it", "science"])
    if not raw_results:
        raw_results = await search(biased_query)
    ranked = rank_results(raw_results, max_results=max_results)

    # Image search runs in parallel-ish: small additional latency, big UX win.
    image_limit = 4 if _wants_images(query) else 2
    try:
        images = await search_images(biased_query, limit=image_limit)
    except Exception:
        images = []

    return {
        "results": ranked,
        "images": images,
        "query": biased_query,
        "original_query": query,
        "image_intent": _wants_images(query),
    }
