# Area 1 — Library & getting documents in (features 1–18)

> Part of the [feature catalogue](README.md). Each entry: what it does, where it lives, how it
> works, why it is built that way (including what was tried and failed), and how to see it.
>
> **Reflects code as of:** 2026-09-12 (`main`, 3c72291 + drop-anywhere).

---

## 1. PDF upload: drag-and-drop or click

**What it does.** Drop a PDF **anywhere on the library view** (or click the dashed card), say
whether it is a *book* or a *research paper*, and it is stored and queued for extraction. The
processing overlay takes over until the document is readable.

**Where.** Client: [`App.tsx`](../../frontend/src/App.tsx) (`startUpload`, `pickFileWithKind`,
`handleFileUpload`), [`LibraryView.tsx`](../../frontend/src/views/LibraryView.tsx) (the dropzone),
`UploadKindModal`. Server: [`endpoints/documents.py::upload_paper`](../../backend/app/api/v1/endpoints/documents.py)
and `_stream_pdf_upload`.

**How it works.**
0. *The whole view is the drop target, not the card.* `LibraryView`'s root element handles
   `dragenter/dragover/dragleave/drop` for any drag whose `dataTransfer.types` includes `Files`,
   and a full-screen "Drop to add to your library" overlay (`.lib-drop-overlay`,
   `pointer-events: none`) shows while one is over it; the dashed card only mirrors that state.
   ⚠ Before 2026-09-12 only the card accepted drops, and a PDF dropped a few pixels below it was
   not ignored — with no handler claiming it, the browser did its default for a dropped file and
   **navigated the tab to it**, replacing the app with the PDF viewer. The reader assumed the
   upload had happened; the API never saw a request; the library had nothing. `dragenter` and
   `dragleave` fire for every child crossed, so the overlay is driven by a depth counter, not a
   boolean, and only clears when the drag really leaves the window. A drop with no PDF in it
   (a `.txt`, a `.docx`) shows a notice saying so — the old fallback of opening the file picker
   read as "the drop was lost"; a multi-PDF drop takes the first and says the rest must come one
   at a time, because the kind question is per file. Every other route has a safety net in
   `App.tsx`: a window-level `dragover`/`drop` listener that refuses (`dropEffect = 'none'`) any
   file drag nothing else claimed — it runs last in the bubble and checks `defaultPrevented`, so
   the book chat's image attachments (feature 63) keep working — and the app can no longer be
   navigated away by a stray drop.
1. *The kind chooser always runs first.* `startUpload(file?)` stores a dropped `File` in
   `pendingFile` and opens the modal. A click passes nothing. Either way the user must pick a
   `DocKind` — it decides which reader opens and whether the embedding pass runs at all, and a
   drop cannot state it.
2. `pickFileWithKind(kind)`: if `pendingFile` exists it is uploaded directly (no picker); otherwise
   an `<input type=file accept=.pdf>` is created and clicked. `pendingFile` is cleared on *both*
   exits of the modal (choose and cancel) — a file left there would be silently uploaded by the
   *next* click-initiated upload instead of the one the user picked.
3. `handleFileUpload` switches `route='processing'`, POSTs `multipart/form-data` to
   `/papers/upload?kind=…`, gets `{id, status:'processing'}` back, and starts polling
   `/papers/{id}/progress` every second (feature 5).
4. Server side, `_stream_pdf_upload` reads the multipart body in **1 MB chunks**, writing straight
   to `documents/<uuid>.pdf`, counting bytes as it goes: past `MAX_UPLOAD_SIZE_MB` (500) it raises
   `413` and unlinks the partial file; if the first 1 KB has no `%PDF-` header it raises `415`
   (the PDF spec requires the header within the first 1024 bytes — checking content, not the
   filename, blocks an executable renamed `.pdf`). A second copy goes to `assets/<doc_id>.pdf`
   (served by `/raw`, feature 11/12). Then a `documents` row (`status='queued'`), an
   `ingestion_jobs` row, and `process_ingestion.delay(...)` to Celery.

   As soon as the server accepts the upload, `App` signals `LibraryView` to reload immediately.
   This matters when the user presses **Back to library** before extraction starts: the library is
   still mounted underneath the processing panel, so returning to it must not wait for the normal
   settled-library poll. If the upload request itself is still in flight, its response sends the
   same signal when the committed document ID arrives.

