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
2. Arabic routing is automatic; users do not have to choose an OCR engine.
3. Classification happens locally so it consumes no Gemini quota.
4. Printed Arabic uses `gemini-3.7-flash` with its lowest supported thinking
   level, `low`. This model cannot disable thinking completely.
5. Handwritten Arabic uses Gemini Pro.
6. Gemini credentials form an ordered cascade. A failed, invalid,
   rate-limited, or quota-exhausted key falls through according to the error
   policy in section 8.
7. If every Gemini key is unavailable for a printed Arabic document, discard
   all partial Gemini output and re-extract the entire document with Ollama
   Cloud `gemma4:31b-cloud`.
8. If every Gemini key is unavailable for a handwritten Arabic document,
   discard all partial output and mark ingestion failed with a retryable
   message explaining that the daily quota is exhausted and may take up to 24
   hours to reset.
9. Arabic OCR repair runs only when the complete document was extracted by
   Gemma 4. Gemini output is stored without textual correction.
10. No API key is committed, logged, returned by an endpoint, or stored in a
    database row. The user-supplied keys are placed only in the ignored local
    environment during sandbox execution.

## 3. Scope boundaries

### Included

- Uploaded PDF documents classified as English, printed Arabic, handwritten
  Arabic, or ambiguous.
- Arabic PDFs with or without a usable embedded text layer.
- Automatic provider routing and Gemini key fallback.
- Adaptive page batching and atomic document fallback.
- MinerU-compatible extraction artifacts for the existing chunker.
- Conditional Arabic rendering direction in the paper reader.
- Provider provenance, progress, user-facing errors, retry, and evaluation.
- A conservative repair framework for Gemma-only Arabic output.

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
   |                                    discard partial output
   |                                          |
   |                                          v
   |                                    Gemma 4 full rerun
   |                                          |
   |                                          v
   |                                    Gemma-only repair
   |
   |-- handwritten/ambiguous Arabic ----> Gemini Pro cascade
                                              |
                                              | all keys unavailable
                                              v
                                        discard partial output
                                              |
                                              v
                                      retryable quota failure

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
`english`, `arabic_printed`, or `arabic_handwritten`. Uncertain Arabic is
treated as handwritten because losing handwriting is more damaging than using
the more capable model on a printed page.

### 5.1 Text-layer fast path

PyMuPDF reads text from up to five evenly distributed non-empty pages,
including the first, middle, and last pages when they exist. The classifier
counts Unicode Arabic-script letters and Latin letters after excluding digits,
punctuation, URLs, and isolated formula symbols.

- A clear Arabic majority routes to the Arabic style classifier.
- A clear Latin majority routes to the unchanged English pipeline.
- Missing, sparse, or mixed text routes to visual classification.

Thresholds are configuration values fixed by the sandbox corpus, then frozen
in tests. The code must not infer handwriting from the text layer.

### 5.2 Visual classifier

Sampled pages are rendered as native RGB images. A local Ollama vision model
returns strict JSON with:

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

Document aggregation rules:

- Any sampled page whose primary readable content is handwritten makes the
  document `arabic_handwritten`.
- `mixed` Arabic handwriting/print is `arabic_handwritten`.
- Arabic with isolated signatures, stamps, or short annotations remains
  `arabic_printed` when the primary document content is printed.
- Arabic/English bilingual printed documents are `arabic_printed` when Arabic
  is a primary language.
- Visual `unknown` after a sparse text result is `arabic_handwritten` only when
  Arabic script is visible; otherwise the upload fails classification with a
  clear retry/manual-report message instead of being silently sent to MinerU.

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

The complete document is committed only after every batch validates.

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
| Handwritten Arabic OCR | `gemini-2.5-pro` | dynamic thinking; recorded in metrics |
| Printed fallback OCR | `gemma4:31b-cloud` through existing Ollama Cloud | disabled where supported |

Printed OCR uses Gemini 3.7 Flash as explicitly selected by the user. Google's
current documentation says this model always uses thinking and supports only
`low`, `medium`, and `high`; `minimal` is unsupported. The client therefore
sets `thinking_level=low`, captures thought-token usage, and includes those
tokens in cost metrics. The pipeline does not claim that thinking is disabled.

The handwritten route starts with Gemini 2.5 Pro because the current pricing
page lists a free tier for it, while Gemini 3.1 Pro Preview has no free API
tier. Google also states that 2.5-model access is restricted for some new
projects, so neither access nor quota is assumed. Before the live corpus runs,
a capability preflight tests every supplied key against the configured model
with the smallest valid request and records only success/failure metadata. If
none of the keys can access the required model, implementation stops with an
actionable report; it does not silently enable billing or select a different
model.

All external model names remain settings, so an explicitly approved provider
change requires an environment update rather than a code change. A startup
capability probe must fail with an actionable configuration error if a
configured model is no longer available.

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
request quotas reset at midnight Pacific time. Key rotation therefore provides
separate quota only when the keys belong to separate projects; it still
provides credential-failure isolation when they do not. The implementation
does not assume that a daily quota error will always require a full 24-hour
wait.

## 9. Whole-document fallback semantics

The Arabic pipeline never persists a mixture of Gemini and Gemma OCR.

### Printed Arabic

If all Gemini targets become unavailable during any batch:

1. Delete the temporary Gemini extraction directory for that attempt.
2. Start a clean extraction directory.
3. Re-run every page through `gemma4:31b-cloud` using the same batching,
   prompt contract, and adapter.
