# Test Plan

## Safety

Run the backend suite only with a disposable PostgreSQL database whose name contains test.
The fixtures truncate documents CASCADE, including dependent chunks, assets, conversations,
and notes. conftest.py blocks other database names unless ALLOW_DESTRUCTIVE_TESTS=1 is set.
The override disables a safety check; it does not make a development database safe.

The backend suite uses PostgreSQL and Redis for database, vector, session, queue, and rate-limit
behavior. CI provisions disposable PostgreSQL/pgvector and Redis services; see
[the workflow](../../.github/workflows/ci.yml).

## Coverage map

- Ingestion and chunking: test_ingestion_pipeline.py, test_chunker_*.py,
  test_heading_repair.py, and test_glyph_repair_dropped_f.py.
- Retrieval and embeddings: test_vector_retrieval.py, test_library_search.py,
  test_document_search_embedding.py, test_cross_language_retrieval.py, and
  test_retrieval_reranking.py.
- Arabic routing, OCR, recovery, and language parity: test_arabic_*.py.
- Chat routes, provider fallback, streaming, agent tools, and tracing:
  test_chat_routing_fallback.py, test_llm_client_cascade.py,
  test_agent_tools_streaming.py, and test_tracing_*.py.
- Article fetch, extraction, and generated thumbnails: test_article_*.py,
  test_cloudflare_images.py, and test_backfill_article_thumbnails.py.
- API authentication, ownership, queue capacity, and worker behavior:
  test_auth_http.py, test_ownership.py, test_capacity.py, test_celery_queues.py,
  and test_worker_restore.py.

These are focused tests; they do not constitute a browser end-to-end test of the complete
application. test_context_router.py is still a placeholder and does not test route selection.
The test suite does not verify production integrations or their credentials.

See [the project test plan](../../docs/04-testing/test-plan.md) for the frontend Vitest inventory,
CI scope, and the manual acceptance script.