**Why.**
- *The drop handler consumes `e.dataTransfer` synchronously.* It plucks the first PDF (by MIME or
  `.pdf` extension) inside `onDrop` before any state update — the `DataTransfer` is neutered once
  the handler returns, so a file read later (after the modal resolves) is already gone.
- *Streaming, not `await file.read()`.* The old code read the whole body into memory, then checked
  the size, then wrote it twice — peak memory of ~2× the file per request, multiplied by
  concurrent uploads ([docs/issues/010](../issues/010-upload-limit-is-checked-after-buffering.md)).
  The cap now bounds disk, which is what makes a 500 MB cap safe (Khaled's requirement: whole
  scanned books). ⚠ The cap lives in **three** places that must move together — the config
  default, the live `.env`, and nginx's `client_max_body_size`.
- *Queue capacity is reserved in the same transaction as the row.* `create_ingestion_job` takes a
  Postgres advisory lock, counts queued jobs, and inserts — so a burst of uploads cannot all pass a
  stale count (feature 18). A full queue rolls the whole thing back and removes the files.
- *If Celery dispatch fails* (Redis down), the document is marked `failed` with a message naming
  the broker, rather than sitting `queued` forever.

**See it.** Drop a PDF on the library. Try a 3 MB text file renamed `.pdf` → 415. Watch the
network tab: one `POST /papers/upload`, then `GET /progress` once a second.

---

## 2. Article import from a URL

**What it does.** Paste a web page's URL and read it like a paper — extracted to clean Markdown,
chunked, chatted with. If the link turns out to be a PDF, it is adopted as a paper/book instead.

**Where.** [`endpoints/documents.py::import_article`](../../backend/app/api/v1/endpoints/documents.py),
[`extraction/pipeline_sync.py::run_article_pipeline_sync`](../../backend/app/extraction/pipeline_sync.py),
[`services/article_extraction.py`](../../backend/app/services/article_extraction.py) (fetch cascade +
trafilatura extraction), [`scraping/*`](../../backend/app/scraping) (Firecrawl, CRW, Tavily-extract
clients). Client: `App.tsx::handleArticleImport`, `submitImportUrl`; the URL box lives inside the
same kind-picker modal.

**How it works.**
1. The endpoint creates the row as `doc_kind='article'` **regardless of the kind picked**, with the
   URL as the placeholder `original_filename` — the real title is unknown until the fetch runs. The
   picked kind (`book`/`paper`) travels to the Celery task as a *hint*.
2. `fetch_resource(url)` runs the cascade **Firecrawl → CRW → direct fetch**, each behind its own
   circuit breaker, after `_check_url_is_fetchable` (the SSRF guard, feature 92). A managed provider
   that reports "this is a PDF" is *not* trusted for its HTML: its server-side PDF→HTML conversion
   is lossy (no images, headings fused into paragraphs), so the pipeline discards it and fetches the
   real bytes directly — a PDF is a static file, essentially never behind the bot/JS challenge the
   cascade exists for.
3. If the fetch found a PDF, the hint kind is adopted and the document goes into the MinerU pipeline
   exactly like an upload. Otherwise `extract_article_from_html` runs trafilatura with
   `include_links=True` (real hyperlinks survive as clickable links), `upgrade_responsive_images`
   picks the largest `srcset` candidate, and a second pass **splices `<video>` elements back in**
   — trafilatura's readability heuristic throws video widgets away as chrome; they are re-anchored
   under the nearest heading that survived extraction, or appended at the end rather than dropped.
4. `try_tavily_extract_fallback` is a last-resort third tier: Tavily's `/extract` returns
   already-extracted text, never HTML, so it can save an import from failing but leaves nothing for
   the raw snapshot (feature 3).
5. Images are **hotlinked, never downloaded** — by request: no storage, no risk of pulling
   arbitrary bytes onto the box. The cost: an article's image can't be attached to a VLM call.

**Why.** Three pipelines, not one with branches: papers, books and articles differ end to end
(extractor, reader, chat), and the pipeline decides the *real* kind mid-flight. The overlay shows
the PDF step list optimistically when a `book`/`paper` kind was picked because most such links
turn out to be PDFs; if not, the job still finishes as an article — only the narration differs.

**See it.** Paste `https://blog.google/...` into the URL box. The overlay shows "Fetching page →
Extracting article → Chunking". Open the paper: links are clickable, videos play.

---

## 3. Raw HTML snapshot of imported articles

