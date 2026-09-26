# Arabic Document OCR Pipeline Design

**Date:** 2026-09-25

**Status:** Design approved in conversation; written specification awaiting review

**Phase:** Arabic documents only

## 1. Objective

Add an Arabic PDF ingestion route that automatically distinguishes English,
printed Arabic, and handwritten Arabic documents before extraction. Existing
English PDF ingestion, MinerU behavior, web-article extraction, chunking,
embedding, and summarization remain the reference behavior and must not be
regressed.

The first delivery covers Arabic documents. Arabic books and Arabic web
articles are later projects built only after the document route passes its
accuracy, cost, recovery, and rendering acceptance criteria.

## 2. Agreed product rules

1. English documents continue through the current MinerU pipeline.
2. Arabic routing is automatic when confidence meets the evaluated threshold.
   An uncertain printed/handwritten decision asks the user for confirmation
   instead of guessing.
3. Classification happens locally so it consumes no Gemini quota.
4. Printed Arabic uses `gemini-3.7-flash` with its lowest supported thinking
   level, `low`. This model cannot disable thinking completely.
5. The handwritten route is designed for `gemini-3.1-pro-preview`, but live
   handwritten extraction is disabled until the operator supplies a
   billing-enabled Gemini project.
6. Gemini credentials form an ordered cascade. A failed, invalid,
   rate-limited, or quota-exhausted key falls through according to the error
   policy in section 8.
7. If every Gemini key becomes unavailable during printed extraction, retain
   fully validated Gemini pages and continue from the first uncommitted page
   with Ollama Cloud `gemma4:31b-cloud`.
8. Repair only blocks extracted by Gemma. Gemini blocks are stored without
   textual correction, including inside a hybrid document.
9. English-only documents are LTR. Arabic and substantive Arabic/English mixed
   documents use the Arabic extraction route and are RTL.
10. No API key is committed, logged, returned by an endpoint, or stored in a
    database row. The user-supplied keys are placed only in the ignored local
    environment during sandbox execution.

## 3. Scope boundaries

### Included

- Uploaded PDF documents classified as English, printed Arabic, handwritten
  Arabic, or ambiguous.
- Arabic PDFs with or without a usable embedded text layer.
- Automatic provider routing and Gemini key fallback.
- Adaptive page batching, resumable provider fallback, and atomic final
  persistence.
- MinerU-compatible extraction artifacts for the existing chunker.
- Conditional Arabic rendering direction in the paper reader.
- Provider provenance, progress, user-facing errors, retry, and evaluation.
- A conservative repair framework applied only to Gemma-origin output.
- A non-executable handwritten route with a clear availability message and a
  future billing-enabled activation flag.

### Deferred

- Arabic books and book navigation.
- Arabic URL/article ingestion and crawler-output shaping.
- Gemini fine-tuning.
- CE-OCR multi-sample verification.
- Multi-crop prompting except where the document evaluation proves a specific
  failure that cannot be solved by resolution or batching.
- General changes to English extraction, MinerU, or the existing VLM extractor.

## 4. Architecture

```text
PDF upload
   |
   v
local document classifier
   |-- English --------------------------> existing MinerU pipeline
   |
   |-- printed Arabic -------------------> Gemini Flash cascade
   |                                          |
   |                                          | all keys unavailable
   |                                          v
   |                                  keep validated pages
   |                                          |
   |                                          v
   |                              Gemma 4 from first unfinished page
   |                                          |
   |                                          v
   |                              repair Gemma-origin blocks only
   |
   |-- handwritten Arabic --------------> clear unsupported message
   |                                      (Pro route disabled)
   |
   |-- uncertain Arabic style ----------> request user confirmation

successful Arabic extraction
   |
   v
Arabic artifact adapter -> content_list.json + assets
   |
   v
existing chunk persistence and downstream pipeline
   |
   v
conditional RTL reader rendering
```

The new route joins the existing pipeline at the extraction artifact boundary.
It emits the same ordered, page-indexed block structure consumed by
`create_chunks_from_content_list`. Persistence and downstream processing are
shared after that point.

## 5. Local classification

Classification has two stages and returns one document-level route:
`english`, `arabic_printed`, `arabic_handwritten`, or
`arabic_style_uncertain`. The classifier must abstain when confidence is below
the evaluated threshold. It must never convert uncertainty into a handwritten
decision, because that would incorrectly block a printed document.

