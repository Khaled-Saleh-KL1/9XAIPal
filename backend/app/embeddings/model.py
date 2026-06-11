"""Embedding model wrapper: local Ollama or an OpenAI-compatible cloud API.

The backend is picked by app.llm.resolver (EMBEDDING_PROVIDER=auto: Ollama
when reachable, else OpenAI, else Gemini — the only clouds with embedding
APIs) and pinned for the process lifetime so one library is never embedded by
two different models within a run.

All embeddings are shaped to settings.vector_dimension before storage/search:
larger vectors are truncated and re-normalized — valid for MRL-trained models
(qwen3-embedding, text-embedding-3-*, gemini-embedding) — and smaller ones are
zero-padded (padding never changes cosine similarity). Keeping the stored
dimension ≤ 2000 is what allows the pgvector HNSW index to exist at all.
"""

import math

import httpx

from app.api.errors import ModelUnavailable
from app.core.config import settings
from app.core.logging import get_logger
from app.llm import resolver
from app.llm.resolver import EmbeddingTarget

logger = get_logger(__name__)

# Providers whose /embeddings endpoint accepts the OpenAI `dimensions`
# parameter (server-side MRL truncation). For others we only shape locally.
_DIMENSIONS_PARAM_PROVIDERS = {"openai", "gemini"}


async def active_embedding_model() -> str:
    """Model name embeddings are stored under (for the embedding_model column)."""
    return (await resolver.resolve_embedding()).model


def active_embedding_model_sync() -> str:
    """Sync variant for Celery workers."""
    return resolver.resolve_embedding_sync().model


def _cloud_headers(target: EmbeddingTarget) -> dict:
    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    return headers


def shape_embedding(vec: list[float]) -> list[float]:
    """Fit an embedding to settings.vector_dimension (truncate+renorm or pad)."""
    dim = settings.vector_dimension
    if len(vec) == dim:
        return vec
    if len(vec) > dim:
        head = vec[:dim]
        norm = math.sqrt(sum(x * x for x in head)) or 1.0
        return [x / norm for x in head]
    return list(vec) + [0.0] * (dim - len(vec))


def _cloud_payload(texts: list[str], target: EmbeddingTarget) -> dict:
    payload: dict = {"model": target.model, "input": texts}
    if target.provider in _DIMENSIONS_PARAM_PROVIDERS:
        payload["dimensions"] = settings.vector_dimension
    return payload


def _parse_cloud_embeddings(data: dict, expected: int, model: str) -> list[list[float]]:
    rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
    embeddings = [r.get("embedding") or [] for r in rows]
    if len(embeddings) != expected:
        raise ModelUnavailable(
            f"{model} (returned {len(embeddings)} embeddings for {expected} inputs)"
        )
    return [shape_embedding(e) for e in embeddings]


async def get_embedding(text: str) -> list[float]:
    """Generate an embedding for a single text."""
    return (await get_embeddings_batch([text]))[0]


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    if not texts:
        return []

    target = await resolver.resolve_embedding()
    if target.provider == "ollama":
        url = f"{target.base_url}/api/embed"
        payload = {
            "model": target.model,
            "input": texts,
            # Keep the small embed model resident so a query embedding doesn't
            # force a reload, and so it can coexist with the warm chat model.
            "keep_alive": settings.ollama_keep_alive,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            logger.error("Ollama embedding error: %s", e)
            raise ModelUnavailable(f"{target.model} (Ollama error: {e})")
        embeddings = data.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise ModelUnavailable(
                f"{target.model} (returned {len(embeddings)} embeddings for {len(texts)} inputs)"
            )
        return [shape_embedding(e) for e in embeddings]

    url = f"{target.base_url}/embeddings"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=_cloud_payload(texts, target), headers=_cloud_headers(target))
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300]
        logger.error("%s embedding HTTP %d: %s", target.provider, e.response.status_code, body)
        raise ModelUnavailable(f"{target.model} ({e.response.status_code}: {body})")
    except httpx.RequestError as e:
        raise ModelUnavailable(f"{target.model} (network error: {e})")
    return _parse_cloud_embeddings(data, len(texts), target.model)


async def get_query_embedding(query: str) -> list[float]:
    """Generate an embedding for a search query."""
    return await get_embedding(query)


def _embed_one_sync(client: "httpx.Client", url: str, model: str, text: str) -> list[float] | None:
    r = client.post(url, json={"model": model, "input": text, "keep_alive": settings.ollama_keep_alive})
    r.raise_for_status()
    embs = r.json().get("embeddings", [])
    return embs[0] if embs else None


def get_embeddings_batch_sync(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts synchronously (Celery workers).

    Ollama's /api/embed enforces the model context window across the WHOLE
    batch and returns a hard 400 ("input length exceeds the context length")
    if the combined inputs are too large — it does not truncate. So we try the
    fast batched request first, and on any failure fall back to one request per
    text with progressive truncation. A chunk that still can't embed gets a
    zero vector (harmless in cosine search) so ingestion can never stall again.
    """
    if not texts:
        return []

    target = resolver.resolve_embedding_sync()
    if target.provider != "ollama":
        url = f"{target.base_url}/embeddings"
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, json=_cloud_payload(texts, target), headers=_cloud_headers(target))
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            logger.error("[sync] %s embedding HTTP %d: %s", target.provider, e.response.status_code, body)
            raise ModelUnavailable(f"{target.model} ({e.response.status_code}: {body})")
        except httpx.RequestError as e:
            raise ModelUnavailable(f"{target.model} (network error: {e})")
        return _parse_cloud_embeddings(data, len(texts), target.model)

    url = f"{target.base_url}/api/embed"
    model = target.model

    with httpx.Client(timeout=120.0) as client:
        # Fast path: the whole batch in one request.
        try:
            r = client.post(url, json={"model": model, "input": texts, "keep_alive": settings.ollama_keep_alive})
            r.raise_for_status()
            embs = r.json().get("embeddings", [])
            if len(embs) == len(texts):
                return [shape_embedding(e) for e in embs]
        except httpx.HTTPStatusError:
            pass  # fall through to the resilient per-item path
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
            out.append(shape_embedding(emb))
        return out
