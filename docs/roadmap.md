# Roadmap — known gaps & future work

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
| **No CI at all** — no `.github/` | 7 test files exist and nothing runs them | A workflow running `pytest` + `tsc --noEmit` + `npm run build` |
| **Nothing is pinned** in `requirements.txt` | `fastapi`, `sqlalchemy`, `httpx` all float. A fresh install in six months may not work | Pin, or generate a lockfile |
| ⚠ **`pyproject.toml` is gitignored** | It is not in the clone, so `pip install -e .` fails for everyone. Also why there is no lint/format config | Remove it from `.gitignore` and commit it |
| **No linter or formatter** | No ruff/black/ESLint anywhere | Add ruff + an ESLint config |
| ~~No pytest config~~ | ~~async fixtures in `conftest.py` were collected but never run, failing with an opaque `assert not self._finalizers`~~ | **Fixed 2026-07-26** — `backend/pytest.ini` sets `asyncio_mode = auto` |
| **`.gitignore` ignores `*.env`** | Would also ignore `.env.example` if it were named `example.env` — fragile | Narrow the pattern |

## Testing

- **[`test_context_router.py`](../backend/tests/test_context_router.py) is a one-line placeholder
  comment.** The route table is the single most behavior-defining piece of logic in the app and it
  is untested.
- **The two largest and most complex modules have no direct tests** —
  `chat/orchestrator.py` (1061 lines) and `extraction/chunker.py` (1141 lines).
- ⚠ **`conftest.py` `TRUNCATE`s a real database.** Tests cannot run without live Postgres, and
  pointing them at a dev DB destroys it. There is no isolation and no throwaway-DB guard.
- ⚠ **`test_chunk_sequence.py::test_embedding_batching_resumption_and_casting` fails** —
  `psycopg2.errors.DataException: expected 1024 dimensions, not 4096`. The test mocks
  `get_embeddings_batch_sync` with hardcoded 4096-dim vectors, bypassing the MRL truncation that
  normally coerces them to `VECTOR_DIMENSION`. Pre-existing (verified by stashing unrelated
  changes); it was simply invisible until `backend/pytest.ini` made the suite runnable. Fix by
  mocking at the layer above the truncation, or by deriving the mock width from settings.
- No frontend tests of any kind.

## Data & schema

- **`chunks.page_start` / `page_end` are nullable and never populated** — MinerU page metadata is
  not wired through. Page-based citation is therefore impossible today.
- **`chunk_assets.caption`, `.width`, `.height` are reserved fields**, always null.
- **`ask_traces.retrieved_chunk_ids` is always null** — reserved.
- ⚠ **Migrations are best-effort by design.** [`migrations.py`](../backend/app/database/migrations.py)
  catches every per-statement exception, logs a warning, and continues; a second pass
  (`_ensure_recent_columns`) patches up what failed. It is self-described as a recovery mechanism.
  It works until it silently does not. `[planned]` Alembic.

## Security

- ⚠ **No authentication anywhere.** The API is open to any caller that can reach `:8000`. This is
  acceptable on localhost and **not** acceptable in the LAN-server mode the repo ships a script
  for. A shared-token header would be a small addition.
- **Rate limiting is per-process and in-memory** — with `--workers 2` the real ceiling is double
  the configured value. Documented honestly in the middleware docstring; a known tradeoff, not a
  bug.
- **Static mounts expose everything under `storage/`** at `/static/*`. Treat uploaded PDFs and
  extracted assets as world-readable to anyone who can reach the API.
- **Default Postgres password** ships in `.env.example`. Startup warns, but nothing enforces.
- **90 `except Exception` blocks** across the backend. Zero bare `except:`, which is good
  discipline — but that density means genuine failures can be logged and swallowed.

## Product gaps

- **Cross-paper search is not surfaced.** `search_chunks` already accepts `document_id=None`, so
  the retrieval layer supports a library-wide GLOBAL route — the orchestrator simply never calls
  it. This is the closest thing to free functionality in the repo.