### 5.1 Text-layer fast path

PyMuPDF inspects the text layer of every page because this is local and cheap.
It counts Unicode Arabic-script letters and Latin letters after excluding
digits, punctuation, URLs, and isolated formula symbols.

- Any substantive, verified Arabic body text routes to the Arabic style
  classifier, including Arabic/English mixed documents.
- A document with clear Latin body text and no verified Arabic body text routes
  to the unchanged English pipeline.
- Missing, sparse, or uncertain text-layer evidence routes to visual language
  classification.

Thresholds are configuration values fixed by the sandbox corpus, then frozen
in tests. The code must not infer handwriting from the text layer.

### 5.2 Visual classifier

Every page receives a local, low-resolution visual style screen in page-order
batches. Pages marked suspicious, conflicting, or low-confidence are rendered
again at higher resolution and classified individually; a one-page document
also receives full-page and region-level views. This costs local compute but no
Gemini quota and reduces the chance that a sampled subset hides a different
writing style. The local Ollama vision model returns strict JSON with:

```json
{
  "language": "arabic|english|mixed|unknown",
  "writing_style": "printed|handwritten|mixed|unknown",
  "confidence": 0.0,
  "evidence": "short explanation"
}
```

The initial local model is `qwen3-vl:4b-instruct`, configurable through
`ARABIC_ROUTER_MODEL`. It is small enough to run locally, accepts images, and
is used only for classification. Before production deployment, its accuracy
must meet the classifier acceptance criteria in section 14 on the actual VPS.
If it fails those criteria, the operator may select a different local Ollama
vision model through the same interface without changing routing code.
The deployment checklist must also verify the Ollama version and available VPS
memory required by the chosen model rather than assuming local compatibility.

Document aggregation is deliberately conservative:

- A document becomes `arabic_handwritten` only when the evaluated
  high-confidence threshold is met across multiple independent page views and
  no sampled primary-content page is confidently printed.
- A one-page document must obtain consistent handwritten decisions from the
  full page and independently rendered content regions before it can be marked
  handwritten.
- `mixed` handwriting/print, conflicting page votes, `unknown`, and confidence
  below threshold become `arabic_style_uncertain`; they are not auto-routed.
- Arabic with isolated signatures, stamps, or short handwritten annotations
  remains `arabic_printed` when the primary body content is printed.
- Arabic/English bilingual documents set `detected_language=mixed`, use the
  Arabic route, and are classified separately for printed versus handwritten
  style.
- Any verified Arabic body content is sufficient for the mixed-language route;
  isolated OCR noise or an unreadable decorative mark is not.

For `arabic_style_uncertain`, the UI asks the user to identify the document as
printed or handwritten. Selecting printed continues through Gemini Flash;
selecting handwritten shows the unsupported message. This abstention path is
required because a 100% guarantee on unseen documents is not technically
possible. The existing job state becomes `failed` with error code
`arabic_style_confirmation_required`; after confirmation, the existing
`failed -> queued` retry transition restarts the job with the recorded manual
classification and does not run the classifier again.

Classification results are stored as document metadata for auditability.

## 6. Arabic extraction request shape

### 6.1 Image preparation

- Render pages in RGB at 200 DPI for the first benchmark.
- Do not binarize, grayscale, sharpen, or apply contrast enhancement.
- Preserve page aspect ratio.
- For Gemini models that support it, set per-image `media_resolution` to
  `HIGH`; record the resulting image-token usage and compare it with the
  default during the sandbox evaluation.
- Enforce a configurable maximum encoded image size without silently lowering
  resolution below the evaluated minimum.
- Keep page images temporary unless a failed job needs them as a debug artifact.

### 6.2 Adaptive batching

The extractor minimizes API requests while keeping failures recoverable:

- Documents at or below `ARABIC_OCR_SINGLE_REQUEST_MAX_PAGES` are sent in one
  request.
- Longer documents are split into consecutive batches of
  `ARABIC_OCR_BATCH_PAGES`.
- Initial sandbox values are 4 pages for a single request and 4 pages per
  batch. Evaluation may change both values before they are frozen.
