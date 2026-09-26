# Arabic document OCR operations

This runbook covers the feature-flagged Arabic **document** pipeline only.
Books and articles are out of scope. The English MinerU/VLM, book, article,
and web-search pipelines must remain unchanged.

## Current safety state

- `ARABIC_OCR_ENABLED=false` by default. Keep it false until the real frozen
  evaluation corpus passes the acceptance criteria and an operator approves
  sandbox activation.
- `ARABIC_HANDWRITTEN_OCR_ENABLED=false` and must remain false. The configured
  Gemini Pro model is not called; there is no billing-enabled handwritten
  OCR path in this release.
- English-only documents use the existing extraction dispatcher and LTR.
  Verified Arabic, including Arabic/English mixed content, uses the Arabic
  route and RTL for document content. Application chrome is not direction-flipped.
- No deployment or VPS activation is included in this branch.

## Local classifier preflight

The classifier model configured by default is `qwen3-vl:4b-instruct`. The
official Ollama model listing currently describes that tag as a 3.3 GB model
and says Qwen3-VL requires Ollama 0.12.7 or newer. The 3.3 GB download size is
not a RAM/VRAM guarantee: context allocation, image workload, parallel
requests, other loaded models, and CPU/GPU placement affect runtime memory.
Ollama documents that larger context and parallelism increase memory use;
inspect the actual loaded model with `ollama ps` rather than assuming the
artifact size is the serving requirement. See the [Qwen3-VL Ollama listing](https://ollama.com/library/qwen3-vl:4b-instruct),
[Ollama context guidance](https://github.com/ollama/ollama/blob/main/docs/context-length.mdx),
and [Ollama concurrency guidance](https://github.com/ollama/ollama/blob/main/docs/faq.mdx).

On the machine that runs Ollama:

```bash
ollama --version
ollama pull qwen3-vl:4b-instruct
ollama list
ollama ps
free -h
df -h
```

For NVIDIA hosts, also inspect `nvidia-smi`; use the platform's equivalent
for other accelerators. Confirm the server is reachable from the API/worker
container at `http://host.docker.internal:11434`. Both Compose files map the
classifier endpoint to that host address; production Compose adds the
`host-gateway` mapping. Confirm model loading and image input with a
non-sensitive synthetic Arabic/English PDF in the sandbox before enabling the
feature. If the model cannot fit or is being unintentionally offloaded, do not
enable Arabic routing; lower the workload only through a separately reviewed
configuration change.

Every PDF page is checked for selectable text. A page with a clear English
text layer remains English even if it has figures or logos, and an entirely
clear English document takes the no-model fast path. Pages with Arabic body
text still require local writing-style classification. Pages with missing or
sparse text need local visual language screening; a high-confidence Arabic
vote also needs a full-page-plus-region detail confirmation. Unresolved cases
abstain.
Synthetic unit tests prove these decision rules and routing behavior, but do
**not** establish real-world classifier precision/recall. Those rates remain
unmeasured until the labeled held-out corpus is supplied.

## Environment and secrets

Use the ignored `backend/.env` file or the process environment. Never put
credentials in source, manifests, screenshots, command-line arguments,
reports, or support tickets. Verify the ignore rule without printing file
contents:

```bash
git check-ignore -v backend/.env
```

Relevant variable names (no values are included here):

- `ARABIC_OCR_ENABLED` — master routing/extraction switch; remains `false`
  until acceptance.
- `ARABIC_HANDWRITTEN_OCR_ENABLED` — keep `false`; setting it true cannot add
  Gemini Pro billing access.
- `ARABIC_ROUTER_MODEL`, `ARABIC_ROUTER_BASE_URL` — local Ollama classifier.
- `GEMINI_API_KEYS` — comma-separated Gemini Flash keys for printed Arabic.
  Keys are tried through the configured cascade.
- `ARABIC_GEMINI_PRINTED_MODEL`,
  `ARABIC_GEMINI_PRINTED_THINKING_LEVEL`,
  `ARABIC_GEMINI_TIMEOUT_SECONDS`, and
  `ARABIC_GEMINI_RETRY_AFTER_MAX_SECONDS` — defaults are
  `gemini-3.7-flash` and `low`. Low is the model's lowest supported level, not
  thinking-off: the current Gemini API documents no full thinking-off mode for
  Gemini 3 Flash, and its Gemini 3.7 Flash table supports `low`, `medium`, and
  `high` only. Thought tokens may still be billed and are included in eval COGS
  reporting. See Google's [thinking-level table and controls](https://ai.google.dev/gemini-api/docs/generate-content/thinking).
  The request timeout defaults to 120 seconds; a Retry-After delay is used
  once on a key only when it is at most 10 seconds by default.
- `ARABIC_GEMINI_HANDWRITTEN_MODEL` — configured as
  `gemini-3.1-pro-preview` for future billing-enabled work, but never called
  while handwritten OCR is disabled.
- `ARABIC_GEMMA_FALLBACK_MODEL`, `ARABIC_GEMMA_BASE_URL` — defaults are
  `gemma4:31b-cloud` and `https://ollama.com`.
- `OLLAMA_API_KEY` — Compose forwards this to the Arabic Gemma cloud fallback.
  For direct non-Compose settings, `ARABIC_GEMMA_API_KEYS` is also supported.
- `ARABIC_OCR_DPI`, `ARABIC_GEMINI_MEDIA_RESOLUTION`,
  `ARABIC_OCR_SINGLE_REQUEST_MAX_PAGES`, `ARABIC_OCR_BATCH_PAGES`,
  `ARABIC_OCR_MAX_OUTPUT_TOKENS`, `ARABIC_MIN_BODY_CHAR_COUNT`,
  `ARABIC_CLASSIFIER_CONFIDENCE_MIN`,
  `ARABIC_HANDWRITTEN_CONFIDENCE_MIN`, and
  `ARABIC_CLASSIFIER_BATCH_PAGES` — runtime controls with safe defaults in
  `.env.example`.

For Compose, use the keys/values already wired in `backend/docker-compose.yml`
or `backend/docker-compose.prod.yml`; do not add a value to `.env` unless the
Compose file forwards it into the service. Run the relevant `docker compose
config` command before restarting services.

## Enable only in a sandbox

1. Keep a separate sandbox database and storage tree; never run backend tests
   against a database whose name does not include `test`.
2. Confirm the local classifier is installed and reachable, the backend
   `.env` is ignored, the Gemini keys are present only in the environment, and
   the real evaluation corpus manifest points to private, human-verified
   ground truth.
3. Run classifier-only evaluation first. Run live OCR only after a human
   approves it and only with the real corpus:

   ```bash
   cd backend
   python -m eval.arabic_documents.run_eval \
     --manifest /absolute/path/to/private-corpus/manifest.json
   python -m eval.arabic_documents.run_eval \
     --manifest /absolute/path/to/private-corpus/manifest.json --live
   ```

   `--live` preflights each Gemini key with the printed Flash model before
   corpus OCR. It never probes Gemini Pro. Do not use the synthetic example
   manifest with `--live`.
4. Only after held-out routing and OCR acceptance, set
   `ARABIC_OCR_ENABLED=true` in the sandbox environment and recreate both
   API and worker services so they read the same settings. Keep
   `ARABIC_HANDWRITTEN_OCR_ENABLED=false`.
   Both services probe every configured printed Flash key at startup with a
   small request. They record only key index and success/failure. If no key
   works, startup fails with a configuration error; check key access, model
   availability, and quota before retrying. The disabled Pro model is never
   probed. Startup probing consumes one Flash request per key per service.
5. Upload the English, printed Arabic, mixed, uncertain, and handwritten
   controls from the approved sandbox set. Check route/status metadata,
   reading order/direction, page completeness, and error persistence. Do not
   use real user documents for initial smoke tests.

## Routing, confirmation, and provider behavior

- The local classifier routes English-only PDFs to the existing extractor.
  It routes verified printed Arabic and mixed Arabic/English to the Arabic
  OCR route. A low-confidence style decision abstains rather than silently
  declaring handwriting.
- An uncertain document stays failed with
  `arabic_style_confirmation_required`. The user can confirm printed or
  handwritten. Printed confirmation requeues the same job; handwritten
  confirmation records the choice and leaves the job unavailable, with no
  Gemini Pro call and no chunks.
- `arabic_classifier_unavailable` means the local Ollama classifier could not
  route a page without a clear English text layer. Start Ollama, check the
  configured vision model, and retry. Clear English pages continue to MinerU.
- `handwritten_arabic_unavailable` is the explicit user-facing handwritten
  state. `arabic_gemini_pro_not_configured` is the internal typed state if an
  operator tries to enable handwritten processing without billing access.
  `arabic_routing_failed` is the safe typed routing failure. Other OCR
  extraction failures may have no specialized `error_code`; use the stored
  sanitized `error_message` and worker logs rather than inferring a provider
  cause.
- Printed OCR uses `gemini-3.7-flash` with low thinking. Gemini keys are
  rotated through bounded retries. On recoverable key/quota/network/output
  exhaustion, fully validated Gemini pages are kept and Gemma begins at the
  first uncommitted page. The pipeline does not splice providers within a
  page. An invalid request or a failed Gemma fallback can still fail the job;
  fallback is not a guarantee of success.
- Only Gemma-origin pages pass through the provenance-gated repair step.
  Gemini-origin page text is preserved as returned.
- Arabic and mixed document content is RTL; English-only document content is
  LTR. English extraction code and its artifacts are not rewritten by this
  Arabic OCR route.

## Artifacts, reports, and rollback

Production extracted artifacts are under the configured storage tree, in the
document-specific extraction directory; `ocr_manifest.json` records page
provider ranges, attempts, and token usage. Treat that directory as private.

Evaluation output defaults to ignored
`backend/eval/arabic_documents/runs/<run-id>/`. `records.jsonl`, `score.json`,
and `score.md` contain hashes, metrics, status, timing, and usage rather than
OCR text or prompts. The per-document `artifacts/` subdirectories contain
extracted text and must remain private. The corpus and run directories are
git-ignored. Use opaque document IDs in manifests because IDs and split labels
are retained in reports.

Rollback in the sandbox: set `ARABIC_OCR_ENABLED=false`, keep
`ARABIC_HANDWRITTEN_OCR_ENABLED=false`, recreate the API and worker, and wait
for already-running jobs to finish before resuming uploads. Disabling the flag
does not remove already-created chunks or artifacts; do not delete them as a
rollback step. Confirm English-only upload/extraction behavior afterward.

## Release gate still pending

The real frozen corpus and browser-upload acceptance set were not included in
this implementation workspace. Consequently, there are no defensible Arabic
classifier precision/recall, OCR CER/WER/F1, cost-per-page, or visual
acceptance claims yet. Keep the feature disabled until those results are
recorded and reviewed.
