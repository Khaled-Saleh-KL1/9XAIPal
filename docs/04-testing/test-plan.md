# Test plan

> **What this is:** what is covered automatically, what must be exercised by hand before calling a
> release good, and the acceptance script for a full manual pass.
>
> **Owns:** test inventory and the manual acceptance script.
> **Does not own:** known testing gaps ([roadmap.md](../roadmap.md)), recovery procedures
> ([operations.md](../01-orientation/operations.md)).
>
> **Status:** current · **Last verified:** 2026-07-25
> **Verify with:** `cd backend && POSTGRES_DB=9xaipal_test pytest -v`

---

## 1. Automated

```bash
cd backend && source .venv/bin/activate
POSTGRES_DB=9xaipal_test pytest -v
```

| File | Covers |
| --- | --- |
| [`test_chunk_sequence.py`](../../backend/tests/test_chunk_sequence.py) | Chunker sequence numbering + structural type detection |
| [`test_ingestion_pipeline.py`](../../backend/tests/test_ingestion_pipeline.py) | End-to-end pipeline (extractor stub → chunks → assets); the fast/full profile split — a paper completes at chunking with nothing dispatched, a book still dispatches `embed_document` |
| [`test_vector_retrieval.py`](../../backend/tests/test_vector_retrieval.py) | `search_chunks` against pgvector with deterministic vectors |
| [`test_provider_resolver.py`](../../backend/tests/test_provider_resolver.py) | Provider auto-detection, fallback order, namespace isolation, `NoLLMConfigured` |
| [`test_subthread_conversations.py`](../../backend/tests/test_subthread_conversations.py) | Sub-thread trees via `parent_turn_id`, recursive history |
| [`test_context_router.py`](../../backend/tests/test_context_router.py) | ⚠ **Placeholder — a single comment line. Covers nothing.** |

⚠ **These tests `TRUNCATE documents CASCADE` against whatever `POSTGRES_DB` resolves to**, before
and after every test. That cascade takes chunks, assets, conversations, and notes with it — run it
against the development database and the library is gone, with only the PDFs left on disk. This
has happened.

`conftest.py` therefore refuses to start unless the database name contains "test":

```bash
POSTGRES_DB=9xaipal_test pytest -v          # the normal way
ALLOW_DESTRUCTIVE_TESTS=1 pytest -v         # override, if you mean it
```

First-time setup of the scratch database:

```bash
docker exec 9xaipal-postgres psql -U 9xaipal -d postgres -c 'CREATE DATABASE "9xaipal_test"'
docker exec 9xaipal-postgres psql -U 9xaipal -d 9xaipal_test \
  -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

**Not covered by anything:** `chat/orchestrator.py`, `chat/paper_agent.py`, `extraction/chunker.py`,
`extraction/glyph_repair.py`, the notes API, and the entire frontend. See
[roadmap.md](../roadmap.md).

⚠ `test_chunk_sequence.py::test_embedding_batching_resumption_and_casting` fails on a default
setup: it builds 4096-dimension vectors while the column is `vector(1024)` per `VECTOR_DIMENSION`.
Pre-existing, unrelated to ingestion correctness.

---

## 2. Manual acceptance script

Run in order against a clean library. Sample paper:
[`samples/attention-is-all-you-need.pdf`](../../samples/).

### Ingestion

| # | Step | Pass criteria |
| --- | --- | --- |
| 1 | `GET /api/v1/health` | All fields `ok` |
| 2 | Drag the sample PDF onto the library | Overlay shows `extracting → chunking → embedding` |
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

### Chat — one per route

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
| 26 | After deletion, check `conversation_turns` | **Rows survive** with `document_id = NULL` — this is intended |
| 27 | Stop Ollama, ask a question | Polite error, no crash. Restart Ollama → next ask succeeds |
| 28 | Stop Redis, upload a PDF | Document marked `failed` with an actionable `error_message` |

---

## 3. Performance sanity

Compare against the baselines in
[operations.md §4](../01-orientation/operations.md#4-performance-baselines). A 3× regression on
any row is worth investigating before release; the usual causes are an unset `CLASSIFIER_MODEL`
or a model being evicted between requests (`OLLAMA_KEEP_ALIVE` too short).