**What it does.** Alongside the extracted article, the app keeps *what the page actually looked
like* — a sanitized copy of the real HTML — viewable in-app, so a reader unsure whether the
extractor caught everything (a JS-hidden tab panel, say) can check the original.

**Where.** [`services/article_crawl.py`](../../backend/app/services/article_crawl.py)
(`sanitize_html`, `snapshot_article_page`, `save_crawled_pages`), `raw_snapshot_pages` table,
`GET /papers/{id}/raw` and `/raw/{page_id}` in `documents.py`,
[`RawArticleViewer.tsx`](../../frontend/src/views/RawArticleViewer.tsx),
[`lib/rawFrameSync.ts`](../../frontend/src/lib/rawFrameSync.ts).

**How it works.** The snapshot is a **separate, best-effort side quest** of the import: it only
ever sets `documents.raw_snapshot_status`, never `documents.status`, so a snapshot failure cannot
break the article the reader is using. `sanitize_html` uses lxml's Cleaner with a config verified
against a sample page containing a `<script>`, an `onclick=`, a `javascript:` href, an `onerror=`,
a meta-refresh, an `<iframe>`, a `<form>` and a `<style>` block: scripts/frames/forms stripped,
`meta=True` also removes `<meta http-equiv="refresh">` (a script-free redirect vector),
`page_structure=False` keeps `<head>` so a `<base href>` can be injected and the title preserved.
Lazy images are upgraded to their largest `srcset`, videos made playable. Served with
`Content-Security-Policy: script-src 'none'; object-src 'none'` — defense in depth for the same
guarantee at serve time. The viewer is an `<iframe>` pointed at `/raw`; `rawFrameSync` reads the
iframe's scroll position (same-origin by deployment design) so "Read structured" opens the
structured reader at the passage you were looking at, and vice versa via text anchors.

**Why.** An earlier version *crawled* same-site links to a bounded depth to snapshot multi-page
docs sites. Dropped: extracting the page's real hyperlinks into the article (`include_links=True`)
and letting the reader follow one themselves was simpler and more honest than chasing, caching and
sanitizing a site that can change under a saved snapshot. `depth` is still in the schema (always
0) so nothing downstream needed to change.

---

## 4. Three document kinds, three pipelines

**What it does.** `paper`, `book`, `article` are distinct end to end: extraction path, ingest
profile, reader component, chat path.

**Where.** `documents.doc_kind`; [`ReadingView.tsx`](../../frontend/src/views/ReadingView.tsx)
dispatches to `ArticleReader` (paper, article) or `BookReadingView` (book);
`pipeline_sync.py::_is_fast_ingest`; `chat-and-ask.md`'s three answering paths.

**How it works.** | kind | extractor | ingest | reader | asks via |
| --- | --- | --- | --- | --- |
| paper | MinerU | fast (readable after chunking) | ArticleReader, margin notes | paper agent (`/notes`) |
| book | MinerU | full (embeddings + summaries + VLM) | BookReadingView, chapter reveal | orchestrator (`/ask`), 4 context modes |
| article | trafilatura | fast | ArticleReader (no page numbers, no stepped mode) | paper agent |

**Why.** A book cannot be stuffed into a context window and full-text scanning a 700-page volume is
no substitute for vector retrieval — so books always take the full chain. A paper is readable the
moment MinerU finishes; everything the model needs is derived at question time. ⚠ `article` falls
through to `ArticleReader` as the *default*, not a third branch — anything added there lands on
articles too unless gated (stepped reading checks `doc_kind === 'paper'` explicitly). Rule of the
repo: a change to one pipeline is not a change to the others — check all three.

---

## 5. Live processing overlay

**What it does.** While a document is ingesting, a full-screen panel narrates the real steps
(Extracting structure → Chunking → Embedding → Summarizing), with a progress bar, the queue
position if the worker is busy, the extractor actually used, an error if it fails, a Cancel that
deletes the half-made document, and "Back to library".

**Where.** [`ProcessingOverlay.tsx`](../../frontend/src/views/ProcessingOverlay.tsx),
[`lib/progress.ts`](../../frontend/src/lib/progress.ts) (`STAGE_PROGRESS`, `stageProgress`),
`App.tsx::pollUploadProgress`, `GET /papers/{id}/progress`.

