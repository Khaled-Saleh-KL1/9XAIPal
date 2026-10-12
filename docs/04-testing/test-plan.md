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

The CI workflow runs backend pytest and the frontend TypeScript check/build when their
paths change. It does not run Vitest, browser end-to-end tests, or Markdown/link checks.
Root Markdown and `docs/**` changes alone skip those jobs, but Markdown under
`backend/**` or `frontend/**` matches the corresponding application path filter and
does run that job. See [the CI workflow](../../.github/workflows/ci.yml). The full ask
route through tools, persistence and final response is not covered by one end-to-end
test; `test_context_router.py` remains a placeholder.

⚠ Backend tests use database fixtures that truncate documents CASCADE, which also removes dependent rows. The test configuration refuses to start unless POSTGRES_DB contains test, unless ALLOW_DESTRUCTIVE_TESTS=1 explicitly overrides that guard. Use a disposable test database and Redis; never point the suite at a development library. See [conftest.py](../../backend/tests/conftest.py) and [backend/tests/README.md](../../backend/tests/README.md).

For local execution, run the full backend suite with `POSTGRES_DB` set to a disposable
database whose name contains `test`. Frontend Vitest runs with `npm test -- --run`.
The four standalone version checks can run with `pytest --noconftest tests/test_version.py`
because they read release metadata only; do not use that flag to bypass fixtures for
database-dependent tests. The v3 update validates Markdown links/anchors, catalogue
numbering, locked project metadata and a production frontend build. Full application
coverage and provider/corpus acceptance are separate checks.

---

## 2. Manual acceptance script

### v3 additions

Use an approved isolated library/corpus for document acceptance. Arabic OCR and
provider probes can make external requests; use mocks for unit checks.

| Area | Check | Expected |
| --- | --- | --- |
| Printed Arabic and mixed PDF | Upload under each reading kind with routing configured | Arabic classification/OCR metadata, retained page provenance and RTL content. |
| English control | Upload a clear English PDF | Existing configured extractor and LTR classification. |
| Uncertain style | Exercise a mocked abstention and confirm printed | Same eligible job is queued; HTTP 202 `arabic_ocr_queued`. |
| Manual handwriting | Confirm handwritten on an eligible failed job | Choice stored; unavailable state; no provider request queued. |
| Confident handwriting | Exercise enabled/disabled branches with mocks | Dedicated pipeline when enabled; typed unavailable when disabled. |
| Provider recovery | Simulate gateway/direct failure and Gemma continuation | Only complete pages committed, correct provenance and bounded unreadable-page behavior. |
| PWA | Android install, iPhone guide, desktop QR, controlled update | Appropriate install UI; active uploads/answers are not interrupted by reload. |
| Public discovery | Inspect built homepage/About without JavaScript | Visible Khaled/9XAI/Rami descriptions, consistent JSON-LD IDs and canonical URLs. |
| Crawler files | Request robots, sitemap and AI reference | Plain text/XML content, not an HTML SPA fallback. |
| Models | Simulate definitive and transient provider outcomes | Correct cached picker state; scheduled refresh runs on light worker. |
| Queues | Use same-owner duplicate and controlled failed job | Identity preserved, one admitted job, safe generation-fenced retry. |

Focused source suites: `test_arabic_classifier.py`, `test_arabic_pipeline_routing.py`,
`test_gemini_ocr_client.py`, `test_arabic_ocr_fallback.py`,
`test_arabic_confirmation_api.py`, `test_arabic_document_api.py`,
`test_model_availability_refresh.py`, and frontend `src/pwa/*.test.ts(x)`.
The [evaluation README](../../backend/eval/arabic_documents/README.md) describes
quality measurements; synthetic unit tests do not establish OCR accuracy.

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
| 6 | Open a book for chapter reveal; open a paper for article scrolling | Correct reader for each `doc_kind`; do not expect book controls in the paper reader |
| 7 | Reach a math chunk | KaTeX renders, no raw LaTeX |
| 8 | Reach a figure chunk | Correct image, correct caption |
| 9 | Reach the end | `404` on the next sequence sets the end state cleanly |
| 10 | Open `#/paper/<id>` and refresh | Reading view restores from the URL hash |

### Chat: one per route

| # | Ask | Expect |
| --- | --- | --- |
| 11 | In a book, ask *"What does this figure show?"* with a figure current | `context_type=LOCAL`, answer describes the actual diagram; paper notes use the separate anchored-agent path |
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
| 24 | `POST /papers/{id}/reextract` | Classification/configured extraction runs again; document re-enters the overlay |

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
