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