**How it works.** The poll (1 s) reads `status`, the finer `job_status`, `progress_fraction`
(pages extracted / total, reported by MinerU page batches), `queue_position` (this job's rank
among queued jobs by `created_at`), `extractor`, `error_message`. Step states are **derived from
the backend status, no fake timer**: a step is active when its `matches` list contains the current
status, done if an earlier one is active. The same `STAGE_PROGRESS` map (queued 0.06 → extracting
0.3 → chunking 0.55 → embedding 0.78 → summarizing 0.92 → complete 1) feeds the library card, the
deep-linked paper load and the overlay. On `complete` the cancel handle is dropped so a later
Cancel click can never delete a finished document.

**Leaving early.** **Back to library** only hides the overlay; processing continues and the
document remains in the database. It triggers an immediate library reload, and the library poll
effect is restarted so an older in-flight response cannot replace the fresh list. **Cancel** is
the separate action that deletes the document and its stored files.

**Why.** The step list is the same for every kind on purpose. It used to branch on `kind`, and a
paper showed both its steps "done" the moment it left chunking (the 4-item order lacked
`embedding`, so lookups returned −1) while the library card still showed 78 %. Completion is a
backend decision (`INGEST_PROFILE`) that has nothing to do with `kind`; if embedding is truly
skipped the status jumps to `complete` and every step is marked done — never dishonest, just
occasionally instant. The progress map was copy-pasted in three places and drifted; one shared
map removes the possibility.

---

## 6. Library shelf with cover thumbnails

**What it does.** Every card leads with the paper's first page as a picture.

**Where.** [`services/covers.py`](../../backend/app/services/covers.py), `GET /papers/{id}/cover`,
[`PaperCover.tsx`](../../frontend/src/views/PaperCover.tsx).

**How it works.** On first request, PyMuPDF rasterises page 1 at 480 px wide, JPEG quality 78, to
`storage/covers/<id>.jpg`; every later request is a file read. Rendering runs in
`run_in_threadpool` (50–200 ms of native CPU, and the grid asks for every cover at once). A paper
with no renderable cover answers **204, not 404**; `<img>` reports 204 as a load error, so
`PaperCover` keeps its `onError` fallback glyph. Cards use a fixed aspect `1 / 1.294` with
`object-position: top` — Letter and A4 differ, and rows of mismatched heights read as broken;
cropping from the bottom keeps the title and authors.

**Why.** At ten papers a filename tells them apart; at fifty it doesn't, and filenames are
routinely arXiv ids. *Lazy, not at ingestion*: ingestion is already the slow path and a cover is
worth nothing until the library is looked at; papers ingested before covers existed get them too.
*Keyed by id alone*: a first page cannot change — re-extraction rewrites derived text, never the
PDF — so there is no invalidation problem. JPEG not PNG: a scanned page is continuous-tone and PNG
stores it at ~6× the size for no visible gain at thumbnail scale. A wall of 404s made a working
library look broken; hence 204.

---

## 7. Grid / list layouts

**What it does.** Toggle between cards (grid) and rows (list). Both share `CardActions` and the
cover. The choice lives in `App.tsx` state for the session (it is not persisted — reopening the app
returns to the grid).

**Where.** `LibraryView.tsx` (`layout` / `setLayout` props), `App.tsx`.

**Why.** Two densities for two moods — recognising (grid) vs scanning many (list). One shared
action set so a fix to rename/delete/open lands in both.

---

## 8. Library search and sort (keyword + semantic)

**What it does.** The library's search box filters by substring over title and authors, *and* asks
the server for semantic hits — "find by what it's about". Sort cycles recent → title → pages.

**Where.** `LibraryView.tsx` (local filter and sort), `GET /papers/search?q=`,
[`services/library_search.py`](../../backend/app/services/library_search.py).

**How it works.** Each document gets a `search_embedding` of its title plus a short lead excerpt
(≤ 2000 chars), computed **lazily on its first appearance in a search** and stored on the row.
Cosine similarity below 0.3 is dropped (found empirically: below that it is "not what you typed"
more often than a real hit). The keyword filter still reaches everything below the line, so the
threshold only trims the semantic side's tail.

**Why.** Most documents are fast-ingested with **no whole-document chunk embeddings**; only the
small figure-only index is built for image retrieval. There is nothing useful for library search to
reuse, so one short embedding per document is cheap enough to piggyback on a search rather than
add a full ingestion step or a backfill job. `/papers/search` is registered before `/papers/{id}` so
the word "search" is never swallowed as a paper id.

