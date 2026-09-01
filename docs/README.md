# 9XAIPal: documentation index

> **What this is:** the dispatcher for every doc in this repo. Find your task in the table, start
> at the named doc, verify with the named command. This page routes; it never explains.
>
> **Status:** current · **Reflects code as of:** 2026-08-27 (`502272b`) for the auth routing row
> below; 2026-08-18 (`8fb153b`) for everything else
> **Rule:** when a doc and the code disagree, **the code is authoritative**, and the doc is a
> defect to be fixed in the same unit of work.

---

## Route by task

| If you need to… | Start with | Then verify |
| --- | --- | --- |
| Run the app for the first time | [01-orientation/setup.md](01-orientation/setup.md) | `curl localhost:8000/api/v1/health` |
| Know what listens on which port | [01-orientation/runtime-topology.md](01-orientation/runtime-topology.md) | `docker compose ps` |
| Diagnose something broken | [01-orientation/operations.md](01-orientation/operations.md) | `ask_traces` / `ingestion_jobs` queries in that doc |
| Understand the system end-to-end | [02-architecture/overview.md](02-architecture/overview.md) | n/a |
| Trace a PDF from upload to readable | [02-architecture/ingestion-pipeline.md](02-architecture/ingestion-pipeline.md) | `tests/test_ingestion_pipeline.py` |
| Understand how a question gets answered | [02-architecture/chat-and-ask.md](02-architecture/chat-and-ask.md) | `NOTE[...]` / `ASK[stepN]` log lines |
| Know which model serves which call | [02-architecture/ai-backend.md](02-architecture/ai-backend.md) | `tests/test_provider_resolver.py` |
| Understand sessions, signup, or per-user data scoping | [02-architecture/auth.md](02-architecture/auth.md) | `tests/test_auth_http.py`, `tests/test_ownership.py` |
| Work on the React UI | [02-architecture/frontend.md](02-architecture/frontend.md) | `npm run build` |
| Look up an endpoint | [03-reference/api.md](03-reference/api.md) | `localhost:8000/docs` |
| Look up a table or column | [03-reference/database-schema.md](03-reference/database-schema.md) | `backend/app/database/schema.sql` |
| Set an environment variable | [03-reference/configuration.md](03-reference/configuration.md) | `backend/app/core/config.py` |
| Find a file on disk / a static URL | [03-reference/storage.md](03-reference/storage.md) | `ls backend/app/storage` |
| Change the schema | [03-reference/migrations.md](03-reference/migrations.md) | restart the API; watch migration logs |
| Test a release | [04-testing/test-plan.md](04-testing/test-plan.md) | `cd backend && POSTGRES_DB=9xaipal_test pytest -v` |
| Know what's broken or missing by design | [roadmap.md](roadmap.md) | n/a |
| Check whether an idea was already tried and rejected | [decisions.md](decisions.md) | n/a |
| Read or write a plan | [plans/](plans/) | n/a |

---

## Glossary

Vocabulary used across every doc. These are the literal identifiers in code: search for these
strings, not for synonyms.

