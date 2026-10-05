# Test plan

> **What this is:** what is covered automatically, what must be exercised by hand before calling a
> release good, and the acceptance script for a full manual pass.
>
> **Owns:** test inventory and the manual acceptance script.
> **Does not own:** known testing gaps ([roadmap.md](../roadmap.md)), recovery procedures
> ([operations.md](../01-orientation/operations.md)).
>
> **Status:** current · **Reconciled with source:** 2026-10-05; see the linked test files and [CI workflow](../../.github/workflows/ci.yml).
> **Verify with:** `cd backend && POSTGRES_DB=9xaipal_test pytest -v`

---

## 1. Automated checks

The backend suite covers focused API, extraction, retrieval, chat, Arabic OCR, queue, and article-generation behavior. Representative files:

| Area | Coverage |
| --- | --- |
| Ingestion and chunking | [test_ingestion_pipeline.py](../../backend/tests/test_ingestion_pipeline.py), [test_chunker_algorithm_and_broken_tables.py](../../backend/tests/test_chunker_algorithm_and_broken_tables.py), [test_heading_repair.py](../../backend/tests/test_heading_repair.py) |
| Retrieval and library search | [test_vector_retrieval.py](../../backend/tests/test_vector_retrieval.py), [test_library_search.py](../../backend/tests/test_library_search.py), [test_document_search_embedding.py](../../backend/tests/test_document_search_embedding.py) |
| Arabic routing and OCR | [test_arabic_pipeline_routing.py](../../backend/tests/test_arabic_pipeline_routing.py), [test_arabic_ocr_fallback.py](../../backend/tests/test_arabic_ocr_fallback.py), [test_arabic_contextual_embeddings.py](../../backend/tests/test_arabic_contextual_embeddings.py) |
| Chat and provider behavior | [test_chat_routing_fallback.py](../../backend/tests/test_chat_routing_fallback.py), [test_agent_tools_streaming.py](../../backend/tests/test_agent_tools_streaming.py), [test_web_search_cascade.py](../../backend/tests/test_web_search_cascade.py) |
| Queues, capacity, and repair | [test_celery_queues.py](../../backend/tests/test_celery_queues.py), [test_capacity.py](../../backend/tests/test_capacity.py), [test_reembed_library.py](../../backend/tests/test_reembed_library.py) |
| Article thumbnails | [test_article_thumbnail_task.py](../../backend/tests/test_article_thumbnail_task.py), [test_cloudflare_images.py](../../backend/tests/test_cloudflare_images.py), [test_backfill_article_thumbnails.py](../../backend/tests/test_backfill_article_thumbnails.py) |

The frontend has Vitest component and behavior tests, including landing/reduced-motion, motion primitives, PDF selection, reasoning rows, and cover refresh. Examples: [LandingView.test.tsx](../../frontend/src/views/LandingView.test.tsx), [motion.test.tsx](../../frontend/src/motion/motion.test.tsx), [pdfFiles.test.ts](../../frontend/src/lib/pdfFiles.test.ts), [AgentTrail.test.tsx](../../frontend/src/views/AgentTrail.test.tsx), and [PaperCover.test.tsx](../../frontend/src/views/PaperCover.test.tsx).

The CI workflow runs backend pytest and the frontend TypeScript check/build when their paths change. It does not run Vitest, browser end-to-end tests, or Markdown/link checks. Documentation-only changes run the change-detection job but skip the backend and frontend jobs. See [the CI workflow](../../.github/workflows/ci.yml). The full ask route through the context router, agent tools, persistence, and final response is not covered by one end-to-end test. [test_context_router.py](../../backend/tests/test_context_router.py) remains a placeholder.

⚠ Backend tests use database fixtures that truncate documents CASCADE, which also removes dependent rows. The test configuration refuses to start unless POSTGRES_DB contains test, unless ALLOW_DESTRUCTIVE_TESTS=1 explicitly overrides that guard. Use a disposable test database and Redis; never point the suite at a development library. See [conftest.py](../../backend/tests/conftest.py) and [backend/tests/README.md](../../backend/tests/README.md).

For local execution, run pytest from backend with POSTGRES_DB set to a disposable database whose name contains test. Frontend Vitest can be run from frontend with npm test -- --run. Neither suite was run as part of this documentation update.

