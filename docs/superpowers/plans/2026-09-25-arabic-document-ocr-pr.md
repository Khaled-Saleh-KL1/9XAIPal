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

## Independent-review fixes

The follow-up review found defects; the original statement that it found no
remaining findings was incorrect. The fixes are separate commits by group:

- Clear English text-layer pages, including pages with embedded figures, stay
  on MinerU without visual language screening. Sparse scanned pages still get
  language confirmation. During a local classifier outage, a document with no
  substantive Arabic text and at least
  `ARABIC_CLASSIFIER_OUTAGE_ENGLISH_PAGE_SHARE` (0.80) clear English pages
  stays on MinerU, so a blank or figure-only page does not block an English
  paper; mostly-scanned documents fail with `arabic_classifier_unavailable`.
- Gemma-only repair preserves Markdown structure and non-Arabic Unicode;
  invalid repair output is retried as a typed batch failure.
- Gemini calls now have a bounded timeout/retry policy, short `Retry-After`
  handling, job-scoped key state, invalid-key classification, and per-image
  media resolution. Both provider prompts constrain reading order and verbatim
  transcription; the adapter preserves display math and table captions.
- Per-page provider provenance is stored. Startup checks only the printed
  Flash capability when Arabic OCR is enabled, and blocks only when every key
  is definitively unavailable (HTTP 400/401/403/404); a rate limit, spent
  quota, outage, or network error at boot is logged and does not stop the API
  or worker. Handwritten remains disabled.
  Confirmation, re-extraction, error messages, and frontend blocking states
  were corrected.
- Routing now uses substantive Arabic body text and primary-content style
  votes, with one-page detail views for non-English candidates. The evaluator
  gates held-out printed/handwritten misroutes, counts failed OCR comparisons,
  reports attempts/fallbacks, and preflights at configured DPI.
- Legacy documents remain LTR. English margin marks keep their original rules;
  RTL documents get mirrored bookmark and note-tint marks, and footnotes and
  lists use logical CSS. The compose frontend build, `scripts/deploy-once.sh`,
  CI, and deployment docs all use Node 22, matching the test dependencies.
- Prose between two display equations stays a text block; display
  environments may still contain nested environments.

- The evaluator scores spec §14 structural block retention (overall and per
  block type, with type mismatches) and reading-order correctness (pairwise
  order and longest in-order run) against an optional human-verified
  `ground_truth_blocks_file`, using the same content list the chunker reads.

The branch also integrates the newer `main` (#156–#159): document-deletion
safeguards, heading repair, and the two-worker production setting. Both
MinerU-specific repairs (glyph and heading) are skipped for Arabic OCR output
at ingestion and on re-chunk, because Gemini output is stored uncorrected
(spec §2.8). No merge to `main` or deployment occurred.

## English pipeline isolation

With `ARABIC_OCR_ENABLED=false` the English path issues the same statements as
`main`: the shared job-status helpers keep main's rules and only name the new
`error_code` column for a typed Arabic failure (so a worker that starts before
the API's migration cannot fail an English job), `get_document` adds only the
Arabic error columns, and `error_message` for non-Arabic documents is the
document's own message as before. The only existing dependency whose locked
version changed is `websockets` (17.1 → 16.1.1, required by `google-genai`;
used only by uvicorn's optional WebSocket support, which the app does not use).

Differential check: the real PDF pipeline was run by `main` and by this branch
on 15 recorded MinerU outputs with their source PDFs (17,559 chunks, 4,203
image assets, one MinerU-failure case), replacing only the MinerU subprocess.
Persisted chunks, assets, document and job rows, stored files, and dispatched
tasks were identical to `main` (random stored image filenames normalized) with
the feature off, with it on, and on the book/full-embedding path.

## Automated verification

Passed in the isolated sandbox:

- Full backend suite after the `main` merge and all review fixes: **912
  passed** (340.52s), using the disposable test database and `DEBUG=true` for
  the HTTP test client.
- Frontend tests: **20 passed** across 5 files, and `npm run build`
  (`tsc && vite build`) passed, both inside `node:22-alpine`. Vite reports the
  existing large-chunk advisory; this is not a build failure.
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
provider provenance, and repair boundaries. No live provider fallback was
exercised in this implementation run.

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
- With two workers, two Arabic jobs can render 200 DPI pages and call the local
  classifier at once; include that in the VPS memory check the spec requires.
- Merge only after human review and the frozen-corpus/browser acceptance gate.
