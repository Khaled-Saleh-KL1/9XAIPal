# Arabic document OCR evaluation

This harness separates offline classifier checks, synthetic harness checks,
and explicitly requested live OCR runs. A mock score is not a model accuracy
result.

## Corpus contract

The frozen benchmark should contain at least:

- 10 digitally generated printed Arabic documents;
- 10 scanned printed Arabic documents;
- 10 handwritten Arabic documents;
- 5 Arabic/English mixed documents; and
- 5 English-only controls.

Across those splits, include tables, Arabic and Western numerals, stamps and
signatures, handwriting-like fonts, multi-column layouts, image-only scans,
and documents longer than four pages. Record document characteristics and
the expected route in the manifest. Ground truth must be human-verified. Do
not count uncertain classifier abstentions as correct automatic routes.

Keep source PDFs, whole-document ground truth, page-level ground truth, and
generated run directories private. Place corpus files outside the repository
or under the ignored `backend/eval/arabic_documents/corpus/` directory. A real
manifest references paths relative to itself or absolute paths:

```json
{
  "schema_version": 1,
  "held_out_splits": ["printed_digital"],
  "normalization": {
    "strip_markdown": true,
    "normalize_alef": true,
    "normalize_ya": false,
    "normalize_ta_marbuta_ha": false
  },
  "pricing": {
    "by_model": {}
  },
  "documents": [
    {
      "doc_id": "printed-001",
      "split": "printed_digital",
      "expected_route": "printed",
      "source_pdf": "corpus/printed-001.pdf",
      "ground_truth_file": "corpus/printed-001.gt.txt",
      "ground_truth_pages_file": "corpus/printed-001.pages.json"
    }
  ]
}
```

Populate model rates from the current provider price sheet and billing
account before interpreting cost results. If a used model has no configured
rate, its cost is reported as unknown, not zero. Page ground truth
is optional and uses either `{"1": "page text"}` or an array of
`{"page_number": 1, "text": "page text"}` entries. Its SHA-256 is recorded
separately from the whole-document ground-truth hash.
The `held_out_splits` list is required for real runs; it names frozen splits
whose printed↔handwritten misroutes make the CLI exit non-zero after writing
the report. The synthetic example uses its sole mock split for harness checks.

The committed `manifest.example.json` contains only synthetic strings and
`source_pdf: null`; run it only with `--mock`. It is deliberately not a corpus.

## Modes

From `backend/`:

```bash
# No OCR-provider calls: use the local Arabic classifier and real PDFs.
python -m eval.arabic_documents.run_eval \
  --manifest /absolute/path/to/private-arabic-corpus/manifest.json

# Synthetic contract check: no PDF, classifier, credentials, or network.
python -m eval.arabic_documents.run_eval \
  --manifest eval/arabic_documents/manifest.example.json --mock

# Live hybrid OCR: local classification, one-page Gemini Flash preflight per
# configured key, then printed Arabic extraction. Handwritten Pro is never called.
python -m eval.arabic_documents.run_eval \
  --manifest /absolute/path/to/private-arabic-corpus/manifest.json --live

# Gemma-only printed baseline; no Gemini request is made.
python -m eval.arabic_documents.run_eval \
  --manifest /absolute/path/to/private-arabic-corpus/manifest.json \
  --live --ocr-arm gemma-only
```

Live mode is opt-in. API keys must be supplied only through ignored
`backend/.env` or the process environment; do not put keys in a manifest,
command-line argument, report, or source file. Preflight reports contain only
key indices, safe status categories, model name, token counts, and latency.
If no key returns a valid `gemini-3.7-flash` response, the runner stops before
corpus OCR. The handwritten Gemini Pro model is not probed or called.

The default run location is this ignored `runs/` directory. Use `--run-dir`
to select another new directory; existing run directories are never
overwritten. `records.jsonl`, `score.json`, and `score.md` omit OCR text and
prompts. The per-document `artifacts/` subtree contains extracted content and
must be treated as private. Source PDF and ground-truth bytes are SHA-256
hashed in each record so a benchmark can be checked for drift without copying
the corpus into the report.

## Metrics and limits

Reports separate classifier confusion, auto-route coverage, abstentions,
printed-to-handwritten and handwritten-to-printed errors, and classifier
failures. Invalid provider page structure is counted as a parse failure, apart
from whole-document OCR failures. OCR reports raw and configured-normalized CER, WER, and multiset
token-F1, with micro, macro, per-document, and split-level results. Page
coverage and provider page-range overlaps are reported independently.

Normalization is scoring-only: it never modifies extracted text or
artifacts. The defaults remove Arabic vowel marks and tatweel, normalize Alef
variants, strip the supported Markdown/HTML layout syntax, and collapse
whitespace. Ya and ta-marbuta/ha equivalence are opt-in because they can hide
real spelling errors.

Token costs include Gemini thought tokens at the output rate. The report also
shows a candidates-only tracker estimate and the thought-token under-report
percentage, calculated as `(all-in cost - candidates-only cost) / all-in cost
× 100`. The zero-cost case is reported as not applicable. Provide verified
per-model rates under `pricing.by_model`; otherwise cost is explicitly
unknown. Cost and end-to-end/OCR latency are split by corpus subset; latency
uses nearest-rank p50/p95 and includes attempted OCRs that failed. Optional
`--compare-run-dir` computes paired normalized-CER changes by document ID and
counts failed OCRs as losses instead of dropping them. Provider/key attempts,
retries and hybrid fallback frequency are aggregated without recording keys.

This tool does not certify classifier or OCR quality from synthetic data.
Live accuracy claims require the complete frozen corpus, human-verified gold,
and a separately reviewed report. In particular, the handwritten set measures
the classifier only: handwritten OCR remains unavailable while the Pro
feature flag is disabled.