- Every prompt identifies the absolute page number of every image.
- Each response must contain explicit page boundaries. Missing, duplicated, or
  out-of-order pages invalidate the batch and trigger a bounded retry.

The complete document is committed only after every page validates. Provider
batches are transport units, not persistence boundaries.

### 6.3 OCR output

The vision model produces faithful structured Markdown in logical reading
order, including:

- headings,
- paragraphs,
- lists,
- Markdown or HTML tables,
- displayed equations,
- image/chart captions,
- page boundaries, and
- `[غير واضح]` for illegible text instead of guessing.

The prompt explicitly requires Arabic columns to be read from the rightmost
column to the leftmost column, with each column read top to bottom. It forbids
translation, summarization, spelling correction, invented diacritics, and
modernization of historical spelling.

A deterministic local adapter converts the Markdown page sections into the
existing `content_list.json` schema and assigns `page_idx`. It recognizes
headings, prose, lists, tables, equations, and captions. The adapter does not
make another Gemini call. If a structure is ambiguous, it is preserved as a
text block rather than dropped.

## 7. Models and thinking behavior

All external model names are settings, so provider model churn requires an
environment change rather than a code change.

| Purpose | Initial model | Thinking |
| --- | --- | --- |
| Local routing | `qwen3-vl:4b-instruct` | instruct mode; no reasoning requirement |
| Printed Arabic OCR | `gemini-3.7-flash` | `thinking_level=low` |
| Handwritten Arabic OCR, disabled | `gemini-3.1-pro-preview` | provider default when later enabled |
| Printed fallback OCR | `gemma4:31b-cloud` through existing Ollama Cloud | disabled where supported |

Printed OCR uses Gemini 3.7 Flash as explicitly selected by the user. Google's
current documentation says this model always uses thinking and supports only
`low`, `medium`, and `high`; `minimal` is unsupported. The client therefore
sets `thinking_level=low`, captures thought-token usage, and includes those
tokens in cost metrics. The pipeline does not claim that thinking is disabled.

The handwritten route is configured for `gemini-3.1-pro-preview`, which is not
included in the Gemini API free tier. `ARABIC_HANDWRITTEN_OCR_ENABLED` therefore
defaults to `false`, and the current release must stop before making a Pro API
call. The route, model interface, status handling, and mocked tests are built
so it can be enabled later with an explicit configuration change after a
billing account is available.

Before the printed live corpus runs, a capability preflight tests every
supplied key against Gemini 3.7 Flash with the smallest valid request and
records only success/failure metadata. If none of the keys can access it,
implementation stops with an actionable report; it does not silently enable
billing or select a different model.

All external model names remain settings, so an explicitly approved provider
change requires an environment update rather than a code change. A startup
capability probe must fail with an actionable configuration error if an
enabled model is no longer available. It must not probe or call the disabled
handwritten Pro model.

## 8. Gemini credential cascade

`GEMINI_API_KEYS` is a comma-separated ordered list. Each non-empty value
becomes a provider target with its own circuit-breaker identifier. The client
logs only the target index, never the key.

Error handling:

- `401`/invalid authentication: disable that target for the job and try the
  next key.
- `403`/permission or model access: disable that target for the job and try the
  next key.
- `429` rate or quota exhaustion: honor a short `Retry-After` once when present,
  then try the next key. Preserve the provider's error details so minute-rate
  exhaustion can be distinguished from daily quota exhaustion. Do not wait
  hours inside a Celery task.
- `408`, network timeout, or `5xx`: bounded exponential retry with jitter on
  the current key, then try the next key.
- `400` request/schema errors: fail the batch immediately because rotating
  credentials cannot repair an invalid request.
- Empty, truncated, unparsable, or page-incomplete output: retry once on the
  current key, then try the next key.

Gemini quotas are enforced per Google project rather than per key, and daily
request quotas reset at midnight Pacific time. The user confirms that the
supplied keys come from separate accounts; the preflight treats them as
independent targets and runtime responses remain the authority for their
actual project quotas. The implementation does not assume that a daily quota
error will always require a full 24-hour wait.

## 9. Printed fallback and handwritten availability

### Printed Arabic

If all Gemini targets become unavailable during any batch:

