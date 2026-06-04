"""Local embedding model wrapper via Ollama."""

import httpx

from app.api.errors import ModelUnavailable
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def get_embedding(text: str) -> list[float]:
    """Generate an embedding for a single text."""
    url = f"{settings.ollama_base_url}/api/embed"
    payload = {
        "model": settings.embedding_model,
        "input": text,
        # Keep the small embed model resident so a query embedding doesn't force
        # a reload, and so it can coexist with the warm chat model.
        "keep_alive": settings.ollama_keep_alive,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectError as e:
        logger.error("Ollama embedding connect error: %s", e)
        raise ModelUnavailable(f"{settings.embedding_model} (Ollama unreachable: {e})")

    embeddings = data.get("embeddings", [])
    if embeddings:
        return embeddings[0]
    raise ValueError("No embedding returned from Ollama")


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    url = f"{settings.ollama_base_url}/api/embed"
    payload = {
        "model": settings.embedding_model,
        "input": texts,
        "keep_alive": settings.ollama_keep_alive,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.ConnectError as e:
        logger.error("Ollama embedding batch connect error: %s", e)
        raise ModelUnavailable(f"{settings.embedding_model} (Ollama unreachable: {e})")

    return data.get("embeddings", [])


async def get_query_embedding(query: str) -> list[float]:
    """Generate an embedding for a search query."""
    return await get_embedding(query)


def _embed_one_sync(client: "httpx.Client", url: str, model: str, text: str) -> list[float] | None:
    r = client.post(url, json={"model": model, "input": text, "keep_alive": settings.ollama_keep_alive})
    r.raise_for_status()
    embs = r.json().get("embeddings", [])
    return embs[0] if embs else None


def get_embeddings_batch_sync(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts synchronously.

    Ollama's /api/embed enforces the model context window across the WHOLE
    batch and returns a hard 400 ("input length exceeds the context length")
    if the combined inputs are too large — it does not truncate. So we try the
    fast batched request first, and on any failure fall back to one request per
    text with progressive truncation. A chunk that still can't embed gets a
    zero vector (harmless in cosine search) so ingestion can never stall again.
    """
    url = f"{settings.ollama_base_url}/api/embed"
    model = settings.embedding_model

    with httpx.Client(timeout=120.0) as client:
        # Fast path: the whole batch in one request.
        try:
            r = client.post(url, json={"model": model, "input": texts, "keep_alive": settings.ollama_keep_alive})
            r.raise_for_status()
            embs = r.json().get("embeddings", [])
            if len(embs) == len(texts):
                return embs
        except httpx.HTTPStatusError:
            pass  # fall through to the resilient per-item path
        except httpx.ConnectError as e:
            logger.error("Ollama embedding batch sync connect error: %s", e)
            raise ModelUnavailable(f"{model} (Ollama unreachable: {e})")
        except httpx.ConnectError as e:
            logger.error("Ollama embedding batch sync connect error: %s", e)
            raise ModelUnavailable(f"{model} (Ollama unreachable: {e})")

        out: list[list[float]] = []
        for t in texts:
            emb: list[float] | None = None
            for cap in (None, 2000, 1000, 400):
                try:
                    emb = _embed_one_sync(client, url, model, t if cap is None else t[:cap])
                    if emb:
                        break
                except httpx.HTTPStatusError:
                    emb = None
                except httpx.ConnectError as e:
                    logger.error("Ollama embedding sync connect error: %s", e)
                    raise ModelUnavailable(f"{model} (Ollama unreachable: {e})")
            if not emb:
                logger.warning("Embedding failed for a chunk even after truncation; storing zero vector.")
                emb = [0.0] * settings.vector_dimension
            out.append(emb)
        return out
