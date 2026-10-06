# Queue reliability implementation plan

Goal: implement spec-Q1 fully in the existing queue worktree, with local commits and report.
Execution: inline, already approved by the orchestrator.

Constraints: no secrets, push, SSH, production or provider calls. No OCR routing/quality changes, LLM/chat/model-picker edits, or container interference. Use throwaway Postgres/Redis at 55443/55444.

- [x] DLQ and alerts: write failing real-DB/Redis tests for classifier, terminal Celery signals, unique job failure identity, stalled versus live heartbeat, Redis atomic suppression/cap/concurrency, mocked STARTTLS SMTP and daily summary. Implement centralized failure service, nullable heartbeat/schema migrations guarded by migration advisory lock, light tasks and scripts.
- [x] Backpressure: failing tests for each admission condition, stable codes/header, bounded upload cleanup; extend existing advisory-locked reservation and frontend shared notice/countdown.
- [x] Identity: failing tests for streamed hashes, same-user dedupe, failed-row retry, simultaneous barrier uploads, idempotency replay/mismatch, normalized URL identity and delivery fencing. Extend normal create-job path; add opt-in backfill.
- [x] Scheduling/docs: light worker embedded beat with singleton note, every new setting in templates and compose, operator runbook and architecture. Test CLI help, full backend suite once, full vitest and build.
- [x] Review diff for forbidden areas, secret leaks and concurrency edges, commit sections with required coauthor trailer, write exact report, remove only test containers started here.

Review focus: inspection unavailable must not claim a worker absent; delayed failures cannot overwrite newer jobs; returned failed outcomes need DLQ capture; idempotency Redis loss cannot defeat database identity; duplicate losers leave no files.

Validation note: the required full native backend run has macOS disk-headroom and Linux subprocess-fence failures, plus regressions corrected by subsequent targeted tests. The report records the exact full-suite tail and final reruns; no Linux full-suite success is claimed.
