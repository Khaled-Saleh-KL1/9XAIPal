# Roadmap: known gaps & future work

> **What this is:** everything the system does **not** do, or does badly, in one place. Kept out
> of the how-it-works docs on purpose: a reference doc that mixes current behavior with
> aspiration teaches the reader to distrust both.
>
> **Owns:** known gaps, debt, and planned direction.
> **Does not own:** how anything currently works.
>
> **Status:** current · **Last verified:** 2026-07-25 (`main`, ad43845)

Tense tags are load-bearing here: unmarked = a verified current gap, `[planned]` = intended work,
`[historical]` = context for why something looks the way it does.

---

## Engineering scaffolding

The application code is more mature than the tooling around it. These are the cheapest, highest
-leverage fixes in the repo.

| Gap | Impact | Fix |
| --- | --- | --- |
| **No CI at all**: no `.github/` | 7 test files exist and nothing runs them | A workflow running `pytest` + `tsc --noEmit` + `npm run build` |
| ~~**Nothing is pinned**~~ | ~~`fastapi`, `sqlalchemy`, `httpx` all floated in `requirements.txt`~~ | **Fixed 2026-08-29**: migrated to `pyproject.toml` + `uv.lock`, which pins every dependency (including transitive ones) exactly |
| ~~**`pyproject.toml` is gitignored**~~ | ~~It was not in the clone, so `pip install -e .` failed for everyone~~ | **Fixed 2026-08-29**: `pyproject.toml`/`uv.lock` removed from `.gitignore` and committed |
| **No linter or formatter** | No ruff/black/ESLint anywhere | Add ruff + an ESLint config |
| ~~No pytest config~~ | ~~async fixtures in `conftest.py` were collected but never run, failing with an opaque `assert not self._finalizers`~~ | **Fixed 2026-07-26**: `backend/pytest.ini` sets `asyncio_mode = auto` |
| **`.gitignore` ignores `*.env`** | Would also ignore `.env.example` if it were named `example.env`, which is fragile | Narrow the pattern |

## Testing

- **[`test_context_router.py`](../backend/tests/test_context_router.py) is a one-line placeholder
  comment.** The route table is the single most behavior-defining piece of logic in the app and it
  is untested.
- **The two largest and most complex modules have no direct tests**:
  `chat/orchestrator.py` (1061 lines) and `extraction/chunker.py` (1141 lines).
- ⚠ **`conftest.py` `TRUNCATE`s a real database.** Tests cannot run without live Postgres, and
  pointing them at a dev DB destroys it. There is no isolation and no throwaway-DB guard.
- ⚠ **`test_chunk_sequence.py::test_embedding_batching_resumption_and_casting` fails**:
  `psycopg2.errors.DataException: expected 1024 dimensions, not 4096`. The test mocks
  `get_embeddings_batch_sync` with hardcoded 4096-dim vectors, bypassing the MRL truncation that
  normally coerces them to `VECTOR_DIMENSION`. Pre-existing (verified by stashing unrelated
  changes); it was simply invisible until `backend/pytest.ini` made the suite runnable. Fix by
  mocking at the layer above the truncation, or by deriving the mock width from settings.
- No frontend tests of any kind.

## Data & schema

- ~~**`chunks.page_start` / `page_end` are nullable and never populated**~~: **wrong when
  written, corrected 2026-09-08.** The `content_list.json` chunker has always converted MinerU's
  0-based `page_idx` to a 1-based page (`extraction/chunker.py`), and `pipeline_sync.py` writes
  both columns. Measured on the live database: **6132 of 6521 chunks carry a page**, and the 389
  that do not are whole documents rather than gaps — every `doc_kind='article'` row, which is an
  imported web page with no pages to have. What was actually missing was the *surface*: nothing
  displayed the column, so a quoted passage could not say where it came from. Shipped 2026-09-08,
  see [plans/page-numbers-in-citations.md](plans/page-numbers-in-citations.md).
- **A PDF that falls back to markdown chunking still has no pages.** Only the `content_list.json`
  path carries `page_idx`; the PyMuPDF fallback produces none, so those documents cite by
  paragraph. Not a regression, and the UI degrades to `¶N` on its own, but it is the remaining
  half of "every citation has a page".
- **`chunk_assets.caption`, `.width`, `.height` are reserved fields**, always null.
- **`ask_traces.retrieved_chunk_ids` is always null**, reserved.
- ⚠ **Migrations are best-effort by design.** [`migrations.py`](../backend/app/database/migrations.py)
  catches every per-statement exception, logs a warning, and continues; a second pass
  (`_ensure_recent_columns`) patches up what failed. It is self-described as a recovery mechanism.
  It works until it silently does not. `[planned]` Alembic.

## Security

- **Rate limiting is per-process and in-memory**, so with `--workers 2` the real ceiling is double
  the configured value. Documented honestly in the middleware docstring; a known tradeoff, not a
  bug.
- ⚠ **Static mounts bypass auth entirely.** `/static/{images,extracted,assets}` are plain
  `StaticFiles` mounts (`app/main.py`) with no `get_current_user` dependency: the JSON API is
  per-user isolated (see [auth.md](../02-architecture/auth.md)), but a caller who already knows or
  guesses a file path reads it with no login and no ownership check. Paths are UUID-derived, not
  sequential, so this is not trivially enumerable, but it is not access-controlled either.