1. Keep every contiguous Gemini page that independently passed page-boundary,
   ordering, completeness, and output validation, including complete pages in
   a truncated final batch when the provider returned usable partial text.
2. Discard only the first incomplete/invalid page and everything after it in
   that response.
3. Start `gemma4:31b-cloud` at the first uncommitted page and use Gemma for all
   remaining pages.
4. Preserve per-page and per-block provider provenance.
5. Run Arabic repair only on Gemma-origin Markdown or blocks.
6. Validate the combined page sequence for gaps and duplicates.
7. Persist the complete document atomically only after every page is valid.

The adapter gives Gemini and Gemma output the same canonical block structure,
so provider switching does not require discarding valid text. Batch boundaries
are not the recovery boundary; validated page boundaries are. The pipeline
never splices providers inside a page or at an unvalidated text offset.

### Handwritten Arabic

When classification resolves to handwritten and
`ARABIC_HANDWRITTEN_OCR_ENABLED=false`:

1. Make no Gemini or Gemma OCR request.
2. Store no extracted chunks.
3. Keep the original PDF.
4. Mark the ingestion result with a dedicated
   `handwritten_arabic_unavailable` error code.
5. Show this user-facing message prominently:

   > Handwritten Arabic extraction is not currently available because it
   > requires Gemini Pro with a billing-enabled account. No text was extracted,
   > and your original file has been kept.

The message appears in a persistent blocking status panel on the document,
not only as a transient notification.

Gemma is never used for handwritten Arabic. Enabling the Pro route later is a
separate, explicit operational decision.

## 10. Gemma-origin Arabic repair

The repair pass is gated by block/page provenance:

```python
for page in extracted_pages:
    if page.provider == "gemma4_arabic_fallback":
        repair_arabic_gemma_page(page, pdf_path)
```

Gemini-produced pages and blocks bypass the repair function completely, even
when later pages in the same document came from Gemma.

The repair system follows the same epistemic rule as the existing MinerU glyph
repair: change text only when the source document or a measured, deterministic
failure signature supplies evidence. It is not an Arabic spell-checker.

Initial behavior:

- Preserve the raw Gemma Markdown as an extraction artifact before repair.
- Normalize unsafe Unicode presentation forms to their canonical characters
  without removing meaningful letters or diacritics.
- Remove accidental OCR control characters and duplicated zero-width marks.
- Detect repetition loops and fail the affected batch for retry rather than
  silently deleting repeated content.
- When the PDF has a usable Arabic text layer, align high-confidence spans and
  repair only discrepancies with unambiguous surrounding context.
- Leave ambiguous text unchanged or marked `[غير واضح]`.

Additional Arabic corrections are added only after a real failure is reproduced
in the benchmark corpus and covered by a regression fixture. Broad substitutions
such as alef normalization, ta-marbuta/ha replacement, ya/alef-maqsura
replacement, or diacritic removal are prohibited in stored OCR text because
they can change meaning. Such normalization is allowed only in evaluation
metrics.

## 11. Persistence and API contract

Add nullable document metadata:

- `detected_language`: `english`, `arabic`, `mixed`, or `unknown`.
- `detected_writing_style`: `printed`, `handwritten`, `mixed`, or `unknown`.
- `text_direction`: `ltr`, `rtl`, or `auto`.
- `classifier_model`: the local model that selected the route.
- `classification_confidence`: numeric confidence when the classifier supplies
  one.
- `classification_source`: `automatic` or `user_confirmed`.
- `ocr_provider_summary`: ordered provider/page ranges for completed Arabic
  extraction.

Existing `documents.extractor` records the final whole-document extractor:

- `mineru` / existing values for English,
- `gemini_arabic_flash`,
- `gemma4_arabic_fallback`,
- `gemini_gemma_arabic_hybrid`, or
- reserved `gemini_arabic_pro` after handwritten extraction is enabled.

The temporary Arabic artifact carries a provider marker for each page/block.
The document stores a compact provider summary with page ranges so support and
evaluation can distinguish Gemini-only, Gemma-only, and hybrid results without
storing credentials or model responses.

The upload response remains compatible. Document detail and progress responses
gain optional metadata fields. An uncertain style returns an
`action_required=confirm_arabic_writing_style` state with printed and
handwritten choices. Add a nullable `error_code` to ingestion jobs so clients
do not parse human-readable messages. Older clients continue to work and see
the job as failed rather than incorrectly completed.

