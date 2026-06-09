"""Local model registry: capabilities and names."""

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass
class ModelCapabilities:
    text: bool = True
    vision: bool = False
    math: bool = False
    long_context: bool = False
    embedding: bool = False


@dataclass
class ModelEntry:
    name: str
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)


# Registry of known local models (prefer lowercase tags; resolver in ollama_client is case-insensitive)
MODEL_REGISTRY: dict[str, ModelEntry] = {
    "gemma4:26b": ModelEntry(
        name="gemma4:26b",
        capabilities=ModelCapabilities(text=True, vision=True, math=True, long_context=True),
    ),
    "nomic-embed-text": ModelEntry(
        name="nomic-embed-text",
        capabilities=ModelCapabilities(text=True, embedding=True),
    ),
    "qwen3-embedding": ModelEntry(
        name="qwen3-embedding",
        capabilities=ModelCapabilities(text=True, embedding=True),
    ),
}


def get_chat_model() -> str:
    return settings.chat_model


def get_embedding_model() -> str:
    return settings.embedding_model


def get_vlm_model() -> str:
    return settings.vlm_model