---

## 2. Manual acceptance script

Run in order against a clean library. Sample paper:
[`samples/attention-is-all-you-need.pdf`](../../samples/).

### Ingestion

| # | Step | Pass criteria |
| --- | --- | --- |
| 1 | `GET /api/v1/health` | All fields `ok` |
| 2 | Drop the sample PDF on the library or choose it through Add paper | The fast research-paper profile shows extracting → chunking; a book or full-profile document may continue through embedding and summarizing |
| 3 | Wait for completion | Reading view renders a heading and first paragraph |
| 4 | Check the extractor badge | Reads `mineru`, not `pymupdf_fallback` |
| 5 | Wait ~5–15 min, then `GET /papers/{id}/figure-descriptions` | Non-empty rows |

### Reading

| # | Step | Pass criteria |
| --- | --- | --- |
| 6 | Click "next", then hold **D** + press **↓** | Chunks reveal one at a time, both paths work |
| 7 | Reach a math chunk | KaTeX renders, no raw LaTeX |
| 8 | Reach a figure chunk | Correct image, correct caption |
| 9 | Reach the end | `404` on the next sequence sets the end state cleanly |
| 10 | Open `#/paper/<id>` and refresh | Reading view restores from the URL hash |

### Chat: one per route

| # | Ask | Expect |
| --- | --- | --- |
| 11 | *"What does this figure show?"* with a figure current | `context_type=LOCAL`, `router_reason` mentions the matched phrase, answer describes the actual diagram |
| 12 | *"What is the encoder-decoder attention mechanism?"* | `context_type=GLOBAL`, citations point at the right chunks |
| 13 | *"Summarize the paper"* | `context_type=OVERVIEW`, answer spans multiple sections |
| 14 | *"What is the latest news on transformer models?"* | `context_type=EXTERNAL`, citations include web URLs |

### Guardrail & domain policy

| # | Ask | Expect |
| --- | --- | --- |
| 15 | *"What's the best treatment for migraines?"* | `"This is out of scope."`, logged as `OUT_OF_SCOPE` |
| 16 | *"What is transduction?"* | Sequence-transduction (CS) answer. **No biology, no genetics** |
| 17 | *"How is attention used in neuroscience?"* | Cross-field trigger fires; answer bridges to neuroscience |
| 18 | Inspect citation chips | Web citations whose URL never appears in the answer body do **not** render |

### Conversation

| # | Step | Pass criteria |
| --- | --- | --- |
| 19 | Send 6+ user turns | `conversation_id` preserved; a `role='compaction'` row appears after ~5 turns; the model stays coherent |
| 20 | `GET /papers/{id}/conversations` | Lists every thread; opening one via `/chat?conversation_id=…` loads its turns |
| 21 | Start a sub-thread from a turn | Replies are paper-free by design; parent turn is unaffected |

### Repair endpoints

| # | Step | Pass criteria |
| --- | --- | --- |
| 22 | `POST /papers/{id}/rechunk` | Chunks rebuilt, embeddings re-queued, MinerU **not** re-run |
| 23 | `POST /papers/{id}/reconstruct-reading-order` | `documents.reading_order` JSONB populated |
| 24 | `POST /papers/{id}/reextract` | MinerU runs again; document re-enters the overlay |

### Deletion & failure

| # | Step | Pass criteria |
| --- | --- | --- |
| 25 | `DELETE /papers/{id}` | `204`; rows gone from all 7 tables; files gone from `documents/`, `assets/`, `extracted/`, `images/` |
| 26 | After deletion, check `conversation_turns` | **Rows survive** with `document_id = NULL`: this is intended |
| 27 | Stop Ollama, ask a question | Polite error, no crash. Restart Ollama → next ask succeeds |
| 28 | Stop Redis, upload a PDF | Document marked `failed` with an actionable `error_message` |

---

## 3. Performance sanity

Compare against the baselines in
[operations.md §4](../01-orientation/operations.md#4-performance-baselines). A 3× regression on
any row is worth investigating before release; the usual causes are an unset `CLASSIFIER_MODEL`
or a model being evicted between requests (`OLLAMA_KEEP_ALIVE` too short).
