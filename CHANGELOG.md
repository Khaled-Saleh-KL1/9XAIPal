# Changelog

## v3 — Arabic document routing

Version `3.0.0`: 2026-10-12. Feature documentation reconciled with the merged
source at `d73d5e9`; the release synchronizes API and package version metadata.

### Added and updated

- Arabic-aware classification before PDF extraction, including English, printed Arabic,
  mixed-language PDFs, confident handwriting and an uncertain-style confirmation state.
- Arabic OCR through the configured ModelGateway/direct Gemini cascade, with recoverable
  Gemma fallback, page-boundary validation, provider provenance and RTL content rendering.
- Android/iPhone installation, QR handoff and automatic PWA update handling.
- Cached model availability with scheduled light-worker refresh.
- Upload reservations and identity, execution fencing, progress observation, durable
  failed-job records, retry tooling and optional owner alerts.
- Public discovery metadata and static About content identifying Khaled Saleh, his 9XAI
  fellowship, the program at HTU, and its founder and leader, Rami Alkarmi.

### Current limits and configuration

`ARABIC_OCR_ENABLED` and `ARABIC_HANDWRITTEN_OCR_ENABLED` default to `true` in
`Settings` and both Compose files. An installation can override them. Startup currently
requires direct `GEMINI_API_KEYS` and probes the printed Flash model even when a
ModelGateway key is configured. The local classifier also needs its configured Ollama
vision model. These are source defaults, not a claim about a particular server's secrets
or provider access.

Automatic confident handwriting can reach the handwritten OCR path when its flag is
enabled. The separate manual `arabic-writing-style` endpoint still records a handwritten
choice as unavailable; it does not enqueue handwriting OCR. See the
[Arabic routing feature](docs/05-features/16-arabic-document-routing.md) and
[operator runbook](docs/runbooks/arabic-document-ocr.md).

The README, backend `APP_VERSION`, Python project and npm package are synchronized
at `3.0.0`, including their project lockfile records. `/health.version` reports the
backend build actually running; it changes to `3.0.0` after the updated backend is
deployed. `test_version.py` verifies the release identifiers agree.

Synthetic tests establish implementation behavior, not measured Arabic OCR quality.
Held-out routing precision/recall, OCR error rates, provider costs and browser acceptance
need a real labeled corpus and approved evaluation. Documentation changes do not enable
provider calls or establish those metrics.

### Documentation map

Start at [docs/README.md](docs/README.md). The numbered catalogue now includes PWA,
Arabic routing, model refresh, queue reliability and public discovery. Historical plans,
specifications and audits retain their original decisions; their indexes point to this
current milestone so earlier disabled-handwriting assumptions are not read as runtime
contracts.

## v2 — Article reader and research desk

Introduced continuous paper reading, margin notes, paper/study agents, a research desk,
grounding evidence, accounts, a waiting room, exports, decks and clickable citations.
The backend and frontend version identifiers were set to `2.0.0` on 2026-09-12.
