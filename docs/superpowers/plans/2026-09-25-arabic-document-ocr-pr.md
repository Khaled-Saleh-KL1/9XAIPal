# Arabic document OCR — draft pull request

> Draft only. The real frozen human-verified corpus and live browser-acceptance
> PDFs were not supplied, so live accuracy and visual acceptance remain
> pending. No deployment was performed.

## Scope and non-goals

- Adds an isolated, feature-flagged Arabic **document** routing/OCR path.
- Leaves English MinerU/VLM, book/article ingestion and crawling, and
  web-search behavior unchanged. The shared document reader only consumes the
  new per-document direction metadata so Arabic OCR output renders RTL; English
  content remains LTR.
- Arabic/English mixed documents route through Arabic OCR; English-only stays
  on the existing extractor path.
- Handwritten Arabic is detected/abstained and given a clear unavailable
  state. Gemini Pro is configured only as a future model name and is not
  called. Books and articles remain deferred.

## Architecture

- Local Ollama `qwen3-vl:4b-instruct` classifies document language and Arabic
  writing style before extraction.
- Printed Arabic uses Gemini 3.7 Flash. On recoverable Gemini exhaustion,
  validated pages are retained and Gemma 4 continues at the first
  uncommitted page; repair is limited to Gemma-origin pages.
- Gemini 3.7 Flash is configured at `thinking_level=low`, its lowest supported
  setting. A true thinking-off mode is not available for this selected model,
  so thought tokens may still be billed and are included in evaluation costs.
- Arabic extraction artifacts are converted into the existing content-list
  and chunking pipeline. API and UI expose classification, direction,
  uncertainty confirmation, and handwritten-unavailable status.
- Feature flags remain off by default. This pull request does not deploy to
  the VPS.

## Automated verification

Passed in the isolated sandbox:

- Full backend suite: **831 passed** (209.95s), using the disposable test
  database and `DEBUG=true` for the HTTP test client. The full repository was
  mounted at its expected root in the test container.
- Arabic-focused adapter/classifier/fallback/API tests: **85 passed**
  (21.07s), including regression tests for mixed scanned content and the
  no-primary-vote abstention case.
- Frontend tests: **16 passed** across 5 files.
- `npm run build`: passed. Vite reports the existing large-chunk advisory
  (the main bundle is about 1.09 MB); this is not a build failure.
- Both development and production Compose configurations validated. Production
  config used a throwaway placeholder only to satisfy required-variable
  interpolation; it was not a credential and no services were started.
- The evaluation CLI's synthetic `--mock` smoke run passed. It is explicitly
  synthetic-only and is not an accuracy result.

No Gemini or Gemma provider calls were made.

## Classifier results on the frozen corpus

Not measured: the real held-out, human-labeled corpus was not supplied. No
precision/recall, confusion matrix, false-positive/false-negative, coverage,
or abstention rate is claimed. Synthetic mock results are harness checks only.

## Printed OCR accuracy and cost

Not measured: no real OCR corpus or human-verified ground truth was available.
CER, WER, token-F1, cost/page, thought-token COGS gap, and subset-level results
must be populated from an approved live run before this PR is ready to merge.

## Fallback proof

The passing backend suite includes `test_arabic_ocr_fallback.py` coverage for
continuing at the first incomplete page, preserving complete Gemini pages,
provider provenance, and repair boundaries. The focused Arabic run passed all
85 tests. No live provider fallback was exercised in this implementation run.

## Visual evidence

Pending the approved sandbox PDFs. The required English, printed Arabic,
mixed, uncertain, and handwritten-control screenshots have not been
captured; attach them from the ignored evaluation run directory after browser
acceptance.

## Limitations and merge gates

- The real corpus is still needed to validate classifier thresholds and
  printed-vs-handwritten error rates; uncertain cases must count as abstentions.
- Handwritten OCR remains unavailable and must remain disabled until a
  billing-enabled Gemini Pro path is separately designed, tested, and approved.
- Arabic books and articles are deferred; article crawler output direction is
  not changed here.
- No accuracy claim can be derived from the committed synthetic manifest.
- The requested zero-thinking behavior cannot be guaranteed with Gemini 3.7
  Flash; see Google's [official thinking-level table](https://ai.google.dev/gemini-api/docs/generate-content/thinking).
- Merge only after human review and the frozen-corpus/browser acceptance gate.
