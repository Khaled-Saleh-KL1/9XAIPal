"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "9XAIPal"
    debug: bool = False

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "9xaipal"
    postgres_user: str = "9xaipal"
    postgres_password: str = "9xaipal_dev_password"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Storage
    storage_root: str = "app/storage"

    # MinerU (installed CLI for PDF extraction). MinerU 3.x ships the `mineru`
    # binary; the legacy `magic-pdf` 0.x package is abandoned.
    mineru_binary: str = "mineru"
    # OCR language hint for the pipeline backend.
    mineru_lang: str = "en"
    # When mineru isn't installed, allow degraded PyMuPDF text-only fallback.
    # Disabled by default so a missing extractor fails loudly instead of silently
    # producing low-quality output with no OCR/tables/math.
    allow_pymupdf_fallback: bool = False
    # Hard wall-clock timeout (seconds) for a single MinerU subprocess. A large
    # book (e.g. a 700-page PDF) through the full pipeline on CPU-only hardware
    # can take hours, so this defaults high. Lower it to fail fast on small docs.
    mineru_timeout_sec: int = 14400  # 4 hours

    # Ollama (Gemma4 for chat + VLM enrichment)
    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "gemma4:26b"
    vlm_model: str = "gemma4:26b"
    embedding_model: str = "qwen3-embedding"

    # ── Latency tuning ──────────────────────────────────────────────────────
    # Small, fast model used ONLY for cheap classification (router + guardrail).
    # Leave empty to reuse chat_model. Pointing this at a 1–3B model (e.g.
    # "llama3.2:3b", "gemma2:2b") removes two big-model calls from the critical
    # path of every question — usually the single biggest /ask speedup.
    classifier_model: str = ""
    # How long Ollama keeps a model resident after a call. Without this the big
    # chat model is unloaded between requests and every question pays a cold
    # reload. "-1" = keep forever, "30m" = 30 minutes, "0" = unload immediately.
    ollama_keep_alive: str = "30m"
    # Cap the answer length so generation can't run away on slow hardware.
    # 0 = uncapped (model decides). Classification calls are capped separately.
    chat_num_predict: int = 0
    # Skip the LLM topic-guardrail when the user is reading a paper. Paper Q&A is
    # in-scope by definition, so this removes a whole model call per question.
    guardrail_skip_in_paper: bool = True

    @property
    def effective_classifier_model(self) -> str:
        return self.classifier_model or self.chat_model

    # LOCAL context window size (number of chunks on each side of the current one)
    local_context_window: int = 3   # Increased from 2 for better "see surrounding" experience

    # Vector
    vector_dimension: int = 4096

    # SearXNG
    searxng_url: str = "http://localhost:8080"

    # Upload limits
    max_upload_size_mb: int = 100

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    @property
    def effective_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def effective_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    # Concurrency tuning for "my machine = server" with multiple simultaneous users.
    # These control SQLAlchemy async + sync pool sizes. Increase on a beefy machine
    # with many concurrent /ask or ingestion jobs. Decrease for very low-RAM setups.
    db_pool_size: int = 10
    db_max_overflow: int = 15


settings = Settings()