---

## 9. Inline rename

**What it does.** Hover a card → pencil → the title becomes an input. Enter commits, Escape
reverts, blur commits. The new name appears everywhere (reader header, desk, exports).

**Where.** `LibraryView.tsx`, [`components/TitleEditor.tsx`](../../frontend/src/components/TitleEditor.tsx),
`PATCH /papers/{id}` → `documents.title`, [`lib/titles.ts::displayTitle`](../../frontend/src/lib/titles.ts),
mirrored server-side by `services/export.py::_display_title`.

**How it works.** `displayTitle` is the single resolver: a rename wins, else the filename minus
`.pdf`. The library poll is **paused while a rename is open** (`renamingRef`) — the poll replaces
the whole list every 2.5–10 s and a tick mid-edit would blow away the input.

**Why.** *A rename never touches disk.* `filename` is the on-disk key every storage path is built
from and `original_filename` is what `/raw` serves the download as; renaming those would break
both. The Raw files panel therefore still shows real filenames, correctly. The card's open target
is an inner `div`, not the `<article>`: rename/delete are real buttons, and a button nested inside
something that is itself `role="button"` makes assistive tech announce one control named "… 17p ·
read Rename this paper Delete this paper".

---

## 10. Delete paper

**What it does.** Removes the document, every DB row (cascade) and every on-disk artefact
(PDF, extraction, images, cover, snapshots), after an in-app confirm dialog.

**Where.** `DELETE /papers/{id}` → `services/documents.py::delete_document`;
[`components/ConfirmDialog.tsx`](../../frontend/src/components/ConfirmDialog.tsx) (`useConfirm`).

**Why.** Disk cleanup is best-effort: a missing file never prevents the row from being removed,
so a half-failed earlier delete can always be finished. The confirm is the app's own dialog, not
`window.confirm`, so it matches the theme and works inside the desk's overlays.

---

## 11. Raw files panel

**What it does.** A slide-in list of every original file — the PDF for papers/books, the HTML
snapshot for articles — searchable, with "open" (the in-app viewer) and a download link.

**Where.** [`RawFilesPanel.tsx`](../../frontend/src/views/RawFilesPanel.tsx), `GET /papers/{id}/raw`.

**How it works.** `/raw` branches on `doc_kind`: PDF → `FileResponse` with
`Content-Disposition` naming the file after the *display* title (rename wins) — only the suggested
save name changes, never the on-disk key; article → the sanitized snapshot HTML with the CSP
headers; nothing available → a small readable HTML message rather than a bare JSON 404 (it is
opened in a tab by a person, not fetched by JS). The list re-polls every 10 s while open.

---

## 12. Built-in PDF viewer

**What it does.** Read the original PDF in-app (`#/raw/<id>`): page navigation, page input, zoom
0.5–3×, and a "Read structured" button that opens the structured reader at the same page.

**Where.** [`PdfViewer.tsx`](../../frontend/src/views/PdfViewer.tsx) (react-pdf / pdf.js),
`lib/pageMap.ts` (page ↔ sequence mapping via `GET /papers/{id}/pages`).

**How it works.** The viewer is `lazy()`-loaded — pdf.js is the heaviest dependency and most
sessions never open it. The worker is **bundled from the installed `pdfjs-dist`**, not fetched
from unpkg: a CDN round-trip on the render path added latency to every open and occasionally left
the viewer blank with no error until a refresh warmed the cache. The PDF is loaded from the
authenticated `/raw` route with `withCredentials: true` (pdf.js has its own fetch, which does not
go through the app's credentialed wrapper) in a memoised `file` object (react-pdf compares by
identity and would reload on every render). `initialPage` clamps once the real page count is
known, and re-seeks when `paper.id` changes under an unchanged route.

**Why.** Served same-origin and build-pinned, both failure modes vanish. nginx needs an explicit
`text/javascript` for `.mjs` (the worker) or a browser refuses to run a module script and the
viewer stays blank with no visible error (feature 105).

---

## 13. Library export wizard

**What it does.** One top-bar button opens a four-step panel: *select* (search + Books / Research /
Articles chips + checklist) → *format* → *running* (progress bar, red Cancel that aborts the
request) → *done* (tick, real filename, Done).

