"""Which models the reader may ask.

Enumerates models the configured providers advertise, then reports only
availability learned from real chat calls. Unknown availability stays
selectable so a temporary Redis or provider outage cannot empty the picker.

Ollama exposes two kinds of model under one API:

* **pulled locally** — weights on disk, reported with a real byte size
  (``gemma4:26b``, 18 GB).
* **cloud-hosted** — run on Ollama's infrastructure or another remote Ollama
  endpoint, even when its tag rows report non-zero sizes.

Both are askable, so both are listed — cloud ones last, since they leave the
machine and that ordering makes the local-first default obvious.
"""

import ipaddress
from typing import Optional
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.llm import availability, resolver

logger = get_logger(__name__)


def _is_cloud(name: str, size: int, base_url: Optional[str] = None) -> bool:
    """A model Ollama runs remotely rather than from local weights.

    Endpoint location is authoritative for hosted Ollama deployments such as
    ollama.com. Tag suffix and size remain useful signals for local endpoints.
    """
    lowered = name.lower()
    parsed = urlsplit(base_url or settings.ollama_base_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "localhost.localdomain"} and not host.endswith(".localhost"):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
            remote = False
        elif parsed.scheme == "http" and parsed.port == 11434:
            remote = False
        else:
            remote = bool(host)
    else:
        remote = False
    return remote or lowered.endswith("cloud") or size == 0


def _is_embedding(name: str) -> bool:
    """Embedding models share the tag list but cannot hold a conversation."""
    return "embedding" in name.lower() or "embed" in name.lower()


async def list_chat_models() -> dict:
    """Return the models that can answer a question, plus which one is default.

    Shape: ``{"models": [{name, is_cloud, size_bytes, available,
    unavailable_reason}], "default": str}``.

    Falls back to the single configured cloud model when Ollama is unreachable
    — the user still gets a working (if one-item) picker rather than an error.
    """
    default = ""
    default_provider = ""
    try:
        target = await resolver.resolve_llm()
        default = target.model_for_role("chat")
        default_provider = target.provider
    except Exception:
        logger.warning("catalog: no LLM configured; the picker will be empty")

    models: list[dict] = []
    provider_by_name: dict[str, str] = {}
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
                    provider_by_name[name] = "ollama"
        except Exception:
            logger.exception("catalog: could not read Ollama tags (non-fatal)")

    if not models and default:
        # No Ollama — the active cloud provider's model is the only option.
        models.append({"name": default, "is_cloud": True, "size_bytes": 0})
        provider_by_name[default] = default_provider or "ollama"

    # A model pinned to its own provider (see resolver.MODEL_PROVIDER_PINS,
    # e.g. meta/muse-glimmer-30b -> nvidia) is selectable directly regardless
    # of whether Ollama is reachable — picking it bypasses the cascade
    # entirely, so it doesn't need to "win" the cascade to show up here.
    for pinned_model, provider in resolver.MODEL_PROVIDER_PINS.items():
        if resolver.cloud_api_key(provider):
            row = next((m for m in models if m["name"] == pinned_model), None)
            if row is None:
                row = {"name": pinned_model, "is_cloud": True, "size_bytes": 0}
                models.append(row)
            row["is_cloud"] = True
            provider_by_name[pinned_model] = provider

    # Local first, then cloud; alphabetical within each group.
    models.sort(key=lambda m: (m["is_cloud"], m["name"].lower()))

    # The default must be selectable even if it is not in the tag list (a cloud
    # provider model, or a tag pulled after this process started).
    if default and not any(m["name"] == default for m in models):
        models.append({"name": default, "is_cloud": True, "size_bytes": 0})
        provider_by_name[default] = default_provider or "ollama"

    availability_by_model = await availability.get_model_availability([
        (provider_by_name.get(model["name"], "ollama"), model["name"])
        for model in models
    ])
    for model in models:
        state = availability_by_model.get(
            (provider_by_name.get(model["name"], "ollama"), model["name"]),
            {"available": True, "unavailable_reason": None},
        )
        model["available"] = state["available"]
        model["unavailable_reason"] = state["unavailable_reason"]

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