| Term | Means |
| --- | --- |
| **chunk** | One structural unit of a document: heading, paragraph, math block, table, or figure. Row in `chunks`. The atom of reading, retrieval, and note anchoring. |
| **block** | A chunk as the article reader renders it. Same row, reader-facing name: `data-seq` on the DOM element is its `sequence_id`. |
| **note** | One anchored question + answer, rendered in the reader's margin. Row in `paper_notes`. The paper equivalent of a chat turn. |
| **personal note** | Something the *reader* wrote, anchored the same way. Row in `personal_notes`. Distinct from **note** above, which is what the model answered. |
| **bookmark** | A marked block, several per paper. Row in `reading_bookmarks`. One per block, enforced by a unique constraint. |
| **deck** | Several margin cards sharing one slot, browsed one at a time. Rows in `note_decks` + `note_deck_members`. Owns nothing: spreading one leaves every card as it was. |
| **anchor** | Where a note hangs: `anchor_sequence_id` plus an `anchor_kind` of `text`, `figure`, `equation`, `table`, or `block`. A `table` anchor covers the whole table, so a selection inside one is promoted to it. |
| **contents** | The paper's heading spine, each entry carrying the block number it starts at. What the paper agent is given instead of the paper, and what `SECTION` takes its argument from. |
| **ingest profile** | `INGEST_PROFILE`: `fast` (a paper is done at chunking) or `full` (the historical embed → summarize chain). Books always take `full`. |
| **paper agent** | `chat/paper_agent.py`: answers a note without embeddings, from the anchor plus the contents index, driving `SECTION`/`SEARCH`/`READ` over chunks. |
| **retrieval_mode** | Which of those two the agent used for a given note: `whole` or `agent`. ⚠ `agent` is now the default for every size of paper; `whole` requires `PAPER_WHOLE_DOCUMENT_CONTEXT`. |
| **`sequence_id`** | 1-based physical reading order within a document. The source of truth for order; vector similarity never redefines it. |
| **route / `context_type`** | Which context source answers a `/ask` question: `LOCAL`, `GLOBAL`, `OVERVIEW`, `EXTERNAL`, or `OUT_OF_SCOPE`. Chosen by `chat/router.py`. ⚠ Applies to books only, notes are never routed. |
| **LOCAL** | Current chunk + neighbours + inline images. |
| **GLOBAL** | pgvector similarity search across one document. |
| **OVERVIEW** | Pre-computed hierarchical summaries (`section_summaries`), no vector search. |
| **EXTERNAL** | Live web search, cascading through tavily → linkup → exa → serpapi → duckduckgo. With the paper agent's `WEB` tool, the only path that reaches the public internet, and only the query string does. |
| **turn** | One message in a conversation. Row in `conversation_turns`. |
| **sub-thread** | A tangent branched off a turn via `parent_turn_id`. Deliberately paper-free. |
| **compaction** | A `role='compaction'` turn holding a dense summary of earlier turns, so long chats don't overflow the context window. |
| **provider / resolver** | The auto-detection in `llm/resolver.py` choosing Ollama vs a cloud API per call. Nothing hardcodes a model. |
| **role** | What a model call is *for*: `chat`, `vlm`, `classifier`, `embedding`. Call sites pass a role, never a model name. |
| **extractor** | `mineru` (full: OCR, tables, LaTeX) or `pymupdf_fallback` (degraded: text only). Recorded per document. |

---

## Conventions

Every doc here follows these. Deviations are defects.

- **Tense tags**: unmarked prose is verified current behavior. `[spec]` = intended,
  `[planned]` = future, `[historical]` = past. Never mixed unmarked.
- **⚠ marks a landmine**: something that will bite you, stated where you'd hit it.
- **Diagrams render twice**: an ASCII chart in a `text` fence (greppable, works in any terminal
  and diff) and a Mermaid chart beside it (for rendered viewers). Both live in markdown: there
  are no HTML documentation artifacts in this repo, by policy.
- **Code anchors over restatement**: docs link to `file.py::symbol` rather than copying
  signatures. A behavioral claim with no code reference is design intent, not verified behavior.
- **One canonical home per fact.** A third mention is a link, never a restatement.

---

## Layout

```text
docs/
├── README.md                  ← you are here (dispatcher + glossary)
├── roadmap.md                 known gaps & future work — kept out of how-it-works docs
├── decisions.md               rejected approaches & deliberate ugliness (append-only)
├── 01-orientation/            get it running, understand the moving parts, fix it when broken
│   ├── setup.md
│   ├── runtime-topology.md
│   └── operations.md
├── 02-architecture/           how it works
│   ├── overview.md            ← master zoom doc; start here and descend
│   ├── ingestion-pipeline.md
│   ├── chat-and-ask.md
│   ├── ai-backend.md
│   └── frontend.md
├── 03-reference/              look-up tables, no narrative
│   ├── api.md
│   ├── database-schema.md
│   ├── configuration.md
│   ├── storage.md
│   └── migrations.md
├── 04-testing/
│   └── test-plan.md
├── plans/                     plans, findings, future work        (tracked)
├── tasks/                     handoff tasks cut from plans        (gitignored)
└── archive/                   completed plans & tasks by date     (tracked)
```

Sample PDFs for testing ingestion live in [`samples/`](../samples/), not here: `docs/` holds
prose only.