- **No cleanup for research images.** They accumulate under
  `images/research/<conversation_id>/` forever.
- **`DELETE /papers/{id}` disk cleanup is best-effort**; orphans are tolerated and never
  garbage-collected on a schedule.
- **Section summarization is single-pass** per `(document, prompt template)`. Long books may
  exceed the model's effective context.
- **No multi-tenant isolation** — one database, all data shared.
- **No retry queue** for failed ingestions beyond `embed_document`'s in-Celery retries.
- **Web-search images outside the research agent are not persisted** — remote URLs in older chat
  answers rot.

## Structural debt

- **Two files over 1000 lines**: `chat/orchestrator.py` and `extraction/chunker.py`. Splitting the
  orchestrator's four context strategies into a dispatch table would help most.
- **`BookReadingView.tsx` is ~1320 lines** — the old reveal reader, now reached only for
  `doc_kind='book'`. Untouched by the article-reader work and still the largest frontend file.
- **`ChatPane.tsx` is reachable only from the book reader.** Papers never mount it. If books are
  ever retired, it and the four `/ask` context strategies go with them.
- **Two pipelines exist**: `extraction/pipeline.py` (async, legacy) and `pipeline_sync.py` (used by
  Celery). `[historical]` The async in-process `BackgroundTasks` + `asyncio.Queue` design was
  replaced by Celery; the async pipeline survives as a fast path. One of them should go.
- **Naming is inconsistent across the repo** — the directory is `ScholarFlow`, everything inside
  (README, database, containers, volumes) says `9XAIPal`. A rename must move
  `POSTGRES_DB`, container names, and volume names together.

## Planned direction

- `[planned]` **Replace SearXNG with Exa + Firecrawl** — semantic search plus real page reading,
  so the research agent stops synthesizing from 280-character snippets. Full design:
  [plans/exa-firecrawl-research-stack.md](plans/exa-firecrawl-research-stack.md).
- ~~**Paper-only mode**~~ — **superseded 2026-07-25** by `INGEST_PROFILE=fast`, which skips the
  whole post-chunking chain for papers rather than embeddings alone, and answers at question time
  via [`chat/paper_agent.py`](../backend/app/chat/paper_agent.py). The `PAPER_ONLY_*` settings
  still govern `INGEST_PROFILE=full` and books. Original design:
  [plans/paper-only-embedding-skip.md](plans/paper-only-embedding-skip.md).
- `[planned]` **Alembic** for schema evolution, replacing best-effort `schema.sql` application.
- `[planned]` Cross-paper GLOBAL search across the whole library.
- ~~Second PDF extractor (PaddleOCR-VL)~~ — **evaluated and rejected 2026-07-25.** MinerU 3.4.4
  produced correct two-column reading order with zero inversions and zero fragmented equations on
  the backend this app already uses; the premise did not reproduce. Measured results:
  [plans/pdf-parser-evaluation.md §0](plans/pdf-parser-evaluation.md).
- ~~Pin `mineru` in `requirements.txt`~~ — **done 2026-07-26.** `mineru[core]>=3.4.4`.
- `[planned]` **`reconstruct_reading_order` removal** — measured at n=28 (16 two-column):
  **0 reading-order inversions**, so it looks vestigial. ⚠ Blocked on testing 3–5 *scanned*
  PDFs, the one document class the corpus could not cover.
  Results: [plans/mineru-heuristic-removal.md §0](plans/mineru-heuristic-removal.md).
- ~~Delete the chunker equation heuristics~~ — **measured and rejected 2026-07-26.**
  `_stitch_split_equations` still catches 8 real orphan equation labels (Planck, WMAP5), and
  `_normalize_math_glyphs` repairs **1,930** Unicode glyphs inside math on the PyMuPDF fallback
  path. Both stay.
- `[planned]` Reference-manager integration (Zotero, Semantic Scholar) as a document source.
