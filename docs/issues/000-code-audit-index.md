# Code audit findings

The following issues were found during a repository-wide audit on 2026-09-10
and remediated on `fix/audit-findings`. They are ordered by area rather than
priority; each report records its severity, trigger, root cause, and repair.

| Report | Severity | Finding |
|---|---|---|
| [001](001-public-static-files-bypass-authorization.md) | High | Static document and research files bypass authorization |
| [002](002-web-search-endpoint-is-public.md) | High | Web search can spend provider quota without authentication |
| [003](003-deck-upsert-can-mutate-another-document.md) | High | A caller-controlled deck ID can mutate another document's deck |
| [004](004-conversation-preview-can-leak-another-users-prompt.md) | High | Conversation preview subquery is missing tenant filters |
| [005](005-conversation-ids-are-not-bound-to-the-paper.md) | Medium | Chat history can bleed between a user's documents |
| [006](006-capacity-admission-is-neither-atomic-nor-fifo.md) | High | Capacity admission can exceed its cap and skip the queue |
| [007](007-ingestion-queue-capacity-check-races.md) | Medium | Concurrent imports can exceed the ingestion queue cap |
| [008](008-reference-import-dispatch-failure-stays-queued.md) | Medium | Failed reference imports remain queued forever |
| [009](009-migration-recovery-uses-an-aborted-transaction.md) | High | One failed recovery statement disables all later recovery |
| [010](010-upload-limit-is-checked-after-buffering.md) | High | PDF upload limit does not protect process memory |
| [011](011-image-proxy-limit-is-checked-after-buffering.md) | High | Image proxy limit does not protect process memory |
| [012](012-ssrf-check-has-a-dns-rebinding-window.md) | High | SSRF validation and connection use separate DNS lookups |
| [013](013-cross-origin-frontend-mode-cannot-stay-authenticated.md) | High | Hosted frontend mode omits credentials and uses wrong media origin |
| [014](014-book-paragraph-reveal-shows-content-in-advance.md) | Medium | Book paragraphs are rendered before the reveal cursor advances |
| [015](015-ai-reading-order-toggle-is-a-no-op.md) | Medium | AI reading order is displayed but never used |
| [016](016-book-fetch-errors-are-reported-as-end-of-content.md) | Medium | Transient reader errors permanently become end-of-content |
| [017](017-router-warning-uses-an-undefined-logger.md) | Low | Router diagnostic path raises NameError and is silently swallowed |
| [018](018-concurrent-signups-return-500.md) | Medium | Concurrent signup for one email returns an internal error |

## Audit validation

- Frontend TypeScript and production Vite build passed.
- Python bytecode compilation passed.
- Focused Ruff correctness checks passed after the fixes.
- Focused database-independent backend tests passed.
- The full backend suite (613 tests) passed on 2026-09-11 against a local
  PostgreSQL 16 + pgvector and Redis. That run caught a regression in the
  first fix for 005: `(:document_id IS NULL OR ...)` left asyncpg unable to
  infer the parameter type (`AmbiguousParameterError`), which would have broken
  conversation-history loading in chat. Repaired with `CAST(:document_id AS uuid)`.

## Verified on the VPS (2026-09-10)

The audit and both fix passes ran off-box. What only the deployment host could
answer was checked there, against a throwaway stack built from the production
`backend-api` image with this branch's `app/` mounted, a fresh
`pgvector/pgvector:pg16` and `redis:7-alpine`, and the live *Attention Is All
You Need* rows and files copied in:

- Full backend suite on that image: **592 passed** (21 deselected:
  `test_multi_key_rotation.py` asserts on the provider cascade and reads the
  real `NVIDIA_API_KEY`/`OLLAMA_API_KEY` from the environment, so it only
  passes in an environment with no keys — unrelated to these fixes).
- Over real HTTP: `/static/assets/<id>.pdf` → 404; `/raw` and
  `/papers/{id}/assets/…` → 401 anonymous, the bytes for the owner, 404 for
  `../` and `%2F` traversal (001). `/search/web` → 401 anonymous (002). A
  second user reusing the first user's deck id → 404 and no row touched; a
  foreign note id smuggled into a deck → dropped (003). A conversation id
  used from another user or another paper → 404 on `/chat`, `/ask`,
  `/ask/stream`, and a foreign `parent_turn_id` → 404 before any model call
  (004, 005). A 3 MB upload against a 1 MB cap → 413 with no file left on
  disk; a non-PDF → 415 (010). The image proxy refuses a private host, a
  loopback address and a public host that *redirects* to loopback, and
  fetches a real HTTPS image through the DNS-pinned transport with SNI
  intact (011, 012). Six concurrent signups for one email → one 201, five
  409, no 500 (018).
- The book reader rendered in a real DOM (happy-dom + react-dom, fake fetch):
  a fresh session reveals one unit, each *next* reveals exactly one more
  across a chunk boundary (014); a 500 on the next chunk shows *Retry* and
  does not become end-of-content, and Retry resumes (016); with the toggle on
  the reader walks `reading_order` 3→1→2 and never calls the physical
  `/chunks/after/` route (015).
- Live data needs no migration: no `/static/` URL is persisted anywhere
  (turns, notes, agent steps, chunk markdown), and no conversation id spans
  two documents or two users, so the new scoping rejects nothing that exists.
- The deployment itself was the one thing the fixes could not reach:
  `backend/nginx/9xaipal.conf` still declared `/static/*` "not optional" and
  proxied it, every reference doc still described the public mounts, and the
  PDF viewer still loaded the PDF without credentials (fine same-origin,
  a 401 in the hosted mode 013 is about). All three are fixed on this branch;
  the host's installed nginx config only needs `static` dropped from the
  proxy regex, which is cosmetic (the API answers 404 there either way).