**Where.** [`components/ExportWizard.tsx`](../../frontend/src/components/ExportWizard.tsx)
(`ExportWizard` = state + request, `ExportPanel` = pure render of one step), `POST /export`,
[`api.ts::downloadExport`](../../frontend/src/api.ts). Design: [library-export.md](../plans/library-export.md).

**How it works.** The selection lives only inside the panel; the request is `POST /export` with
`{format, document_ids | all}`; the response is streamed into a Blob with real progress from
`content-length`, then saved through a temporary `<a download>`, the filename read off
`Content-Disposition`. Cancel is a real `AbortController`; an abort returns to the format step
with the selection intact, not an error screen. Built on the `.confirm-*` dialog classes — the
app's one modal style.

**Why.** A previous version put a checkbox on every library card at all times, and its menu did
nothing until papers were ticked — a permanent visual tax for an occasional action, and a flow that
read as "broken" rather than "waiting": **zero export requests ever reached the API from it**
(every click died on an `if (!count) return`). Split into state + pure panel so every step can be
rendered and asserted directly — the clickable-citations regression shipped because `tsc` and a
build passed while the component threw on first render. POST, not a navigated GET, so a 401/500
is a readable error rather than a blank tab.

---

## 14. Export formats: BibTeX, Markdown, Anki, CSV

**Where.** [`services/export.py`](../../backend/app/services/export.py) (pure: data in, string/zip
out), `endpoints/export.py` (DB reads + resolution).

