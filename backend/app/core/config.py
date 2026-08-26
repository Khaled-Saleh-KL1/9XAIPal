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

    # ── LLM provider ────────────────────────────────────────────────────────
    # Which API answers questions. "auto" (default): use Ollama when it is
    # reachable at OLLAMA_BASE_URL, otherwise fall back to the first cloud
    # provider below with an API key set (openai → anthropic → gemini → xai →
    # deepseek); if neither exists, requests fail with instructions to add an
    # API key or an Ollama connection. Set explicitly to pin one backend:
    # "ollama", "openai" (GPT), "anthropic" (Claude), "gemini" (Google),
    # "xai" (Grok), "deepseek", or "custom" (any OpenAI-compatible endpoint).
    llm_provider: str = "auto"
    # Generic key, used when LLM_PROVIDER is pinned explicitly. The
    # per-provider keys below also work in pinned mode and win when both set.
    llm_api_key: str = ""
    # Override the provider's default API base URL (required for "custom",
    # optional otherwise — e.g. an Azure/OpenRouter/proxy endpoint).
    llm_base_url: str = ""

    # Per-provider API keys — in auto mode the first non-empty one (in the
    # order above) is used when Ollama is unreachable.
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    xai_api_key: str = ""
    deepseek_api_key: str = ""

    # Chat model used when each cloud provider is active. CHAT_MODEL /
    # VLM_MODEL / CLASSIFIER_MODEL stay reserved for Ollama (and "custom"),
    # so switching backends never sends an Ollama tag to a cloud API.
    openai_chat_model: str = "gpt-4o"
    anthropic_chat_model: str = "claude-sonnet-4-6"
    gemini_chat_model: str = "gemini-2.5-flash"
    xai_chat_model: str = "grok-4"
    # Note: DeepSeek models have no vision support — figure images can't be
    # described when DeepSeek is the active provider (captions still work).
    deepseek_chat_model: str = "deepseek-chat"

    # ── Cloud thinking / reasoning mode ─────────────────────────────────────
    # When True, sends ``reasoning_effort: "medium"`` to OpenAI-compatible
    # chat-completions endpoints for reasoning models (o1, o3-mini, o4-mini,
    # etc.). Only affects providers/models that support it; silently ignored
    # for Anthropic, Gemini, xAI, DeepSeek, Ollama, and non-reasoning models.
    # Make sure your active chat model is a reasoning model before enabling.
    cloud_thinking_mode: bool = False

    # ── Embedding provider ──────────────────────────────────────────────────
    # "auto" (default): Ollama when reachable, else OPENAI_API_KEY, else
    # GEMINI_API_KEY — only OpenAI and Gemini offer embedding APIs (Anthropic/
    # xAI/DeepSeek don't). Pin to "ollama", "openai", "gemini", or "custom"
    # (OpenAI-compatible /embeddings endpoint) to force one. Key/base-url fall
    # back to the llm_* values when left empty.
    embedding_provider: str = "auto"
    embedding_api_key: str = ""
    embedding_base_url: str = ""

    # Embedding model used when each cloud provider is active. EMBEDDING_MODEL
    # stays reserved for Ollama (and "custom").
    openai_embedding_model: str = "text-embedding-3-small"
    gemini_embedding_model: str = "gemini-embedding-001"

    # Ollama (local default backend; model names live in .env)
    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "gemma4:26b"
    # Vision model for figure descriptions / image questions. Empty = reuse
    # chat_model (set it only when a separate multimodal model should handle
    # vision, e.g. a smaller VLM).
    vlm_model: str = ""
    embedding_model: str = "qwen3-embedding"

    @property
    def effective_vlm_model(self) -> str:
        return self.vlm_model or self.chat_model

    # How many figure descriptions to generate concurrently at ingestion.
    # The VLM calls are pure network I/O, so running them in a small thread
    # pool turns total wall-clock from the SUM of every figure into roughly the
    # slowest one. Measured 2026-07-25 on a 14-page paper: sequential was
    # 190+210+72 = 472s, i.e. 86% of a 549s ingestion.
    # Keep this modest — it is concurrent load on one inference endpoint, and a
    # hosted one may rate-limit. 1 restores the old sequential behaviour.
    vlm_max_concurrency: int = 4

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

    # ── Ingest profile ──────────────────────────────────────────────────────
    # "fast" (default): a paper is DONE the moment MinerU has extracted it and
    # the chunker has run. No embedding pass, no section summaries, no VLM
    # figure descriptions — nothing between dropping the PDF and reading it.
    # Everything the model needs is derived at question time by
    # app.chat.paper_agent (whole-document stuffing, or full-text SEARCH/READ
    # over chunks when the paper is too large to stuff).
    #
    # "full": the historical chain (embeddings → summaries → figure
    # descriptions), still honouring paper_only_mode below.
    #
    # ⚠ The profile only applies to doc_kind='paper'. Books are far too large
    # to stuff or to full-text-scan usefully, so they always take the full
    # chain regardless of this setting.
    ingest_profile: str = "fast"

    @property
    def fast_ingest(self) -> bool:
        return self.ingest_profile.strip().lower() == "fast"

    # ── Paper agent (answering without embeddings) ──────────────────────────
    # Whether a note may be answered by stuffing the ENTIRE paper into the
    # prompt.
    #
    # ⚠ Default False, and that default is the product decision. A note is a
    # question about one passage the reader is pointing at; handing the model
    # forty pages of unrelated prose buys nothing, costs the whole prompt, and
    # measurably dilutes the answer — the model starts describing the paper
    # instead of the sentence. The anchored path gives it the quote, its
    # neighbours, and the paper's contents index, and lets it pull the rest
    # itself with SEARCH / READ / SECTION.
    #
    # Set True to restore the old behavior for papers under
    # whole_paper_max_tokens.
    paper_whole_document_context: bool = False
    # The ceiling that applies when the setting above is True.
    # ⚠ token_count is len(plain_text)/4 — a character heuristic that
    # undercounts math and tables badly. Leave headroom below the real window.
    whole_paper_max_tokens: int = 120_000
    # How many SEARCH/READ rounds the agent may run before it is forced to
    # answer with whatever it has gathered.
    paper_agent_max_steps: int = 4
    # Ceiling on how many chunks a single READ may pull back, so one greedy
    # range request cannot blow the context window.
    paper_agent_read_max_chunks: int = 40
    # Ceiling on how many chunks a single SEARCH may return as hits.
    paper_agent_search_limit: int = 8
    # How many blocks the contents index falls back to sampling when a paper
    # has no detected headings at all, so the model still gets a map of it.
    paper_agent_map_stride: int = 12
    # Ceiling on results returned by one WEB call. Kept small: web extracts are
    # long, and three good sources beat eight that crowd out the paper's own
    # text in the prompt.
    paper_agent_web_limit: int = 4
    # How many rounds a HOLISTIC question gets (asked from the panel about the
    # whole paper rather than about a passage). Higher than the anchored
    # default because there is no anchor doing half the retrieval work: the
    # agent has to find the relevant sections before it can read them.
    paper_agent_holistic_max_steps: int = 6
    # How many opening blocks a holistic question is shown as orientation —
    # normally title, authors, and abstract. The contents index says what
    # exists; this says what the paper claims to be about.
    paper_agent_opening_blocks: int = 6

    # ── Study agent (answering across a group of papers) ────────────────────
    # Tool rounds a desk question gets. Higher than either paper-agent budget
    # because the first round or two are usually spent working out WHICH papers
    # the question turns on, before any of them has been read.
    study_agent_max_steps: int = 8
    # Ceiling on papers one study may hold. The index is headings only, so the
    # prompt cost is modest, but every paper is a full chunk load per question —
    # this is a guard against a study of the entire library.
    study_max_papers: int = 24

    # ── Paper-only mode ─────────────────────────────────────────────────────
    # Skip the embedding pass for documents small enough to fit whole in the
    # chat model's context, serving GLOBAL from full-text search + document
    # stuffing instead of pgvector. See
    # docs/plans/paper-only-embedding-skip.md.
    #
    # Default False so upgrading changes nothing. The decision is made once, at
    # ingestion, and stored in documents.embedding_mode — it is never
    # re-derived, so flipping this (or the threshold) never reclassifies a
    # library that is already ingested.
    paper_only_mode: bool = False
    # Skip only when SUM(chunks.token_count) is at or below this.
    # NOT set near the model's full context: the document body has to share the
    # window with the system prompt, conversation history, and the answer.
    # ⚠ token_count is len(plain_text)/4 — a character heuristic that
    # undercounts math and tables badly, which is most of what this app ingests.
    # 50k against a 262k window absorbs that error; 250k would not.
    paper_only_max_tokens: int = 50_000

    # LOCAL context window size (number of chunks on each side of the current one)
    local_context_window: int = 3   # Increased from 2 for better "see surrounding" experience

    # Stored embedding dimension. Embeddings larger than this are truncated and
    # re-normalized (valid for MRL-trained models: qwen3-embedding,
    # text-embedding-3-*, gemini-embedding); smaller ones are zero-padded.
    # Keep ≤ 2000: pgvector's HNSW index has a hard 2000-dim limit, and without
    # the index every search is a brute-force scan of all embeddings.
    # Changing this triggers an automatic re-embed of the library on next start.
    vector_dimension: int = 1024

    # ── Web search ──────────────────────────────────────────────────────────
    # Which provider serves the EXTERNAL route, the research agent, and the
    # paper agent's WEB tool. "auto" (default): Tavily when TAVILY_API_KEY is
    # set, otherwise SearXNG. Pin to "tavily", "searxng", or "none".
    #
    # ⚠ This is a privacy setting as much as a quality one. SearXNG runs in the
    # compose stack, so a query never leaves the host; Tavily is a third party
    # and the query string does. Neither ever receives paper text, chunks, or
    # chat history. See app/search/web.py.
    web_search_provider: str = "auto"

    # Tavily (https://tavily.com) — search built for agents: ranked, already
    # extracted page content instead of a SERP that still needs scraping.
    tavily_api_key: str = ""
    # "basic" (one credit, fast) or "advanced" (two credits, deeper extraction
    # and better recall on niche research queries).
    tavily_search_depth: str = "basic"

    # SearXNG — the previous default, kept as the local-only alternative.
    searxng_url: str = "http://localhost:8080"

    # Upload limits
    max_upload_size_mb: int = 100

    # Max characters of a chunk's text sent to the embedder. Ollama's
    # /api/embed hard-400s when inputs exceed the model context window (dense
    # tables tokenize heavily — ~3000 chars is a safe ceiling for local
    # models). Cloud embedders have larger windows; raise this accordingly.
    embed_max_chars: int = 3000

    # ── Security ────────────────────────────────────────────────────────────
    # Comma-separated list of allowed browser origins. Add your LAN address
    # (e.g. "http://192.168.1.50:5173") when serving the dev frontend to other
    # machines. Irrelevant in single-port SPA mode (same origin, no CORS).
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
    # Per-client-IP request ceiling across all /api routes. Generous enough for
    # the UI's polling, low enough to blunt scripted abuse. 0 disables.
    rate_limit_per_minute: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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

