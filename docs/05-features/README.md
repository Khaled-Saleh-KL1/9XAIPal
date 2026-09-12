# The feature catalogue

> **What this is:** every feature 9XAIPal has — the ones a reader sees and the nitty-gritty ones
> underneath — each written up in enough detail to study it: what it does, exactly where it lives,
> how it is implemented, why it is built that way (including what was tried first and failed), and
> how to see it working. Written for Khaled to learn his own program from, feature by feature.
>
> **How to read it:** the table below is the index — 107 features in nine areas, numbered straight
> through (plus one, #108, found while writing this: cross-session memory). Each area is one file;
> each feature is one section. Read an area top to bottom, or jump to a number.
>
> **How it relates to the rest of `docs/`:** the architecture docs
> ([02-architecture](../02-architecture/)) explain *subsystems*; the reference docs
> ([03-reference](../03-reference/)) list *contracts*; the plans ([plans](../plans/)) record
> *decisions* at the time they were made. This catalogue cuts across all of them by *feature*, and
> links out wherever the deeper material lives. When they disagree, the code wins and this file
> needs a fix.
>
> **Status:** current · **Reflects code as of:** 2026-09-12 (`main`, cb67f64 + done-reading). Every entry was
> written against the source files it names, on that commit.

## The areas

| Area | File | Features |
| --- | --- | --- |
| Library & getting documents in | [01-library-and-import.md](01-library-and-import.md) | 1–18, 109 |
| The extraction pipeline | [02-extraction-pipeline.md](02-extraction-pipeline.md) | 19–32 |
| The paper / article reader | [03-paper-reader.md](03-paper-reader.md) | 33–58 |
| The book reader | [04-book-reader.md](04-book-reader.md) | 59–65, 111 |
| Asking: the AI answering paths | [05-asking.md](05-asking.md) | 66–75, 108 |
| The desk: studies, cross-paper chat, notes | [06-desk.md](06-desk.md) | 76–85, 110 |
| Accounts, safety, capacity | [07-accounts-and-safety.md](07-accounts-and-safety.md) | 86–94 |
| Models & configuration | [08-models-and-configuration.md](08-models-and-configuration.md) | 95–100 |
| Operations | [09-operations.md](09-operations.md) | 101–107 |

## All features

| # | Feature | Where to read |
| --- | --- | --- |
| 1 | PDF upload: drag-and-drop or click | [library and import](01-library-and-import.md#1-pdf-upload-drag-and-drop-or-click) |
| 2 | Article import from a URL | [library and import](01-library-and-import.md#2-article-import-from-a-url) |
| 3 | Raw HTML snapshot of imported articles | [library and import](01-library-and-import.md#3-raw-html-snapshot-of-imported-articles) |
| 4 | Three document kinds, three pipelines | [library and import](01-library-and-import.md#4-three-document-kinds-three-pipelines) |
| 5 | Live processing overlay | [library and import](01-library-and-import.md#5-live-processing-overlay) |
| 6 | Library shelf with cover thumbnails | [library and import](01-library-and-import.md#6-library-shelf-with-cover-thumbnails) |
| 7 | Grid / list layouts | [library and import](01-library-and-import.md#7-grid--list-layouts) |
| 8 | Library search and sort (keyword + semantic) | [library and import](01-library-and-import.md#8-library-search-and-sort-keyword--semantic) |
| 9 | Inline rename | [library and import](01-library-and-import.md#9-inline-rename) |
| 10 | Delete paper | [library and import](01-library-and-import.md#10-delete-paper) |
| 11 | Raw files panel | [library and import](01-library-and-import.md#11-raw-files-panel) |
| 12 | Built-in PDF viewer | [library and import](01-library-and-import.md#12-built-in-pdf-viewer) |
| 13 | Library export wizard | [library and import](01-library-and-import.md#13-library-export-wizard) |
| 14 | Export formats: BibTeX, Markdown, Anki, CSV | [library and import](01-library-and-import.md#14-export-formats-bibtex-markdown-anki-csv) |
| 15 | Author/year enrichment on export | [library and import](01-library-and-import.md#15-authoryear-enrichment-on-export) |
| 16 | Re-extract / re-chunk / regenerate summaries | [library and import](01-library-and-import.md#16-re-extract--re-chunk--regenerate-summaries) |
| 17 | Reading-order reconstruction | [library and import](01-library-and-import.md#17-reading-order-reconstruction) |
| 18 | Ingestion queue cap | [library and import](01-library-and-import.md#18-ingestion-queue-cap) |
| 19 | MinerU structural extraction | [extraction pipeline](02-extraction-pipeline.md#19-mineru-structural-extraction) |
| 20 | Page-batched extraction | [extraction pipeline](02-extraction-pipeline.md#20-page-batched-extraction) |
| 21 | PyMuPDF fallback | [extraction pipeline](02-extraction-pipeline.md#21-pymupdf-fallback) |
| 22 | Glyph repair | [extraction pipeline](02-extraction-pipeline.md#22-glyph-repair) |
| 23 | Sub/sup garbling repair and leaked-`<sup>` caption cleanup | [extraction pipeline](02-extraction-pipeline.md#23-subsup-garbling-repair-and-leaked-sup-caption-cleanup) |
| 24 | Structure-aware chunking | [extraction pipeline](02-extraction-pipeline.md#24-structure-aware-chunking) |
| 25 | Figure, table and equation crops | [extraction pipeline](02-extraction-pipeline.md#25-figure-table-and-equation-crops) |
| 26 | Fast vs full ingest profile | [extraction pipeline](02-extraction-pipeline.md#26-fast-vs-full-ingest-profile) |
| 27 | Embeddings (pgvector) | [extraction pipeline](02-extraction-pipeline.md#27-embeddings-pgvector) |
| 28 | Hierarchical section summaries | [extraction pipeline](02-extraction-pipeline.md#28-hierarchical-section-summaries) |
| 29 | VLM figure descriptions | [extraction pipeline](02-extraction-pipeline.md#29-vlm-figure-descriptions) |
| 30 | Paper-only mode | [extraction pipeline](02-extraction-pipeline.md#30-paper-only-mode) |
| 31 | Bibliography parsing | [extraction pipeline](02-extraction-pipeline.md#31-bibliography-parsing) |
| 32 | Safe full re-embedding repairs | [extraction pipeline](02-extraction-pipeline.md#32-safe-full-re-embedding-repairs) |
| 33 | Whole-document article view | [paper reader](03-paper-reader.md#33-whole-document-article-view) |
| 34 | Block renderer | [paper reader](03-paper-reader.md#34-block-renderer) |
| 35 | Equation fallback to the page crop | [paper reader](03-paper-reader.md#35-equation-fallback-to-the-page-crop) |
| 36 | Stepped reading (papers only) | [paper reader](03-paper-reader.md#36-stepped-reading-papers-only) |
| 37 | Text-selection "Ask" pill → margin note | [paper reader](03-paper-reader.md#37-text-selection-ask-pill--margin-note) |
| 38 | Ask about a figure, equation, or table | [paper reader](03-paper-reader.md#38-ask-about-a-figure-equation-or-table) |
| 39 | Ask about the block in view (`A`) | [paper reader](03-paper-reader.md#39-ask-about-the-block-in-view-a) |
| 40 | Margin notes (AI) | [paper reader](03-paper-reader.md#40-margin-notes-ai) |
| 41 | Personal notes | [paper reader](03-paper-reader.md#41-personal-notes) |
| 42 | Decks (flashcards) | [paper reader](03-paper-reader.md#42-decks-flashcards) |
| 43 | Bookmarks | [paper reader](03-paper-reader.md#43-bookmarks) |
| 44 | Reading position memory (and the personal-state migration) | [paper reader](03-paper-reader.md#44-reading-position-memory-and-the-personal-state-migration) |
| 45 | Marginalia panel (`I`) | [paper reader](03-paper-reader.md#45-marginalia-panel-i) |
| 46 | Quote highlights | [paper reader](03-paper-reader.md#46-quote-highlights) |
| 47 | Clickable bibliography citations | [paper reader](03-paper-reader.md#47-clickable-bibliography-citations) |
| 48 | Citation lookup queue | [paper reader](03-paper-reader.md#48-citation-lookup-queue) |
| 49 | Citation chips with page numbers | [paper reader](03-paper-reader.md#49-citation-chips-with-page-numbers) |
| 50 | The agent trail | [paper reader](03-paper-reader.md#50-the-agent-trail) |
| 51 | The evidence panel | [paper reader](03-paper-reader.md#51-the-evidence-panel) |
| 52 | Strict-scope toggle | [paper reader](03-paper-reader.md#52-strict-scope-toggle) |
| 53 | Per-note model picker | [paper reader](03-paper-reader.md#53-per-note-model-picker) |
| 54 | Image lightbox | [paper reader](03-paper-reader.md#54-image-lightbox) |
| 55 | Mermaid diagrams in answers | [paper reader](03-paper-reader.md#55-mermaid-diagrams-in-answers) |
| 56 | Responsive tiers | [paper reader](03-paper-reader.md#56-responsive-tiers) |
| 57 | Keyboard shortcuts | [paper reader](03-paper-reader.md#57-keyboard-shortcuts) |
| 58 | The door to the desk (`P`, bottom-left button) | [paper reader](03-paper-reader.md#58-the-door-to-the-desk-p-bottom-left-button) |
| 59 | Chapter-by-chapter reveal reader | [book reader](04-book-reader.md#59-chapter-by-chapter-reveal-reader) |
| 60 | One-unit-at-a-time reveal | [book reader](04-book-reader.md#60-one-unit-at-a-time-reveal) |
| 61 | AI-corrected reading order toggle | [book reader](04-book-reader.md#61-ai-corrected-reading-order-toggle) |
| 62 | Error recovery in the reader | [book reader](04-book-reader.md#62-error-recovery-in-the-reader) |
| 63 | The side chat pane | [book reader](04-book-reader.md#63-the-side-chat-pane) |
| 64 | The progress ceiling | [book reader](04-book-reader.md#64-the-progress-ceiling) |
| 65 | Jump-to-sequence from the desk | [book reader](04-book-reader.md#65-jump-to-sequence-from-the-desk) |
| 66 | The paper agent | [asking](05-asking.md#66-the-paper-agent) |
| 67 | Synthesised index for heading-less papers | [asking](05-asking.md#67-synthesised-index-for-heading-less-papers) |
| 68 | The routed orchestrator: four context modes, router, guardrail, multimodal | [asking](05-asking.md#68-the-routed-orchestrator-four-context-modes-router-guardrail-multimodal) |
| 69 | Conversation continuity, sub-threads, and compaction | [asking](05-asking.md#69-conversation-continuity-sub-threads-and-compaction) |
| 70 | The research agent | [asking](05-asking.md#70-the-research-agent) |
| 71 | The web search cascade | [asking](05-asking.md#71-the-web-search-cascade) |
| 72 | Inline paper figures in answers | [asking](05-asking.md#72-inline-paper-figures-in-answers) |
| 73 | The grounding check on all three surfaces | [asking](05-asking.md#73-the-grounding-check-on-all-three-surfaces) |
| 74 | Ask traces | [asking](05-asking.md#74-ask-traces) |
| 75 | Direct retrieval endpoints | [asking](05-asking.md#75-direct-retrieval-endpoints) |
| 76 | Studies | [desk](06-desk.md#76-studies) |
| 77 | The paper picker | [desk](06-desk.md#77-the-paper-picker) |
| 78 | The study agent | [desk](06-desk.md#78-the-study-agent) |
| 79 | Cross-paper citations that open where they sit | [desk](06-desk.md#79-cross-paper-citations-that-open-where-they-sit) |
| 80 | Streaming transcript with the trail | [desk](06-desk.md#80-streaming-transcript-with-the-trail) |
| 81 | Sticky notes: the chat board | [desk](06-desk.md#81-sticky-notes-the-chat-board) |
| 82 | Sticky notes: the universal wall | [desk](06-desk.md#82-sticky-notes-the-universal-wall) |
| 83 | Agent-written notes, and who wrote them | [desk](06-desk.md#83-agent-written-notes-and-who-wrote-them) |
| 84 | Clear chat, rename, delete study | [desk](06-desk.md#84-clear-chat-rename-delete-study) |
| 85 | Deep links | [desk](06-desk.md#85-deep-links) |
| 86 | Open signup, login, logout, `/me` | [accounts and safety](07-accounts-and-safety.md#86-open-signup-login-logout-me) |
| 87 | Per-user data isolation | [accounts and safety](07-accounts-and-safety.md#87-per-user-data-isolation) |
| 88 | Auth rate limiting | [accounts and safety](07-accounts-and-safety.md#88-auth-rate-limiting) |
| 89 | Concurrent-user cap and the waiting room | [accounts and safety](07-accounts-and-safety.md#89-concurrent-user-cap-and-the-waiting-room) |
| 90 | Concurrent-signup safety | [accounts and safety](07-accounts-and-safety.md#90-concurrent-signup-safety) |
| 91 | Authenticated file serving | [accounts and safety](07-accounts-and-safety.md#91-authenticated-file-serving) |
| 92 | SSRF protection | [accounts and safety](07-accounts-and-safety.md#92-ssrf-protection) |
| 93 | Bounded image proxy | [accounts and safety](07-accounts-and-safety.md#93-bounded-image-proxy) |
| 94 | Security headers | [accounts and safety](07-accounts-and-safety.md#94-security-headers) |
| 95 | Ollama local models and five cloud providers | [models and configuration](08-models-and-configuration.md#95-ollama-local-models-and-five-cloud-providers) |
| 96 | Model roles | [models and configuration](08-models-and-configuration.md#96-model-roles) |
| 97 | `/models` list and per-request override | [models and configuration](08-models-and-configuration.md#97-models-list-and-per-request-override) |
| 98 | Multi-key rotation | [models and configuration](08-models-and-configuration.md#98-multi-key-rotation) |
| 99 | The embedding pin | [models and configuration](08-models-and-configuration.md#99-the-embedding-pin) |
| 100 | Health endpoint and the circuit breaker | [models and configuration](08-models-and-configuration.md#100-health-endpoint-and-the-circuit-breaker) |
| 101 | The Docker Compose stack | [operations](09-operations.md#101-the-docker-compose-stack) |
| 102 | LAN / single-port mode | [operations](09-operations.md#102-lan--single-port-mode) |
| 103 | Idempotent migrations | [operations](09-operations.md#103-idempotent-migrations) |
| 104 | CI and automatic deploy with rollback | [operations](09-operations.md#104-ci-and-automatic-deploy-with-rollback) |
| 105 | nginx same-origin serving | [operations](09-operations.md#105-nginx-same-origin-serving) |
| 106 | Docs discipline | [operations](09-operations.md#106-docs-discipline) |
| 107 | The test suite | [operations](09-operations.md#107-the-test-suite) |
| 108 | Cross-session memory about the reader | [asking](05-asking.md#108-cross-session-memory-about-the-reader) |
| 109 | Done Reading: a second shelf, with folders | [library and import](01-library-and-import.md#109-done-reading-a-second-shelf-with-folders) |
| 110 | Shelved paper lists: the rail and the picker as a file tree | [desk](06-desk.md#110-shelved-paper-lists-the-rail-and-the-picker-as-a-file-tree) |
| 111 | Notes as movable icons (book reader) | [book reader](04-book-reader.md#111-notes-as-movable-icons) |

## How each entry is written

- **What it does** — the behaviour a reader sees, in one or two sentences.
- **Where** — the files, functions, tables, endpoints and settings that make it, linked.
- **How it works** — the mechanism, step by step, with the numbers that matter (limits,
  thresholds, timings) and where they come from.
- **Why** — the design decision, and the failure it answers: what was tried before, what broke,
  what a naive version gets wrong. This is the part worth studying; most of it is lifted from the
  `⚠` comments in the code, which are the project's memory.
- **See it / verify** — where present, how to watch the feature do its thing.

Cross-references between features are by number (`feature 48`). Links to `docs/issues/NNN` point
at the 2026-09-10 audit reports, which record the security and reliability fixes in the same
what / why / how shape.