**How each works, and why.**
- **BibTeX** (`to_bibtex`): one entry per paper; `@article` when Semantic Scholar resolved real
  authors, `@misc` with an honest `note` when it didn't — never a blank author a reader might
  mistake for a gap in *their* bibliography. Every field through `_escape_bibtex` (`{ } & % $ #`
  and backslash, **backslash first** or the others' escapes get double-escaped). Cite keys are the
  title slug cut at a word boundary, collide-proofed with `a`/`b`/`c`. ⚠ `year` **only when
  resolved**: the first version fell back to the date *added*, which put `year = {2026}` on
  *Attention Is All You Need* (2017) — a fabricated year is worse than none.
- **Markdown** (`to_markdown_note` / `to_markdown_zip`): one `.md` per paper (Obsidian treats each
  file as a linkable unit; thirty tiny files per paper would be worse than thirty sections of one).
  **One paper is sent as the `.md` itself; two or more as `notes.zip`** — a single-paper export
  arrived as a ZIP with one file inside before this.
- **Anki** (`to_anki_tsv`): `question\tanswer` lines from the AI Q&A notes only (personal notes are
  free text, not a front/back pair). Each field is the answer's Markdown **rendered to HTML**
  (`markdown-it-py`, raw HTML escaped), `$x$`/`$$x$$` re-delimited to MathJax's `\( \)`/`\[ \]`
  *after* rendering (because `\(` is a CommonMark escape), then flattened to one line (a literal
  tab or newline breaks the TSV row). Zero qualifying cards → `422` naming what would make cards
  exist, not a 0-byte file.
- **CSV** (`to_library_csv`): the library index — title, authors, year, kind, pages, status,
  `date_added` (honestly named), source URL.
- **Citation markers are stripped** from every answer on the way out (`[[11]]`, `[[30], [31]]`,
  `[[P2:41]]`): outside the app they are noise, and in Obsidian `[[11]]` is wiki-link syntax that
  creates a phantom note called "11". Only digit/`P`/colon content matches, so a reader's own
  `[[My Note]]` survives.
- **Titles match the library** (`_display_title` mirrors `displayTitle`): shipped as
  `title = {Attention Is All You Need.pdf}` before this was mirrored.

---

## 15. Author/year enrichment on export

**What it does.** Papers with no known authors/year (the app never stored them — uploads carry only
a filename) are resolved against Semantic Scholar at export time, once, and the answer cached on
the row (`documents.self_resolve_status`, `resolved_authors`, `resolved_year`).

**Where.** `endpoints/export.py::_resolve_documents`, `search/semantic_scholar_client.py`.

**How it works.** Skips rows already `resolved` or `no_match` (completed lookups); retries
`unavailable` (no key, 429, network) on every export — fixing the key or waiting out the limit
should turn it into an answer, not require another trigger. Bounded by the caller's own library
(dozens to low hundreds), not a queue; permanently resolved papers cost nothing. Every call goes
through the shared 1 req/s Semantic Scholar line (feature 48).

**Why.** Caught after getting the exact `resolved`/`no_match`/`unavailable` distinction backwards
once in the sibling citation-resolve endpoint.

---

## 16. Re-extract / re-chunk / regenerate summaries

**What it does.** Per-paper repair actions.
- **Re-chunk** (`POST /papers/{id}/rechunk`): re-run the chunker on the cached MinerU output in
  `storage/extracted/<id>/` without re-running MinerU — after improving the chunker (equation
  stitching, footnote detection, Unicode math normalisation), apply it to papers already on disk.
  Clears chunks/embeddings/assets and rebuilds; `409` if there is no cached extraction.
- **Re-extract** (`POST /papers/{id}/reextract`): wipe extraction + images and run MinerU again.
  ⚠ The existing chunks are deleted **in the same transaction that reserves the ingestion job**,
  so a full queue rolls back and leaves the paper untouched — checking any later would leave a
  paper with nothing to read and no job queued to fix it.
- **Regenerate summaries** (`POST /papers/{id}/regenerate-summaries?force=`): re-dispatch the
  hierarchical summarisation + VLM figure descriptions (feature 28/29), e.g. after changing the
  chat model. Idempotent inside the summariser unless `force`.

**Where.** `endpoints/documents.py`, `workers/tasks.py`.

**Why.** The heavy parts run in `run_in_threadpool` / Celery — re-chunking walks a
`content_list.json` that runs to megabytes on a book, and doing it on the event loop stalled every
other request the worker was holding.

---

## 17. Reading-order reconstruction

**What it does.** For two-column papers whose MinerU order is scrambled (figure spanning columns,
broken continuations), an LLM re-orders the blocks; the book reader can then follow that order
(feature 61).

**Where.** `POST /papers/{id}/reconstruct-reading-order`,
[`services/reading_order.py`](../../backend/app/services/reading_order.py), `workers/tasks.py::reconstruct_reading_order`,
`documents.reading_order` (JSON array of sequence ids).

**How it works.** The chunks with their bounding boxes and types are sent page by page with a
system prompt encoding the rules (left column top-to-bottom, then right; a spanning figure where
first referenced; captions with their figure; headings start sections). The model returns
`{"reading_order": [12, 13, 14, 20, …]}` — only existing `sequence_id`s are accepted. The reader
polls `/papers/{id}` until `reading_order` appears.

**Why.** Optional and per-paper: it costs model time and most papers don't need it. It never
rewrites the chunks — the physical order stays the source of truth, the logical order is a view.

---

## 18. Ingestion queue cap

**What it does.** A hard ceiling (`MAX_QUEUED_INGESTION_JOBS`, 50) on jobs queued or in progress.
Past it, a new upload/import/reextract is rejected with `429 QUEUE_FULL` before anything is
written.

**Where.** [`services/ingestion.py`](../../backend/app/services/ingestion.py)
(`_reserve_queue_capacity`, `create_ingestion_job`), `api/errors.py::TooManyQueuedJobs`.

**How it works.** `SELECT pg_advisory_xact_lock(hashtext('9xaipal:ingestion_queue'))`, then the
count, then the insert, all in the caller's transaction; the lock releases on commit/rollback. An
upload also takes a cheap, non-atomic look at the count *before* streaming the body, so a 300 MB
book is not pushed up the wire only to be refused; the locked check after the write remains the
authority.

**What the reader sees.** Not a failure. The `429` carries `code: QUEUE_FULL` plus `queued` and
`limit`; `api.ts` raises a typed `QueueFullError` and the processing overlay switches to its own
state — header "HTTP 429 · queue full", "50 of 50 slots are taken by documents still being
extracted", "your file was not uploaded and nothing is left behind", every step left pending
(nothing ran, so nothing is painted red), a **Try again now** button that resubmits the same file
with the same kind, and an automatic retry every 45 s with a visible countdown that stops the
moment the reader leaves. The first version showed the same sentence under a "Failed" header
with the *Extracting structure* step in red and only "Back to library" — a decline dressed as a
crash, and the file had to be picked again.

**Why.** The Celery worker runs `--concurrency=1`; this is what stops an upload burst from growing
disk and DB rows unbounded. The first version checked the count *before* the transaction, so
concurrent requests could all pass a stale count ([docs/issues/007](../issues/007-ingestion-queue-capacity-check-races.md)).
An advisory lock needs no schema row and cannot be forgotten open.