4. Set the document extractor to `gemma4_arabic_fallback`.
5. Run the Gemma-only Arabic repair pass.
6. Persist the resulting chunks atomically.

### Handwritten Arabic

If all Gemini targets report daily quota exhaustion:

1. Delete temporary partial OCR artifacts.
2. Mark the document and ingestion job failed.
3. Store this user-facing message:

   > Handwritten Arabic OCR is temporarily unavailable because today's Gemini
   > quota is exhausted. Try again after the quota resets, which may take up to
   > 24 hours.

4. Keep the original PDF so the existing retry action can restart ingestion.

Gemma is never used for handwritten Arabic.

If the keys instead fail because of authentication, permissions, model access,
invalid configuration, a shorter-window rate limit, or a provider outage, the
job still stores no partial chunks but reports that specific failure category.
It must not mislabel those failures as daily quota exhaustion. When the
provider returns only a generic `429` and the reset window cannot be proven,
the message says that the quota or rate limit was reached and recommends
retrying later, possibly after the next daily reset; it does not claim a known
24-hour lockout.

## 10. Gemma-only Arabic repair

The repair pass is gated by extractor provenance:

```python
if extractor == "gemma4_arabic_fallback":
    repair_arabic_gemma_chunks(chunks, pdf_path)
```

Gemini-produced Arabic bypasses the repair function completely.

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

Existing `documents.extractor` records the final whole-document extractor:

- `mineru` / existing values for English,
- `gemini_arabic_flash`,
- `gemini_arabic_pro`, or
- `gemma4_arabic_fallback`.

The upload response remains compatible. Document detail and progress responses
gain optional metadata fields. Older clients continue to work.

No per-chunk provider field is needed because mixed Gemini/Gemma persistence is
forbidden.

## 12. Reader behavior

Arabic documents continue to use the paper/article reader surface during this
phase. When `text_direction == "rtl"`:

- content blocks use `dir="rtl"` or `dir="auto"` at the smallest practical
  block boundary,
- text alignment uses logical `start` rather than hard-coded left/right,
- headings, lists, tables, captions, selections, highlights, and notes inherit
  the correct base direction,
- embedded Latin text and both Western and Arabic-Indic numerals retain their
  natural bidirectional behavior, and
- application chrome remains in the existing interface direction.

English documents receive no new direction attribute and retain current styles.
This follows W3C guidance to establish the correct base direction in markup and
let the Unicode Bidirectional Algorithm handle mixed directional runs.

## 13. Configuration

Add placeholders to `.env.example` and pass them to both API and worker
containers where required:

```dotenv
ARABIC_OCR_ENABLED=false
ARABIC_ROUTER_MODEL=qwen3-vl:4b-instruct
GEMINI_API_KEYS=
ARABIC_GEMINI_PRINTED_MODEL=gemini-3.7-flash
ARABIC_GEMINI_PRINTED_THINKING_LEVEL=low
ARABIC_GEMINI_HANDWRITTEN_MODEL=gemini-2.5-pro
ARABIC_GEMMA_FALLBACK_MODEL=gemma4:31b-cloud
ARABIC_OCR_DPI=200
ARABIC_GEMINI_MEDIA_RESOLUTION=HIGH
ARABIC_OCR_SINGLE_REQUEST_MAX_PAGES=4
ARABIC_OCR_BATCH_PAGES=4
ARABIC_SCRIPT_RATIO_THRESHOLD=0.70
ARABIC_CLASSIFIER_CONFIDENCE_MIN=0.80
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
  weak scans, and more than four pages.

The user-provided prompt screenshot is not an OCR sample and is excluded. Real
Arabic sample documents must be supplied before live accuracy evaluation.

### Metrics

- Raw and normalized CER/WER for Arabic OCR.
- Structural block retention and reading-order correctness.
- Classifier confusion matrix for English/printed/handwritten.
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
2. Language routing accuracy is at least 98% on the frozen corpus, with zero
   English controls entering the Arabic extractor.
3. Printed-versus-handwritten routing accuracy is at least 95%; ambiguous
   Arabic routes to Pro.
4. No successful Arabic job loses or duplicates a page.
5. Gemini 3.7 Flash requests explicitly use `thinking_level=low`; thought-token
   usage is captured and included in the true per-page cost. Any higher or
   default thinking level fails the configuration test.
6. Exhausting all Gemini keys causes a clean whole-document Gemma rerun for
   printed Arabic.
7. Exhausting all Gemini keys for handwritten Arabic produces the specified
   retryable message and no partial chunks.
8. Gemma repair runs on Gemma-only artifacts and never on Gemini artifacts.
9. No credential appears in git history, logs, API output, or database rows.
10. Arabic paragraphs, tables, lists, numerals, and mixed English spans display
    correctly in both light and dark themes.

Accuracy thresholds for CER/WER are recorded as baseline and improvement
results rather than invented in advance. The first release requires the chosen
configuration to beat the Gemma-only printed baseline and for Pro to beat Flash
on the handwritten subset with a paired per-document comparison.

## 15. Error handling and observability

- Classifier, extractor, model, key index, batch/page range, retry count,
  latency, and token usage are logged without document text or credentials.
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
4. Implement whole-document Gemma fallback and Gemma-only repair gating.
5. Add document metadata and conditional RTL rendering.
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