- ~~**`READ` escaped the reader's progress ceiling**~~: **fixed 2026-09-08.** `SECTION` and
  `SEARCH` were clamped by `max_sequence_id`; `READ` took its range from the model's own numbers
  and queried the database directly. Measured on a 3663-block book with a ceiling of 20,
  `READ 1-400` returned 40 blocks, 20 past the ceiling — so a book being read one unit at a time
  could have the rest of it fetched in a single call, with the reading-companion prompt then
  earnestly not mentioning it. A spoiler bug, not a security one (see the ⚠ in
  [chat-and-ask.md](../02-architecture/chat-and-ask.md#the-progress-ceiling)), but it silently
  defeated the feature it belonged to.
- **Default Postgres password** ships in `.env.example`. Startup warns, but nothing enforces.
- **90 `except Exception` blocks** across the backend. Zero bare `except:`, which is good
  discipline, but that density means genuine failures can be logged and swallowed.

## Product gaps

- **Cross-paper search is not surfaced.** `search_chunks` already accepts `document_id=None`, so
  the retrieval layer supports a library-wide GLOBAL route, but the orchestrator simply never calls
  it. This is the closest thing to free functionality in the repo.
- **No cleanup for research images.** They accumulate under
  `images/research/<conversation_id>/` forever.
- **`DELETE /papers/{id}` disk cleanup is best-effort**; orphans are tolerated and never
  garbage-collected on a schedule.
- **Section summarization is single-pass** per `(document, prompt template)`. Long books may
  exceed the model's effective context.
- **No multi-tenant isolation**: one database, all data shared.
- **No retry queue** for failed ingestions beyond `embed_document`'s in-Celery retries.
- **Web-search images outside the research agent are not persisted**: remote URLs in older chat
  answers rot.

## Structural debt

- **Two files over 1000 lines**: `chat/orchestrator.py` and `extraction/chunker.py`. Splitting the
  orchestrator's four context strategies into a dispatch table would help most.
- **`BookReadingView.tsx` is ~1320 lines**: the old reveal reader, now reached only for
  `doc_kind='book'`. Untouched by the article-reader work and still the largest frontend file.
- **`ChatPane.tsx` is reachable only from the book reader.** Papers never mount it. If books are
  ever retired, it and the four `/ask` context strategies go with them.
- **Two pipelines exist**: `extraction/pipeline.py` (async, legacy) and `pipeline_sync.py` (used by
  Celery). `[historical]` The async in-process `BackgroundTasks` + `asyncio.Queue` design was
  replaced by Celery; the async pipeline survives as a fast path. One of them should go.
- **Naming is inconsistent across the repo**: the directory is `ScholarFlow`, everything inside
  (README, database, containers, volumes) says `9XAIPal`. ⚠ Before "fixing" it: the Celery app name
  is part of the task name on the wire, so renaming it desynchronises the API from the worker.
  Tried on a fork: the API dispatched `<newname>.process_ingestion`, the worker still registered
  `9xaipal.process_ingestion`, and Celery **discarded the message**. A live ingestion sat at
  "queued · 0%" indefinitely with nothing in the UI to suggest the task was gone. If it is renamed,
  `POSTGRES_DB`, the role, container names, volume names and the Celery app all have to move
  together, and `api` + `celery_worker` must be recreated in the same step.

## Planned direction

- ~~**Replace SearXNG with Exa + Firecrawl**~~: **superseded 2026-08-26** by **Tavily**, then
  **2026-08-31** by a 6-provider cascade (google, tavily, linkup, exa, serpapi) with a
  DuckDuckGo library fallback needing no key at all — see
  [`app/search/web.py`](../backend/app/search/web.py). SearXNG itself is gone; Exa ended up
  joining the cascade anyway, just not paired with Firecrawl. Original design, never implemented:
  [archive/2026-08-26/exa-firecrawl-research-stack.md](archive/2026-08-26/exa-firecrawl-research-stack.md).
- ~~**Paper-only mode**~~: **superseded 2026-07-25** by `INGEST_PROFILE=fast`, which skips the
  whole post-chunking chain for papers rather than embeddings alone, and answers at question time
  via [`chat/paper_agent.py`](../backend/app/chat/paper_agent.py). The `PAPER_ONLY_*` settings
  still govern `INGEST_PROFILE=full` and books. Original design:
  [plans/paper-only-embedding-skip.md](plans/paper-only-embedding-skip.md).
- `[planned]` **Alembic** for schema evolution, replacing best-effort `schema.sql` application.
- ~~**Cross-paper questions**~~: **shipped 2026-08-26** as the desk. A *study* (a named group of
  papers, or the whole library) scopes a chat answered by
  [`chat/study_agent.py`](../backend/app/chat/study_agent.py); citations are `[[P2:41]]` and expand
  inline. Still open underneath it: the study index is heading spines only, so a paper MinerU found
  no headings in can be searched but not browsed.
- ~~Second PDF extractor (PaddleOCR-VL)~~: **evaluated and rejected 2026-07-25.** MinerU 3.4.4
  produced correct two-column reading order with zero inversions and zero fragmented equations on
  the backend this app already uses; the premise did not reproduce. Measured results:
  [plans/pdf-parser-evaluation.md §0](plans/pdf-parser-evaluation.md).
- ~~Pin `mineru` in `requirements.txt`~~: **done 2026-07-26.** `mineru[core]>=3.4.4`.
- `[planned]` **`reconstruct_reading_order` removal**, measured at n=28 (16 two-column):
  **0 reading-order inversions**, so it looks vestigial. ⚠ Blocked on testing 3–5 *scanned*
  PDFs, the one document class the corpus could not cover.
  Results: [plans/mineru-heuristic-removal.md §0](plans/mineru-heuristic-removal.md).
- ~~Delete the chunker equation heuristics~~: **measured and rejected 2026-07-26.**
  `_stitch_split_equations` still catches 8 real orphan equation labels (Planck, WMAP5), and
  `_normalize_math_glyphs` repairs **1,930** Unicode glyphs inside math on the PyMuPDF fallback
  path. Both stay.
- `[planned]` Reference-manager integration (Zotero, Semantic Scholar) as a document source.