## 12. Reader behavior

Direction is deterministic: `english` uses `ltr`; `arabic` and `mixed` use
`rtl`. Arabic documents continue to use the paper/article reader surface during
this phase. When `text_direction == "rtl"`:

- content blocks use `dir="rtl"` or `dir="auto"` at the smallest practical
  block boundary,
- text alignment uses logical `start` rather than hard-coded left/right,
- headings, lists, tables, captions, selections, highlights, and notes inherit
  the correct base direction,
- embedded Latin text and both Western and Arabic-Indic numerals retain their
  natural bidirectional behavior, and
- application chrome remains in the existing interface direction.

English documents retain current LTR styles. Mixed Arabic/English documents use
an RTL base direction while embedded English text and numerals rely on block
`dir="auto"` and the Unicode Bidirectional Algorithm. This follows W3C guidance
to establish the correct base direction in markup and let the algorithm handle
mixed directional runs.

## 13. Configuration

Add placeholders to `.env.example` and pass them to both API and worker
containers where required:

```dotenv
ARABIC_OCR_ENABLED=false
ARABIC_ROUTER_MODEL=qwen3-vl:4b-instruct
GEMINI_API_KEYS=
ARABIC_GEMINI_PRINTED_MODEL=gemini-3.7-flash
ARABIC_GEMINI_PRINTED_THINKING_LEVEL=low
ARABIC_GEMINI_HANDWRITTEN_MODEL=gemini-3.1-pro-preview
ARABIC_HANDWRITTEN_OCR_ENABLED=false
ARABIC_GEMMA_FALLBACK_MODEL=gemma4:31b-cloud
ARABIC_OCR_DPI=200
ARABIC_GEMINI_MEDIA_RESOLUTION=HIGH
ARABIC_OCR_SINGLE_REQUEST_MAX_PAGES=4
ARABIC_OCR_BATCH_PAGES=4
ARABIC_MIN_BODY_CHAR_COUNT=20
ARABIC_CLASSIFIER_CONFIDENCE_MIN=0.80
ARABIC_HANDWRITTEN_CONFIDENCE_MIN=0.97
```

The feature defaults off until the sandbox evaluation passes. Enabling it does
not change an English document's extractor route.

## 14. Sandbox and evaluation

Development takes place in an isolated git worktree and feature branch. No live
VPS deployment occurs during implementation.

### Corpus

The frozen first-phase corpus contains:

- at least 10 printed Arabic PDFs with clean digital text,
- at least 10 scanned printed Arabic PDFs,
- at least 10 handwritten Arabic PDFs,
- at least 5 bilingual Arabic/English PDFs,
- at least 5 English controls, and
- documents with tables, numerals, stamps, signatures, multi-column layouts,
  weak scans, handwriting-like fonts, sparse typed forms, handwritten notes on
  printed pages, and more than four pages.

The user-provided prompt screenshot is not an OCR sample and is excluded. Real
Arabic sample documents must be supplied before live accuracy evaluation.

### Metrics

- Raw and normalized CER/WER for printed Arabic OCR.
- Structural block retention and reading-order correctness.
- Classifier confusion matrix for English/printed/handwritten, including the
  abstention rate and accuracy among auto-routed documents.
- Provider and key-attempt counts.
- Requests, input/output/thinking tokens, and cost per page.
- Batch retry and fallback frequency.
- Latency p50/p95.
- RTL visual checks for paragraphs, mixed numbers, tables, lists, selection,
  highlights, and notes.

Evaluation normalization may strip tashkeel/tatweel and normalize selected
Arabic variants, but stored and rendered OCR remains untouched.

### Acceptance criteria

1. All English control PDFs take the existing MinerU path and produce
   byte-equivalent chunk payloads, excluding newly nullable document metadata.
2. English controls remain in MinerU, while every document with verified,
   substantive Arabic body text, including Arabic/English mixed text, enters
   the Arabic route.
3. On a held-out classifier set, zero printed documents may be auto-labeled
   handwritten and zero handwritten documents may be auto-labeled printed.
   Documents that cannot meet both conditions must abstain as
   `arabic_style_uncertain`; classifier coverage is reported separately and is
   never hidden inside the accuracy number. This is a finite test-set release
   criterion, not a claim of 100% accuracy on future unseen documents.
