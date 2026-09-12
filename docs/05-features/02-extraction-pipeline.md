# Area 2 — The extraction pipeline (features 19–32)

> Part of the [feature catalogue](README.md). Companion architecture doc:
> [ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

The pipeline runs in the Celery worker, not the API: `POST /papers/upload` inserts the rows and
calls `process_ingestion.delay(...)`; `run_pipeline_sync` does the work with a **sync** SQLAlchemy
session (psycopg2), because Celery is sync throughout. Every stage writes its status to
`ingestion_jobs.status` so the overlay (feature 5) narrates real progress.

```text
extracting ─► chunking ─► [glyph repair] ─► [assets] ─► fast? ──yes──► complete
                                                          │
                                                          no ─► embedding ─► summarizing ─► complete
```

---

## 19. MinerU structural extraction

**What it does.** Turns a PDF into typed blocks — headings, paragraphs, display equations as
LaTeX, tables as HTML, figures with captions, footnotes, code — each with a page index and
bounding box, plus the figure crops as image files.

**Where.** [`extraction/mineru_client.py`](../../backend/app/extraction/mineru_client.py)
(`extract_pdf_sync`, `find_markdown_output`, `find_content_list`, `find_images`);
the `mineru` CLI is installed in the worker image (`Dockerfile.mineru`, weights from Hugging Face).

**How it works.** `mineru -p documents/<uuid>.pdf -o extracted/<doc_id> -m auto` with the
`pipeline` backend: layout detection, OCR for scanned/image-only pages, table structure
recognition, equation → LaTeX. Output: `<stem>.md` (markdown with `$…$` math, `|…|` tables,
`![](img)`), `<stem>_content_list.json` (the ordered typed blocks with `page_idx`) and
`images/*.jpg`. The chunker (feature 24) consumes `content_list.json`; the markdown is kept for
the regex fallback and for re-chunking. A non-zero exit raises `MinerUError`; job and document are
marked `failed` with the message.

**Why.** MinerU gives *real page numbers and a real type per block* — far better than sniffing the
rendered markdown with regexes. The page number is what makes citation chips say "p. 8" and what
lets the PDF viewer and structured reader stay in step.

---

## 20. Page-batched extraction

**What it does.** Long books are extracted in page-range batches so peak RAM stays bounded, and
the overlay gets real progress (pages done / total) instead of a spinner for twenty minutes.

**Where.** `mineru_client.py::_plan_page_batches`, `MINERU_PAGE_BATCH_SIZE` (100 default; the
live box runs 8), `ingestion_jobs.progress_fraction`.

**How it works.** MinerU's `-s/-e` flags (0-based inclusive) run one batch at a time; the
per-batch outputs are merged back into one `content_list.json` + one markdown, with page indices
offset. After each batch an `on_progress(done, total)` callback updates `progress_fraction`. The
model process is reused across batches (no per-batch restart). `0` disables batching.

**Why.** A 700-page book through MinerU in one go exhausted the worker's memory (7 GB limit). The
batch size is an environment knob, not a constant, because the right number depends on the box.

---

## 21. PyMuPDF fallback

**What it does.** When `ALLOW_PYMUPDF_FALLBACK=true` and `mineru` is missing, extraction degrades
to PyMuPDF text-only: no OCR, no table recognition, no math LaTeX — every block becomes a `text`
chunk, and the regex markdown chunker (feature 24) takes over.

**Where.** `mineru_client.py` (`EXTRACTOR_PYMUPDF`), `documents.extractor` records which one ran.

**Why.** Default **off** so a missing extractor fails loudly rather than silently producing a
degraded document; when on, it logs a loud warning and the overlay shows the extractor label.

---

## 22. Glyph repair

**What it does.** Recovers the inline math variables MinerU destroys.

**Where.** [`extraction/glyph_repair.py`](../../backend/app/extraction/glyph_repair.py)
(`repair_chunks`), run as a second pass after chunking with the PDF back in hand.

**How it works — bug 1, U+FFFD.** Papers typeset inline variables with the Unicode Mathematical
Alphanumeric Symbols block (U+1D400–U+1D7FF). Those are astral codepoints (above U+FFFF, surrogate
pairs in UTF-16) and MinerU's text pipeline writes U+FFFD REPLACEMENT CHARACTER instead — the
reader shows `�` where `𝑛` should be. U+FFFD carries no information, but the PDF still does and
PyMuPDF decodes the same glyphs correctly: the repair re-reads the source page and uses the
surrounding text as a key to look up what each `�` was. The recovered character is normalised to
LaTeX (`𝑛` → `$n$`) so it renders as italic math in KaTeX and so the model sees a variable rather
than a symbol its tokenizer has never met. Unrecoverable characters stay `�`: a visible mystery
glyph beats a confidently wrong letter in a formula.

**Bug 2, the ﬀ ligature.** MinerU expands `ﬁ`/`ﬂ` correctly but collapses `ﬀ` to a single `f`
("diference"), verified on a real book with 112 `ﬀ` in its text layer. No marker is left behind
and a dictionary guess risks a confident wrong correction, so every replacement is derived from a
word the *same PDF* demonstrably contains — and a damaged spelling that also occurs literally in
the PDF is left alone.

---

## 23. Sub/sup garbling repair and leaked-`<sup>` caption cleanup

**What it does.** Two OCR artefacts fixed at ingestion: prose wrongly wrapped in `<sub>`/`<sup>`
mid-word, and chart axis tick labels attributed to a figure caption as a run of `<sup>` tags.

**Where.** [`extraction/normalizer.py`](../../backend/app/extraction/normalizer.py):
`unwrap_garbled_sub_sup`, `strip_leaked_sup_run`, `_HTML_TAG`.

**How it works.** MinerU occasionally emits
`E<sub>m</sub>b<sub>e</sub>ddi<sub>ng mo</sub>d<sub>e</sub>l<sub>s prov</sub>id<sub>e</sub>`
(reproduced on a real paper); rendered, letters scatter above and below the baseline. Two earlier
versions gated on "2+ suspicious tags nearby" and both left corruption on the page, because
production chunks are small (a sentence, a cell, a caption) and much of the damage is a single
stray tag with no second offender to corroborate it. The fix keys on **content type as an
allowlist**: a tag whose content is letters-only running prose is unwrapped; a real footnote marker
or exponent (digits, symbols) is kept. `strip_leaked_sup_run` removes runs of **3+ adjacent
`<sup>`** separated only by whitespace — decorative chart text, never a real superscript — and is
applied to captions in the chunker. `_HTML_TAG` strips the rest of MinerU's real table markup from
the *plain text* only (before embedding), because "colspan"/"rowspan" were showing up as ordinary
words to the embedding model; a bare `<` in prose ("score < 0.5") never matches because the
character after it is not a letter.

---

## 24. Structure-aware chunking

**What it does.** One chunk per structural unit — a heading, a paragraph, a display equation, a
table, a figure, a code listing, a footnote — with a monotonically increasing `sequence_id`, a
`heading_path` breadcrumb, page range, `plain_text` for embedding, `table_json` for tables,
`image_refs` linking figures to their files.

**Where.** [`extraction/chunker.py`](../../backend/app/extraction/chunker.py):
`create_chunks_from_content_list` (preferred), `create_chunks_from_markdown` (regex fallback,
precedence heading > math > table > figure > text).

**How it works — the notable pieces.**
- **Equation stitching** (`_stitch_split_equations`): MinerU splits one display equation across
  blocks; a `$$…$$` block greedily absorbs the trailing fragment of the previous block and leading
  fragments of the following ones, emitting one fence pair. Equation fences are peeled wherever the
  closing one actually falls, `\tag{N}` and a bare trailing `(35)` (the model's equation-number
  footer) are folded into a `\quad (N)` label, stray `$` stripped — the one substring guaranteed to
  break KaTeX again.
- **Footnotes** (`_looks_like_footnote`): text on the first 1–2 pages opening with a footnote
  marker is promoted to `footnote`. Restricted to early pages because numbered list items elsewhere
  also start with "1 "; in return it can be permissive about length (author bios run 500+ chars).
- **Code vs math pseudocode** (`_looks_like_code`, `_looks_like_math_pseudocode`): MinerU marks
  an actual optimizer algorithm and a plain tool-call XML schema with the *same*
  `sub_type: "algorithm"`, so sub-type cannot be the signal; content is — a genuine math span AND
  a LaTeX macro or math-italic character. Real code is fenced with a detected language; pseudocode
  stays `text` and renders through KaTeX.
- **Tables** (`_SimpleTableParser`, `_parse_table_body_to_json`): MinerU's `<table><tr><td colspan>`
  HTML is parsed into `{headers, rows}`; `colspan`/`rowspan` are expanded; rows that are not
  consistent with the header width fall back to the markdown rendering rather than a misaligned
  grid. ⚠ `headers` is routinely empty because MinerU emits no `<thead>` — the *renderer* promotes
  `rows[0]` (feature 34) rather than the chunker, so papers ingested earlier benefit too.
- **Inline math normalisation** (`_normalize_inline_math`, `_normalize_math_glyphs`): Unicode
  math glyphs → LaTeX, so `plain_text` is searchable and the reader renders consistently.
- **Paragraph splitting** (`_split_text_into_paragraphs`, `_split_text_around_display_math`):
  a text block containing display math becomes text / math / text chunks so an equation is its own
  addressable, askable unit.
- `_scrub_chunks` strips NUL bytes and control characters Postgres rejects;
  `_renumber_sequences` closes gaps after drops.

**Why.** Every downstream feature addresses a *block*: citations (`[[42]]` is a sequence id),
anchors, bookmarks, the agent's `READ 40-52`, the reveal reader. The chunk is the unit of the whole
product, which is why the chunker is the largest file in the backend.

---

## 25. Figure, table and equation crops

**What it does.** Every figure MinerU found is stored as a file and linked to its chunk; tables and
equations get the page crop as an image fallback; literal code listings get a page crop too.

**Where.** [`extraction/assets.py::move_asset_to_storage`](../../backend/app/extraction/assets.py),
`chunk_assets` table (`file_path` relative to `images_dir()`, e.g. `<doc_id>/<uuid>.png`),
`chunker.py::crop_code_blocks`, `vlm_client._crop_figure`, served by
`GET /papers/{id}/assets/{file_path}` (feature 91), URL built by
`repositories/assets.py::resolve_asset_url`.

**How it works.** After chunks are persisted, every image in MinerU's output is copied to
`images/<doc_id>/<uuid>.<ext>` (randomised names — no collisions); an `original_name → asset`
map is matched against each chunk's `image_refs` and each hit becomes a `chunk_assets` row.
`crop_code_blocks` renders a padded rectangle of the page for every `code` chunk — a listing's
exact whitespace is part of what it shows, and by the time OCR has flattened `code_body` to a
string that structure is gone; the crop is the only faithful copy. It deliberately does *not*
touch math-pseudocode `text` chunks (KaTeX renders those correctly and an image would trade a
searchable rendering for a flat picture) and does not reuse `_crop_figure` (that snaps a rough
bbox onto an embedded image's true geometry, meaningless for text).

**Why.** The served URL is computed at read time from `file_path` — nothing in the database depends
on where files are mounted, which is what made moving from public `/static/` to authenticated
routes a code-only change.

---

## 26. Fast vs full ingest profile

**What it does.** `INGEST_PROFILE=fast` (default) + `doc_kind='paper'`: the document is
**complete the moment chunking finishes** — no whole-document embeddings or summaries. When
`GENERATE_FIGURE_DESCRIPTIONS=true`, a small background task still builds descriptions and
figure-only vectors for image retrieval. `full`, or any book: the whole chain runs.

**Where.** `pipeline_sync.py::_is_fast_ingest`, `config.py::ingest_profile`.

**How it works.** On the fast path the pipeline sets `embedding_mode='skipped'` (reason
`fast_ingest`), records `page_count`, marks document and job `complete`, and optionally dispatches
the retrieval-only figure task. The task stores descriptions privately and embeds only figure
chunks; it never changes the chunk text shown to the reader. On the full chain the pipeline does
**not** mark completion — `_mark_document_and_job_complete` at the end of
`generate_section_summaries` does, the normal exit whenever anything is dispatched.

**Why.** Nothing should stand between dropping a PDF and reading it; everything the paper agent
needs (feature 66) is derived at question time from the chunks. Marking complete before the chain
ended was the bug that made the UI say "done" while the worker was still embedding. Books never
take the fast path (feature 4).

---

## 27. Embeddings (pgvector)

**What it does.** Vector embeddings of every chunk's `plain_text`, for the book orchestrator's
GLOBAL route and `/search/vector`.

**Where.** [`embeddings/service_sync.py`](../../backend/app/embeddings/service_sync.py)
(`embed_document_chunks_sync`, batches of 20), `chunk_embeddings` table (separate from `chunks`,
one row per chunk, `vector(VECTOR_DIMENSION)`), HNSW index created at startup
(`database/pgvector.py`), `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` /
`EMBED_MAX_CHARS` / `EMBEDDING_MAX_CONCURRENCY`.

**How it works.** Un-embedded chunks are pulled in batches, the texts sent to the embedding
endpoint (Ollama-compatible `/v1/embeddings`; the live box runs `qwen3-embedding:0.6b`, 1024
dims), and rows inserted. A couple of batches run concurrently: one-request-per-chunk is ~13×
slower than batching at all, and a few batches in flight use the box's idle cores — kept modest
because local CPU inference *degrades* under oversubscription rather than queuing like a hosted
endpoint.

**Why.** A separate table lets the vector column's dimension be pinned and the index rebuilt
without touching chunks; the *embedding pin* (feature 99) refuses to start if the configured
dimension disagrees with what is stored.

---

## 28. Hierarchical section summaries

**What it does.** Pre-computed summaries at three levels — the whole paper (level 0), each H1
(level 1), each H2 (level 2) — feeding the orchestrator's OVERVIEW route ("summarise the paper").

**Where.** `workers/tasks.py::generate_section_summaries`, `section_summaries` table,
`repositories/section_summaries.py`, `POST /papers/{id}/regenerate-summaries`.

**How it works.** Sections are grouped by `heading_path`, each summarised by the chat model, then
the section summaries are summarised into the paper-level one. Runs in the background after
embeddings (or straight from the pipeline when embeddings are skipped); the reader can already
read and ask while it runs. Idempotent unless `force`.

**Why.** A summary question over a book cannot be answered from top-k chunks — it needs the
outline. Computed once at ingestion because it is minutes of model time per book.

---

## 29. VLM figure descriptions

**What it does.** For every `figure` chunk, a vision model writes a description (what the figure
shows, axes, trends), stored privately in `figure_descriptions` and folded into that figure's
embedding input. The description is retrieval metadata: it is not added to chunk text and is not
shown in the frontend. When a user asks for a figure, semantic retrieval can use the description
to select the original image, which the chat can embed in its answer.

**Where.** [`extraction/vlm_client.py`](../../backend/app/extraction/vlm_client.py),
`generate_section_summaries` (second half) or the fast-profile figure task,
`GET /papers/{id}/figure-descriptions`, `GENERATE_FIGURE_DESCRIPTIONS`, `VLM_MODEL`,
`VLM_MAX_CONCURRENCY`.

**How it works.** The crop is base64-encoded into a multimodal chat request. `vlm_client` also
holds the **VLM page extractor** (`PAGE_PROMPT`) — an alternative to MinerU that asks a vision
model for the page's blocks as JSON. Its prompt carries two rules learned from real failures:
blank ablation-table cells must be emitted as empty `<td></td>` (the model was shifting values
into neighbouring columns), and long paragraphs must not be cut mid-sentence.

**Why.** A figure's caption rarely says what the figure *shows*; the private description is what
lets "which figure compares latency?" find it, while the reader continues to see only the paper's
own caption and image.

---

## 30. Paper-only mode

**What it does.** With `PAPER_ONLY_MODE=true`, a non-book document whose total `token_count` fits
under `PAPER_ONLY_MAX_TOKENS` skips embeddings entirely; the GLOBAL route is served from full-text
search plus stuffing the document into the context instead of pgvector.

**Where.** `pipeline_sync.py::_should_skip_embeddings`, `documents.embedding_mode` /
`embedding_skip_reason` (recorded once, never re-derived). Design:
[paper-only-embedding-skip.md](../plans/paper-only-embedding-skip.md).

**Why.** The claim being tested: a paper that fits whole in a large-context model does not need
retrieval. Off by default — only segment S1 (the skip) has landed; the doc's §12 says what must
land before enabling it. Under the default fast profile the whole-document skip happens before this
gate, but the separate figure task still runs when enabled; this gate matters for
`INGEST_PROFILE=full` and never skips books.

---

## 31. Bibliography parsing

**What it does.** A paper's References section is split into addressable entries
(`[N] → raw text`) and cached, so in-body `[12]` markers can become chips (feature 47).

**Where.** [`services/references.py::parse_references`](../../backend/app/services/references.py)
(pure), `paper_references` table, `GET /papers/{id}/references` (parses on first call).

**How it works.** Finds the `heading` chunk whose text is exactly "references"; the consecutive
`text` chunks after it are the bibliography; the first chunk of any other type ends it. ⚠ A
references entry is not one chunk: MinerU emits several entries per chunk with a literal `- [N] `
list marker starting each entry's line (chunk 120 in the corpus holds `[5]` through `[24]`), and
an entry can wrap onto a following line *without* the marker, so entries are split on where a new
`- [N]` starts, never on newlines. `title_candidates` (feature 47) then guesses the title inside
an entry.

**Why.** An empty list is a real answer: no References heading, or an author–year paper with no
numbered brackets (verified on a RoFormer-style paper).

---

## 32. Safe full re-embedding repairs

**What it does.** Re-embed one document or the whole library after changing the embedding model,
without ever serving an empty index in between.

**Where.** [`scripts/reembed_library.py`](../../backend/scripts/reembed_library.py),
`embeddings/service_sync.py` (forced repair mode), `workers/tasks.py::embed_document`
(PR #123).

**How it works.** The script queues one `embed_document` task per document. A forced repair takes
a **stable snapshot** of the chunk ids first — re-querying for "rows without embeddings" would
return the same rows forever after each upsert — then regenerates and **upserts by chunk primary
key**, so old vectors remain searchable until their replacements are committed. Nothing else is
touched: not extracted files, chunks, assets or summaries. Celery's worker concurrency bounds how
many documents run at once; `EMBEDDING_MAX_CONCURRENCY` bounds batches within one.

**Why.** The alternative — delete all vectors, then re-embed — leaves GLOBAL questions answering
from nothing for as long as the pass takes. The embedding pin (feature 99) is what forces a model
swap to be this explicit pass rather than a silent mix of incompatible vectors.
