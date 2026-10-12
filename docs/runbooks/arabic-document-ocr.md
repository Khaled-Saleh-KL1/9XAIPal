# Arabic document OCR operations

> **Current source:** v3 documentation milestone, checked 2026-10-12 against `d73d5e9`.
> [Feature contract](../05-features/16-arabic-document-routing.md) ·
> [Configuration](../03-reference/configuration.md#arabic-document-routing-and-ocr) ·
> [API confirmation](../03-reference/api.md#post-paperspaper_idarabic-writing-style)

This runbook covers language/writing-style routing before PDF extraction, for both
papers and books. HTML article imports follow their own importer. The configured
English extractor remains the English branch; Arabic OCR publishes artifacts that
join the shared chunking and paper-fast/book-full processing paths.

## Source defaults and prerequisites

`ARABIC_OCR_ENABLED` and `ARABIC_HANDWRITTEN_OCR_ENABLED` default to `true` in
`Settings` and both Compose files. Environment overrides determine a particular
installation's effective behavior. The earlier 2026-09-25 specifications described
a disabled, printed-only rollout; they are historical, not the present defaults.

Enabled OCR currently requires direct `GEMINI_API_KEYS`: API and worker startup
call `probe_printed_flash`, even if `MODELGATEWAY_API_KEY` is configured. The
probe checks printed Flash access, not handwritten Pro access. Missing direct keys
or a definitive rejection of every key blocks startup. Rate limits, spent quota,
provider 5xx and network errors are recorded as unconfirmed rather than taking
English ingestion down.

The classifier must run against the configured local Ollama endpoint. Its default
model identifier is `qwen3-vl:4b-instruct`. Compose points it at
`http://host.docker.internal:11434`; verify reachability from API and worker
containers and provision that exact vision-capable model on the Ollama host.

```bash
ollama list
ollama ps
ollama pull qwen3-vl:4b-instruct
```

The pull command downloads model data; run it when provisioning the classifier.
Actual memory use depends on the model, context, page images and concurrent work.
Check the serving host rather than treating a model artifact size as a memory limit.

## Configuration and secrets

Use the ignored `backend/.env` or the process environment. Check that credentials
will not be committed without printing them:

```bash
git check-ignore -v backend/.env
```

The [configuration reference](../03-reference/configuration.md#arabic-document-routing-and-ocr)
owns every Arabic setting and its source default. Important groups:

- Master and handwriting switches: `ARABIC_OCR_ENABLED`,
  `ARABIC_HANDWRITTEN_OCR_ENABLED`.
- Local classification: `ARABIC_ROUTER_MODEL`, `ARABIC_ROUTER_BASE_URL`,
  timeout, confidence, body-language and English-outage thresholds.
- Optional gateway: `MODELGATEWAY_API_KEY`, `MODELGATEWAY_BASE_URL`,
  `ARABIC_GATEWAY_PRINTED_MODEL`, `ARABIC_GATEWAY_HANDWRITTEN_MODEL`,
  and gateway page/wait budgets.
- Direct Gemini: `GEMINI_API_KEYS`, separate printed/handwritten model identifiers,
  request options, timeout and bounded Retry-After.
- Gemma fallback: `ARABIC_GEMMA_FALLBACK_MODEL`, `ARABIC_GEMMA_BASE_URL`,
  `ARABIC_GEMMA_API_KEYS` or its accepted `OLLAMA_API_KEY` alias.
- Rendering and validation: DPI, main and fallback output-token caps, batch sizes
  and `ARABIC_OCR_MAX_UNREADABLE_PAGE_SHARE`.

Both Compose files forward gateway and direct-Gemini settings to API/workers, use
the host-local classifier, and source fallback keys from `OLLAMA_API_KEY`.
A setting that is not forwarded by Compose needs an explicit service environment
entry before a host `.env` override can affect that service.

## Routing and confirmation

Every PDF page is examined for selectable text. A clearly English document can
take a no-model fast path. Arabic body text needs writing-style classification;
sparse or image-only pages need local visual screening. High-confidence Arabic
votes require detail confirmation; unresolved style abstains.

| Outcome | Behavior |
| --- | --- |
| English | Existing configured extractor; English classification sets LTR. |
| Printed Arabic or printed mixed-language PDF | Arabic OCR and RTL document content. |
| Confident handwriting | Handwritten OCR when its switch is enabled; otherwise a typed unavailable failure. |
| Uncertain style | `arabic_style_confirmation_required`, with printed/handwritten choices. |
| Classifier unavailable | `arabic_classifier_unavailable`, except the clear-English outage fallback described below. |

The outage fallback requires no substantive Arabic body text and at least
`ARABIC_CLASSIFIER_OUTAGE_ENGLISH_PAGE_SHARE` (default 0.80) clearly English
pages. It does not guess a mostly scanned PDF is English.

The owner-authenticated `POST /api/v1/papers/{id}/arabic-writing-style`
endpoint applies only to an eligible failed job. Printed confirmation requeues
that existing job through normal admission and execution fencing. **Manual
handwritten confirmation currently records the choice and returns unavailable;
it does not queue OCR.** This endpoint behavior differs from the automatic
confident-handwriting branch. A disabled route or an ineligible job returns 409;
dispatch failure returns 503 with `arabic_confirmation_dispatch_failed`.

## Provider order, recovery and repair

`GeminiOcrClient` attempts configured ModelGateway first, then the direct Gemini
key cascade. Printed and handwritten clients select their own configured model
identifiers; the current names are in the configuration reference. Gateway
attempts are bounded, and repeated gateway batch failures can skip it for the
remainder of that document.

Recoverable key, quota, network or invalid-output exhaustion can preserve fully
validated pages and hand the next uncommitted page to `GemmaArabicFallback`.
Invalid request configuration can still fail the job. A fallback is not a
guarantee that OCR succeeds.

Page boundaries and provider provenance are validated. Only Gemma-origin pages
receive the Gemma-specific repair; Gemini/gateway output bypasses MinerU glyph
and heading repairs. The unreadable-page budget defaults to 10% of the document,
rounded down. Within that budget an explicit unreadable marker may be retained;
exceeding it fails extraction.

After providers fail an individual page, selectable PDF text can recover a
substantive, predominantly Latin page: at least 200 body letters and 80% Latin,
with invalid/replacement-character text rejected. This is a mixed-document
page fallback, not a bypass of Arabic classification.

## Artifacts and diagnosis

Inspect the document's extraction directory and its private `ocr_manifest.json`
for page ranges, provider/model provenance, attempts and usage. The API exposes
`detected_language`, `detected_writing_style`, `text_direction`, classifier
metadata and `ocr_provider_summary`; the progress payload adds typed error/action
fields. Do not copy document text, page images, prompts or credentials into
support reports.

Classifier and confirmation failures have explicit error codes. A generic OCR
failure may carry only a sanitized error message; use worker logs and manifest
provenance rather than inferring which provider failed from a generic status.

## Evaluation and rollout

Use isolated test storage, a disposable database whose name includes `test`,
and a real labeled corpus for routing/OCR quality measurements. Start with mocked
tests and classifier-only evaluation:

```bash
cd backend
POSTGRES_DB=9xaipal_test uv run pytest \
  tests/test_arabic_classifier.py tests/test_arabic_pipeline_routing.py \
  tests/test_gemini_ocr_client.py tests/test_arabic_ocr_fallback.py \
  tests/test_arabic_confirmation_api.py
uv run python -m eval.arabic_documents.run_eval \
  --manifest /absolute/path/to/private-corpus/manifest.json
```

Live evaluation makes provider calls and must use the approved real corpus and
operator-approved configuration. The evaluation harness has its own handwritten
and live-preflight restrictions; inspect
[its README](../../backend/eval/arabic_documents/README.md) before `--live`.
Do not use its synthetic example as evidence of real OCR accuracy.

Record routing precision/recall, CER/WER/F1, page completeness, direction,
latency and cost on held-out data before making quality claims. Source defaults
and synthetic tests do not supply those measurements.

## Rollback

Set `ARABIC_OCR_ENABLED=false` in the target environment, recreate API and workers
to load it, and let already-running jobs finish. This restores the configured
extractor branch for future PDFs; it does not delete existing chunks or artifacts.
Setting the handwriting switch false disables the automatic handwriting route.
Verify an approved English control and document direction after configuration
changes. For durable failures and controlled retries, use the
[queue runbook](queue-reliability.md).
