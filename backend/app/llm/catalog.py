"""Which models the reader may ask.

Enumerates what is actually available to answer a question, so the composer can
offer a real choice instead of a hardcoded list that drifts from the machine.

Ollama exposes two kinds of model under one API:

* **pulled locally** — weights on disk, reported with a real byte size
  (``gemma4:26b``, 18 GB).
* **cloud-hosted** — run on Ollama's infrastructure, present in the tag list
  but reported with ``size: 0`` and a name ending in ``cloud``
  (``glm-5.2:cloud``, ``gemma4:31b-cloud``).

Both are askable, so both are listed — cloud ones last, since they leave the
machine and that ordering makes the local-first default obvious.
"""

from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm import resolver

logger = get_logger(__name__)


def _is_cloud(name: str, size: int) -> bool:
    """A model Ollama runs remotely rather than from local weights.

    Checks the name suffix first because it is the explicit signal — both
    ``:cloud`` and ``-cloud`` forms appear in practice. Size is a corroborating
    hint: a cloud entry carries no weights, so it reports zero bytes.
    """
    lowered = name.lower()
    return lowered.endswith("cloud") or size == 0


def _is_embedding(name: str) -> bool:
    """Embedding models share the tag list but cannot hold a conversation."""
    return "embedding" in name.lower() or "embed" in name.lower()


async def list_chat_models() -> dict:
    """Return the models that can answer a question, plus which one is default.

    Shape: ``{"models": [{name, is_cloud, size_bytes}], "default": str}``.

    Falls back to the single configured cloud model when Ollama is unreachable
    — the user still gets a working (if one-item) picker rather than an error.
    """
    default = ""
    try:
        target = await resolver.resolve_llm()
        default = target.model_for_role("chat")
    except Exception:
        logger.warning("catalog: no LLM configured; the picker will be empty")

    models: list[dict] = []
    if await resolver.ollama_reachable():
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(
                    f"{settings.ollama_base_url}/api/tags", headers=resolver._ollama_headers()
                )
                resp.raise_for_status()
                for entry in resp.json().get("models", []):
                    name = entry.get("name") or ""
                    if not name or _is_embedding(name):
                        continue
                    size = int(entry.get("size") or 0)
                    models.append({
                        "name": name,
                        "is_cloud": _is_cloud(name, size),
                        "size_bytes": size,
                    })
        except Exception:
            logger.exception("catalog: could not read Ollama tags (non-fatal)")

    if not models and default:
        # No Ollama — the active cloud provider's model is the only option.
        models.append({"name": default, "is_cloud": True, "size_bytes": 0})

    # Local first, then cloud; alphabetical within each group.
    models.sort(key=lambda m: (m["is_cloud"], m["name"].lower()))

    # The default must be selectable even if it is not in the tag list (a cloud
    # provider model, or a tag pulled after this process started).
    if default and not any(m["name"] == default for m in models):
        models.append({"name": default, "is_cloud": True, "size_bytes": 0})

    return {"models": models, "default": default}


def resolve_requested_model(requested: Optional[str]) -> Optional[str]:
    """Normalize a client-supplied model name.

    Returns None for blank input, meaning "use the configured default". No
    allowlist check happens here: the model is validated by the provider on
    use, and a stale name surfaces as a clear ModelUnavailable error rather
    than a silent fallback to a different model than the reader chose.
    """
    if not requested:
        return None
    name = requested.strip()
    return name or None