4. No successful Arabic job loses or duplicates a page.
5. Gemini 3.7 Flash requests explicitly use `thinking_level=low`; thought-token
   usage is captured and included in the true per-page cost. Any higher or
   default thinking level fails the configuration test.
6. Exhausting all Gemini keys keeps every independently validated Gemini page,
   restarts at the first uncommitted page with Gemma, and produces a gap-free,
   duplicate-free hybrid result.
7. A handwritten decision produces the specified unsupported message, makes
   no OCR provider call, and stores no partial chunks while the feature flag is
   disabled.
8. Gemma repair touches only Gemma-origin pages/blocks and never changes a
   Gemini-origin block in the same document.
9. No credential appears in git history, logs, API output, or database rows.
10. English renders LTR; Arabic and mixed Arabic/English render with an RTL base
    direction. Paragraphs, tables, lists, numerals, and mixed English spans
    display correctly in both light and dark themes.

Accuracy thresholds for CER/WER are recorded as baseline and improvement
results rather than invented in advance. The first release requires the chosen
configuration to beat the Gemma-only printed baseline. Handwritten documents
participate only in classifier evaluation until the billing-enabled Pro route
is explicitly activated.

## 15. Error handling and observability

- Classifier, extractor, model, key index, batch/page range, per-page provider,
  retry count, latency, and token usage are logged without document text or
  credentials.
- Extraction progress reports pages completed over total pages.
- Temporary output is written under an attempt-specific directory and promoted
  only after validation.
- Existing ingestion cleanup removes database chunks after any fatal failure.
- Provider failures are distinguished from invalid model output and from local
  adapter failures.
- User messages stay actionable and avoid exposing internal paths, responses,
  keys, or stack traces.

## 16. Delivery sequence

1. Implement and test the local classifier without changing production routing.
2. Implement Gemini extraction and the credential cascade behind
   `ARABIC_OCR_ENABLED=false`.
3. Implement artifact validation, adaptive batching, and the existing chunker
   handoff.
4. Implement continuation from the first uncommitted batch, provider-scoped
   Gemma repair, and hybrid provenance.
5. Add the disabled handwritten route, uncertain-style confirmation, document
   metadata, and deterministic LTR/RTL rendering.
6. Run mocked failure tests and the frozen live sandbox corpus.
7. Freeze configuration values, update operational documentation, and prepare a
   pull request for review.
8. After the document PR is accepted and deployed, begin a separate Arabic book
   design. Arabic articles follow the book phase.

## 17. References

- Current extraction dispatcher:
  [`backend/app/extraction/pipeline_sync.py`](../../../backend/app/extraction/pipeline_sync.py)
- Existing typed-block contract:
  [`backend/app/extraction/chunker.py`](../../../backend/app/extraction/chunker.py)
- Existing Ollama key cascade and configuration:
  [`backend/app/core/config.py`](../../../backend/app/core/config.py)
- Existing source-grounded glyph repair:
  [`backend/app/extraction/glyph_repair.py`](../../../backend/app/extraction/glyph_repair.py)
- Google Gemini rate limits (quota is project-based):
  <https://ai.google.dev/gemini-api/docs/rate-limits>
- Google Gemini API keys:
  <https://ai.google.dev/gemini-api/docs/api-key>
- Google Gemini thinking controls:
  <https://ai.google.dev/gemini-api/docs/thinking>
- Google Gemini 3.7 Flash model details:
  <https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash>
- Google Gemini 3 thinking-level limitations:
  <https://ai.google.dev/gemini-api/docs/gemini-3>
- Google Gemini image understanding and media resolution:
  <https://ai.google.dev/gemini-api/docs/image-understanding>
- Google Gemini model pricing and free-tier availability:
  <https://ai.google.dev/gemini-api/docs/pricing>
- Ollama Qwen3-VL model family:
  <https://ollama.com/library/qwen3-vl>
- Ollama Gemma 4 31B Cloud:
  <https://ollama.com/library/gemma4:31b-cloud>
- W3C bidirectional text guidance:
  <https://www.w3.org/International/docs/bp-html-bidi/>
