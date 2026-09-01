# API reference

> **What this is:** every HTTP endpoint, its payload, and its errors. Look-up only, no narrative.
>
> **Owns:** endpoint paths, request/response shapes, status codes.
> **Does not own:** why a route behaves as it does ([chat-and-ask.md](../02-architecture/chat-and-ask.md)).
>
> **Status:** current · **Last verified:** the `/auth/*` routes 2026-08-27 against
> [`endpoints/auth.py`](../../backend/app/api/v1/endpoints/auth.py) (`main`, 502272b); the rest
> 2026-07-28 against [`api/v1/router.py`](../../backend/app/api/v1/router.py) (`main`, 5471870).
> The note anchor payload was re-read 2026-08-18 against
> [`endpoints/notes.py`](../../backend/app/api/v1/endpoints/notes.py) (`8fb153b`): **not**
> against live OpenAPI, which was not running.
> **Verify with:** `http://localhost:8000/docs` (live OpenAPI, always authoritative)
>
> ⚠ Every route below except `/health` and `/auth/*` requires a logged-in session (an
> `httponly` cookie, see [auth.md](../02-architecture/auth.md)) and is scoped to the caller's own
> data. A request for a resource you don't own **404s**, not 403, since existence is never confirmed to
> a non-owner.

All endpoints live under the prefix **`/api/v1`** and are registered in
[api/v1/router.py](../../backend/app/api/v1/router.py).

```
GET    /health
POST   /auth/signup
POST   /auth/login
POST   /auth/logout
GET    /auth/me
POST   /papers/upload
GET    /papers
GET    /papers/{paper_id}
GET    /papers/{paper_id}/progress
GET    /papers/{paper_id}/raw
GET    /papers/{paper_id}/raw/{page_id}
GET    /papers/{paper_id}/cover
PATCH  /papers/{paper_id}
DELETE /papers/{paper_id}
POST   /papers/{paper_id}/rechunk
POST   /papers/{paper_id}/reextract
POST   /papers/{paper_id}/regenerate-summaries
POST   /papers/{paper_id}/reconstruct-reading-order
GET    /papers/{paper_id}/document
GET    /papers/{paper_id}/chunks
GET    /papers/{paper_id}/chunks/{sequence_order}
GET    /papers/{paper_id}/chunks/after/{sequence_order}
GET    /papers/{paper_id}/chapters
GET    /papers/{paper_id}/figure-descriptions
GET    /papers/{paper_id}/notes
POST   /papers/{paper_id}/notes/stream
PATCH  /papers/{paper_id}/notes/{note_id}/margin
DELETE /papers/{paper_id}/notes/{note_id}
GET    /papers/{paper_id}/personal
GET    /papers/{paper_id}/bookmarks
POST   /papers/{paper_id}/bookmarks
PATCH  /papers/{paper_id}/bookmarks/{bookmark_id}
DELETE /papers/{paper_id}/bookmarks/{bookmark_id}
GET    /papers/{paper_id}/personal-notes
POST   /papers/{paper_id}/personal-notes
PATCH  /papers/{paper_id}/personal-notes/{note_id}
DELETE /papers/{paper_id}/personal-notes/{note_id}
GET    /papers/{paper_id}/decks
PUT    /papers/{paper_id}/decks

GET    /studies
POST   /studies
GET    /studies/{study_id}
PATCH  /studies/{study_id}
DELETE /studies/{study_id}
PUT    /studies/{study_id}/papers
GET    /studies/{study_id}/chat
POST   /studies/{study_id}/chat/stream
DELETE /studies/{study_id}/chat

GET    /stickies
POST   /stickies
PATCH  /stickies/{sticky_id}
DELETE /stickies/{sticky_id}
POST   /papers/{paper_id}/ask
POST   /papers/{paper_id}/ask/stream
GET    /papers/{paper_id}/chat
GET    /papers/{paper_id}/conversations
GET    /models
GET    /search/vector
GET    /search/web
```

Static (not under `/api/v1`):

```
GET /static/images/<doc_id>/<file>           : extracted chunk images
GET /static/extracted/<doc_id>/...           : raw MinerU output
GET /static/assets/<doc_id>.pdf              : original PDF, direct served
GET /static/images/research/<conv_id>/<file> : research-agent-saved images
```

## Health

### `GET /health`

Source: [endpoints/health.py](../../backend/app/api/v1/endpoints/health.py).

```json
{
  "status": "ok" | "degraded",
  "database": "ok" | "unavailable",
  "ollama":   "ok" | "unavailable",
  "web_search": "ok" | "unavailable",
  "web_search_provider": "google" | "tavily" | "linkup" | "exa" | "serpapi" | "duckduckgo" | "none"
}
```

Probes the DB, Ollama (`/api/tags`), and the web search cascade. Overall `status` is `degraded` if
the database is unavailable.

⚠ `web_search_provider` names the **first** provider in cascade order that has a key configured —
not necessarily the one that answers any given query, since a later provider may serve a request
the first one failed or returned empty for. See [configuration.md § Web
search](configuration.md#web-search) for the full cascade order.

⚠ **`web_search` does not prove a key actually works.** None of the five keyed providers expose a
health endpoint, so the only way to verify a key is to spend a search credit, and this endpoint is
the container healthcheck polled every 15-30 seconds. The field means "at least one provider is
usable" — which, since `duckduckgo` needs no key at all, is effectively always true; a bad key
surfaces as a logged `401`/`403` on the first real search for that
provider, and the cascade falls through to the next one.

---

## Auth

Source: [endpoints/auth.py](../../backend/app/api/v1/endpoints/auth.py). See
[auth.md](../02-architecture/auth.md) for the design (session mechanics, the concurrent-user
capacity cap, password hashing).

### `POST /auth/signup`

Open — anyone can create an account, no invite code.

Request:

```json
{ "email": "you@example.com", "password": "<8-200 chars>", "display_name": "optional" }
```

Response: `201 Created`, sets the session cookie, body is a `UserResponse` (`id`, `email`,
`display_name`, `created_at`, never `password_hash`).

Errors: `409` if the email is already registered (generic message — signup being open makes this
otherwise a user-enumeration leak). `429 QUEUE_FULL` never applies to signup itself, only uploads
— see [`POST /papers/upload`](#post-papersupload).

### `POST /auth/login`

Request: `{ "email": "...", "password": "..." }`

Response: `200 OK`, sets the session cookie, body is a `UserResponse`.

`401 Invalid email or password` for both "no such account" and "wrong password": the check runs
a real Argon2 verify either way (against a precomputed dummy hash when the email doesn't exist),
so the two cases take similar time and neither discloses whether an email is registered.

### `POST /auth/logout`

No body. `204 No Content`. Idempotent: logging out with no session, or an already-expired one,
still succeeds.

### `GET /auth/me`

No auth required to call it, that's the point. `200 OK` with
`{ "user": <UserResponse> | null, "admitted": bool, "queue_position": int | null }`. The frontend
calls this once on load to decide whether to show the app, the login screen, or the waiting room
(`admitted: false` — the site is at its concurrent-user cap and this session hasn't been let in
yet), and polls it every few seconds *while* showing the waiting room. This is the one endpoint
that answers `200` for a logged-in-but-queued user; every other endpoint 423s them — see the note
below.

⚠ **Every endpoint behind `get_current_user`** (everything except `/auth/*`) can answer
`423 Locked` with `{ "detail": ..., "code": "NOT_ADMITTED", "queue_position": N }` instead of its
normal response — a queued session simply can't use anything until admitted. See
[auth.md § capacity](../02-architecture/auth.md#4-capacity-the-waiting-room).

---

## Papers

### `POST /papers/upload`

Source: [endpoints/documents.py](../../backend/app/api/v1/endpoints/documents.py).

Multipart upload of a single PDF.

Request: `multipart/form-data` with `file=<binary>`.

Errors: `429 QUEUE_FULL` if the ingestion queue is already at `MAX_QUEUED_INGESTION_JOBS` — checked
before anything is written to disk. Same check, same error, on `POST /papers/import-url` and
`POST /papers/{paper_id}/reextract` below.

Response: `201 Created`

```json
{
  "id":       "<uuid>",
  "filename": "<uuid>.pdf",
  "status":   "processing",
  "message":  "Document uploaded and queued for processing"
}
```

Side effects:

- Writes `documents/<uuid>.pdf` (used by MinerU).
- Writes `assets/<doc_id>.pdf` (used by `/raw` and `/static/assets/...`).
- Inserts a `documents` row (`status='queued'`).
- Inserts an `ingestion_jobs` row (`status='queued'`).
- Dispatches `process_ingestion.delay(doc_id, job_id, filename)` to Celery.
  If Celery dispatch fails, the document is marked `failed` with a
  descriptive message.

### `GET /papers`

List papers, newest first.

Query: `?limit=50&offset=0`.

Response:

```json
{
  "documents": [DocumentResponse, ...],
  "total":     <int>
}
```

`DocumentResponse` ([schemas/documents.py](../../backend/app/schemas/documents.py)):

```ts
{
  id: UUID,
  filename: string,                   // storage-side uuid
  original_filename: string,
  title: string | null,               // reader-chosen name; null = use original_filename
  file_size_bytes: number | null,
  page_count: number | null,
  status: "queued" | "extracting" | "chunking" | "embedding" | "complete" | "failed",
  error_message: string | null,
  extractor: "mineru" | "pymupdf_fallback" | null,
  created_at: string,
  updated_at: string | null
}
```

### `GET /papers/{paper_id}`

Single paper. Returns `DocumentResponse` or `404 DocumentNotFound`.

### `GET /papers/{paper_id}/progress`

The frontend's polling endpoint during ingestion.

```json
{
  "paper_id": "<uuid>",
  "status": "queued|complete|failed",
  "job_status": "queued|extracting|chunking|embedding|summarizing|complete|failed",
  "progress_fraction": <float|null>,
  "queue_position": <int|null>,
  "page_count": <int|null>,
  "error_message": <string|null>,
  "extractor": "mineru|pymupdf_fallback|null",
  "raw_snapshot_status": "none|pending|complete|failed",
  "raw_page_count": <int|null>
}
```

`queue_position` is 1-based, only while `job_status === "queued"` — this box's Celery worker runs
`--concurrency=1`, so it's a real wait, not decoration: how many other still-queued jobs got there
first. `null` once extraction actually starts.

`raw_snapshot_status`/`raw_page_count` are `article`-only (`"none"` for a `paper`/`book`) — the
raw HTML snapshot (`GET /papers/{paper_id}/raw`, above) is saved inline, using the HTML already
fetched for extraction, no second network round trip — so by the time `status` reads
`"complete"`, `raw_snapshot_status` is already resolved to `"complete"` or `"failed"` too, not
left `"pending"` in the background. A `"failed"` snapshot never means the article failed to
import — only that its raw copy didn't save.

### `GET /papers/{paper_id}/raw`

The raw copy of this document — what it actually branches on is `doc_kind`:

- **`paper`/`book`:** streams the original uploaded PDF as `application/pdf`, with
  `Content-Disposition` honoring the original filename. Falls back from
  `assets/<id>.pdf` to `documents/<filename>` if needed.
- **`article`:** a sanitized raw HTML snapshot of the imported page — just that one page, not
  anything it links to (see
  [`services/article_crawl.py`](../../backend/app/services/article_crawl.py)) — served as
  `text/html` with `Content-Security-Policy: script-src 'none'; object-src 'none'` (the
  snapshot's `<script>` tags and event-handler attributes are already stripped at save time;
  this header is defense in depth, not the only protection). If no snapshot exists yet or the
  save failed, a small readable message is returned instead of a bare JSON 404 — this endpoint
  is meant for direct browser navigation.

### `GET /papers/{paper_id}/raw/{page_id}`

One specific raw HTML snapshot page. `404 DocumentNotFound` if `page_id` doesn't belong to
`paper_id` — ownership-checked the same way every other resource in this app is (the
cross-tenant-404 invariant, not a 403). ⚠ Reachable via the row's own `id`, but
`GET /papers/{paper_id}/raw` (above) is the one URL to actually use — with at most one snapshot
page per article now, it already serves that page directly.

### `PATCH /papers/{paper_id}`

Rename a paper. Body `{"title": "<name>" | null}` → the updated `DocumentResponse`.
`404 DocumentNotFound` if it does not exist.

A blank or whitespace-only title **clears** the override rather than storing an empty name, so the
UI falls back to `original_filename`.

⚠ **This renames the row, never the file.** `filename` is the on-disk key that `documents/`,
`extracted/`, `images/` and every chunk asset path are built from, and `original_filename` is what
`GET /raw` serves the download as. Renaming either to match a label would break both.

### `GET /papers/{paper_id}/cover`

The paper's first page as `image/jpeg`, ~480px wide, `Cache-Control: public, max-age=86400`.
Rendered with PyMuPDF on first request and cached at `storage/covers/<id>.jpg`.

| Status | When |
| --- | --- |
| `200` | The cover exists or was just rendered. |
| `204` | No source PDF, or the first page would not rasterise. |
| `404` | No such paper. |

⚠ **204, not 404, for a missing cover.** The library requests one per card; a wall of 404s makes a
working library look broken. ⚠ A browser reports 204 to `<img>` as a **load error**, so every
client needs an `onError` placeholder: see
[`PaperCover.tsx`](../../frontend/src/views/PaperCover.tsx).

⚠ The cache is keyed by document id alone and is never invalidated. A document's first page cannot
change: re-extraction and re-chunking rewrite derived text, never the source PDF.

### `DELETE /papers/{paper_id}`

`204 No Content`. Deletes the `documents` row (cascades to chunks,
embeddings, assets, summaries, ingestion jobs, figure descriptions, and
asset file paths on disk). Conversation turns survive with `document_id`
nullified.

### `POST /papers/{paper_id}/rechunk`

Re-runs the chunker on cached MinerU output. Wipes chunks/embeddings/assets,
re-inserts, re-queues embedding. `409` if no cached extraction.

### `POST /papers/{paper_id}/reextract`

Wipes cached extraction + DB side-effects and re-runs the full pipeline
(MinerU + chunker + embedding).

### `POST /papers/{paper_id}/regenerate-summaries`

Dispatches `generate_section_summaries`, which re-runs hierarchical section
summarization and VLM figure descriptions. Returns `202 Accepted`.

### `POST /papers/{paper_id}/reconstruct-reading-order`

Dispatches `reconstruct_reading_order`. Sends chunks + bounding boxes to
the LLM to fix reading order for two-column / complex layouts.

---

## Chunks

Source: [endpoints/chunks.py](../../backend/app/api/v1/endpoints/chunks.py).

### `GET /papers/{paper_id}/document`

**The article reader's only load.** Returns every block of the paper plus its heading spine, in
one response: chunks and their image assets are fetched in two queries and joined in memory.

⚠ Deliberately unpaginated. The reader renders the whole paper at once; the previous approach
walked `/chunks/after/{seq}` once per chunk, costing 105 sequential round-trips on a 14-page
paper before a single word appeared.

```json
{
  "paper_id": "<uuid>",
  "title": "<original filename without extension>",
  "doc_kind": "paper" | "book",
  "status": "<document status>",
  "page_count": <int|null>,
  "extractor": "mineru" | "pymupdf_fallback" | null,
  "blocks": [
    {
      "id": "<uuid>", "sequence_order": <int>,
      "structural_type": "text|heading|math|table|figure|code|footnote",
      "content_markdown": "...", "plain_text": "...",
      "heading_path": ["..."] | null,
      "page_start": <int|null>, "page_end": <int|null>,
      "table_json": {"headers": [...], "rows": [[...]]} | null,
      "image_url": "/static/images/<doc_id>/<file>" | null
    }
  ],
  "outline": [{"sequence_order": <int>, "text": "...", "level": <int>}],
  "total": <int>
}
```

⚠ `image_url` is populated for `figure` **and** `math` blocks: MinerU crops a bitmap of every
equation alongside its LaTeX, and the reader hands that crop to the model when a question is
anchored to a formula.

### `GET /papers/{paper_id}/chunks`

List all chunks in sequence order.

Query: `?limit=100&offset=0`.

Response:

```json
{
  "chunks": [<raw chunk dict>, ...],
  "paper_id": "<uuid>",
  "total": <int>
}
```

### `GET /papers/{paper_id}/chunks/{sequence_order}`

The reading view's primary endpoint. Returns one chunk shaped for the
client:

```json
{
  "id":               "<uuid>",
  "paper_id":         "<uuid>",
  "sequence_order":   1,
  "content_markdown": "## Introduction\n...",
  "structural_type":  "heading" | "text" | "math" | "table" | "figure" | "footnote",
  "plain_text":       "Introduction ...",
  "page_start":       <int|null>,
  "page_end":         <int|null>,
  "heading_path":     ["Section 1", "1.1 Setup"] | null,
  "image_url":        "/static/images/<doc_id>/<uuid>.png" | null,
  "image_refs":       ["<original_name>", ...]
}
```

`image_url` is populated only when there's a row in `chunk_assets` for
this chunk with `asset_type='image'`.

`404 ChunkNotFound` when there's no chunk at that sequence, which the
frontend uses as the "end of paper" signal.

---

## Figure Descriptions

### `GET /papers/{paper_id}/figure-descriptions`

Returns VLM-generated technical descriptions for every figure in the paper.

```json
{
  "descriptions": [{
    "chunk_id": "<uuid>",
    "description_markdown": "This figure shows ...",
    "image_path": "<doc_id>/<uuid>.png",
    "model": "gemma4:31b-cloud"
  }, ...]
}
```

---

## Notes

Source: [endpoints/notes.py](../../backend/app/api/v1/endpoints/notes.py). Behaviour:
[chat-and-ask.md](../02-architecture/chat-and-ask.md).

A **note** is one question anchored to a place in a paper, plus its answer, rendered as a card in
the reader's margin. Notes live in `paper_notes`, not in `conversation_turns`, because they are
a different artifact: anchored, one Q+A per row, and untouched by routing, compaction, or
sub-threads.

### `GET /papers/{paper_id}/notes`

Every note on the paper, **both scopes**, ordered by `anchor_sequence_id` then `created_at`,
the same order the margin lays them out in. The client splits them: `scope='anchor'` renders in the
gutter, `scope='document'` in the assistant panel.

⚠ A `scope='document'` note carries the first block's `anchor_sequence_id` because the column is
`NOT NULL`. Nothing positions by it; do not sort or place these by anchor.

⚠ `agent_steps` is `[]` for notes created before 2026-08-26, and for any note the model answered
without calling a tool. Both are correct, not missing data.

```json
{"notes": [
  {
    "id": "<uuid>",
    "scope": "anchor" | "document",
    "anchor_sequence_id": <int>, "anchor_chunk_id": "<uuid>|null",
    "anchor_kind": "text|figure|equation|table|block|document",
    "anchor_quote": "<the highlighted passage>|null",
    "anchor_image_path": "<doc_id>/<file>|null",
    "question": "...", "answer": "...",
    "cited_sequence_ids": [<int>],
    "agent_steps": [AgentStep],
    "retrieval_mode": "whole" | "agent" | null,
    "model": "<what the provider reported>|null",
    "requested_model": "<what the reader picked>|null",
    "margin_side": "left" | "right",
    "parent_note_id": "<uuid>|null",
    "created_at": "<iso8601>"
  }
]}
```

### `POST /papers/{paper_id}/notes/stream`

Create a note and stream its answer as Server-Sent Events.

```json
{
  "question": "why is tau so small here?",
  "anchor": {
    "kind": "text|figure|equation|table|block|document",
    "sequence_id": <int>,
    "chunk_id": "<uuid>|null",
    "quote": "<selected text, or a figure caption / equation LaTeX>|null",
    "image_url": "/static/images/...|null"
  },
  "parent_note_id": "<uuid>|null",
  "margin_side": "left" | "right" | null,
  "model": "<model name>|null"
}
```

Event types, one JSON object per `data:` line:

| Event | Payload | Meaning |
| --- | --- | --- |
| `created` | `note_id` | The row exists, render the card now. |
| `status` | `message` | The phase (`Reading the passage…`, `Writing the answer…`). |
| `step` | see below | One tool call. Arrives **twice** per call. |
| `token` | `text` | Answer text as it generates. |
| `done` | `note_id`, `answer`, `model`, `retrieval_mode`, `cited_sequence_ids`, `agent_steps` | Final state. |
| `error` | `detail` | Generation failed; the row survives with an empty answer. |

`AgentStep`:

```ts
{
  id: string,          // "s{round}-{index}": stable across the running/done pair
  n: number,           // which tool round, 1-based
  tool: "SECTION" | "SEARCH" | "READ" | "WEB",
  arg: string,         // what was asked for
  state: "running" | "done",
  think: string | null,   // the model's stated reason; first call of the round only
  label: string,          // "Read “4.2 Training mixture”"
  result: string,         // "2 blocks · ¶47–¶48": empty while running
  seqs: number[],         // block numbers pulled in
  sources: { title: string, url: string }[]   // WEB only
}
```

⚠ **Upsert by `id`, never append.** Every call is announced as `running` before it executes and
re-sent as `done` after. Appending renders each fetch as two rows, the first spinning forever.

⚠ **The observation is never sent.** The raw blocks the model reads, thousands of characters per
call, stay server-side and go only into the next prompt. `result` and `seqs` are the summary.

⚠ Three fields in the request are **advisory and can be overridden by the server**:

- `anchor.chunk_id` is always re-resolved from `sequence_id`. Re-chunking recreates every row with
  fresh UUIDs, so a tab opened before a re-chunk holds ids that no longer exist, and inserting one
  violates the foreign key and 500s the request.
- `model` is **ignored entirely when `parent_note_id` is set**. A follow-up always uses the
  parent's `requested_model`. A thread that switched models halfway would destroy the comparison
  the picker exists for.
- `margin_side` defaults to whichever margin is less crowded near the anchor; a follow-up inherits
  its parent's side. It is forced to `right` for `scope='document'`, which is never in a gutter.

⚠ **`scope` is not a request field.** The server derives it: `anchor.kind === 'document'` →
`scope='document'`, everything else → `'anchor'`. A follow-up inherits its parent's. Accepting both
would allow rows (`kind='document', scope='anchor'`) that no surface can place.

⚠ **A holistic question gets more tool rounds**, `PAPER_AGENT_HOLISTIC_MAX_STEPS` (6) rather than
`PAPER_AGENT_MAX_STEPS` (4), and whole-document stuffing is disabled for it regardless of
`PAPER_WHOLE_DOCUMENT_CONTEXT`.

### `PATCH /papers/{paper_id}/notes/{note_id}/margin`

Body `{"margin_side": "left" | "right"}` → `{"id", "margin_side"}`. 400 on any other value.

### `DELETE /papers/{paper_id}/notes/{note_id}`

`204`. Follow-ups cascade with the root.

---

## Personal reading state

Source: [endpoints/personal.py](../../backend/app/api/v1/endpoints/personal.py). Tables:
[database-schema.md](database-schema.md#personal-reading-state).

Everything in the reader that belongs to the person rather than the paper: **bookmarks**, the
reader's **own notes**, and **decks**. Distinct from `/notes` above, which is what the *model*
answered.

`[historical]` This lived in `localStorage` until 2026-07-28, which made it per-browser. The
client migrates any leftover local copy on first open and then erases it: see
[frontend.md](../02-architecture/frontend.md#personal-state-and-its-migration).

### `GET /papers/{paper_id}/personal`

All three collections in one request. **Fetch this, not the three below**: decks reference the
other two, so served separately a deck list can arrive describing a note a concurrent delete has
already removed, and the margin renders a stack with a hole in it.

```json
{
  "bookmarks": [{
    "id": "<uuid>", "sequence_id": <int>,
    "snippet": "<cached preview of the block>|null",
    "kind": "text|figure|equation|block",
    "page": <int>|null, "progress": <float 0..1>,
    "label": "<reader-supplied name>|null",
    "updated_at": "<iso8601>|null"
  }],
  "notes": [{
    "id": "<uuid>", "anchor_sequence_id": <int>, "anchor_chunk_id": "<uuid>|null",
    "anchor_quote": "<the highlighted passage>|null",
    "body": "<markdown>", "margin_side": "left|right",
    "created_at": "<iso8601>", "updated_at": "<iso8601>"
  }],
  "decks": [{
    "id": "<uuid>", "label": "<name>|null", "top": <int>,
    "margin_side": "left|right", "study": <bool>,
    "members": [{"kind": "ai|personal", "id": "<uuid>"}]
  }]
}
```

Serving this also **prunes decks left holding fewer than two cards** and commits that. Membership
rows cascade away with their notes, so a deck can be reduced from the outside at any time.

### Bookmarks

| Route | Behaviour |
| --- | --- |
| `GET /papers/{paper_id}/bookmarks` | `{"bookmarks": [...]}`, ordered by `sequence_id`. |
| `POST /papers/{paper_id}/bookmarks` | Body `{sequence_id, snippet?, kind?, page?, progress?, label?}` → the row. |
| `PATCH /papers/{paper_id}/bookmarks/{bookmark_id}` | Body `{"label": "<name>\|null"}` → the row. `404` if it belongs to another paper. |
| `DELETE /papers/{paper_id}/bookmarks/{bookmark_id}` | `204`. `404` if it belongs to another paper. |

⚠ `POST` is an **upsert keyed on the block**, not an insert. Marking an already-bookmarked block
updates that row and returns the same `id`; there is deliberately no way to put two marks on one
block. `label` is preserved when the body omits it, so re-marking from the article never wipes a
name set in the panel.

### Personal notes

| Route | Behaviour |
| --- | --- |
| `GET /papers/{paper_id}/personal-notes` | `{"notes": [...]}`, ordered by `anchor_sequence_id` then `created_at`. |
| `POST /papers/{paper_id}/personal-notes` | Body `{anchor_sequence_id, body, anchor_quote?, margin_side?}` → the row. |
| `PATCH /papers/{paper_id}/personal-notes/{note_id}` | Body `{body?, margin_side?}` → the row. Omitted fields are left alone. |
| `DELETE /papers/{paper_id}/personal-notes/{note_id}` | `204`, and prunes any deck this left below two cards. |

⚠ `anchor_chunk_id` is re-resolved from `anchor_sequence_id` server-side and is never accepted from
the client: same re-chunk hazard as `POST /papers/{paper_id}/notes/stream` above.

### Decks

| Route | Behaviour |
| --- | --- |
| `GET /papers/{paper_id}/decks` | `{"decks": [...]}`, oldest first, members in stacking order. |
| `PUT /papers/{paper_id}/decks` | Body `{"decks": [...]}`, replaces the paper's whole arrangement. Returns what was stored. |

⚠ **`PUT` is a whole-collection replace, and that is the contract**: there is no per-deck create,
update, or delete. One drag can dissolve a deck, create another, and move a card between two more;
that is a single arrangement, applied in one transaction. Expressed as granular calls it becomes an
ordered sequence with a half-applied state between every pair, and a request dropped in the middle
leaves a card in two decks or in none.

Deck `id`s are **supplied by the client** and preserved, so untouched decks keep their identity
across a write. They must be UUIDs.

The server reduces the submitted arrangement before storing it, and returns the reduced form:

| Submitted | Stored |
| --- | --- |
| A member that is not a note on this paper | Dropped. |
| A member listed in two decks | Kept by the first deck; dropped from the rest. |
| A deck left with fewer than two members | Dropped entirely. |
| `top` beyond the surviving member count | Clamped. |

---

## Studies

Source: [endpoints/studies.py](../../backend/app/api/v1/endpoints/studies.py). Behaviour:
[chat-and-ask.md § Part 1b](../02-architecture/chat-and-ask.md#part-1b--the-study-agent-the-desk).

A **study** is a named group of papers that scopes an answer. Its chat lives in
`conversation_turns`, the same table the book chat uses, because a desk conversation is a rolling
transcript with follow-ups, not a standalone Q+A.

⚠ **`library` is a valid `{study_id}`.** It means the library-wide scope: every finished paper, no
group. Every endpoint below accepts it; `study_id IS NULL` on the turn rows says the same thing in
the database. `PATCH` and `DELETE` do not, since there is no row to change.

### `GET /studies`

```json
{"studies": [{"id", "name", "description", "paper_count", "created_at", "updated_at"}]}
```

### `POST /studies`

Body `{"name": "…", "description": "…"|null}` → the study. `201`.

### `GET /studies/{study_id}`

The study and its papers, **in citation order**.

```json
{
  "study": {"id", "name", "description", "paper_count", ...},
  "papers": [{"id", "title", "page_count", "status", "paper": 1}]
}
```

`paper` is the `P<n>` the agent cites this paper by. For `library` the study block is synthetic
(`id: "library"`, no timestamps) and cannot be renamed.

### `PUT /studies/{study_id}/papers`

Body `{"document_ids": [...]}` → `{"papers": [...]}`. **Replaces** membership; list order sets
citation order.

⚠ Whole-collection, like decks. The P-numbers the reader is looking at come from this order, so a
partial update could repoint citations already on screen.

⚠ `400` past `STUDY_MAX_PAPERS` (24). Every paper is loaded on every question.

### `GET /studies/{study_id}/chat`

```json
{"turns": [{
  "id", "role": "user"|"assistant", "content", "model",
  "cited": [{"paper": 2, "document_id", "label", "sequence_id": 41}],
  "agent_steps": [AgentStep],
  "created_at"
}]}
```

### `POST /studies/{study_id}/chat/stream`

Body `{"question": "…", "model": null}`. SSE, **the same event shapes as the note stream**:
`created` (carrying `turn_id`), `status`, `step`, `token`, `done`, `error`, so one client
component renders both. `done` carries `turn_id`, `answer`, `model`, `cited`, `agent_steps`.

⚠ The user's turn is stored **before** generation, so a failed model call still leaves the question
in the transcript.

### `DELETE /studies/{study_id}/chat`

`204`. Clears the scope's transcript. The papers and the sticky notes stay.

---

## Sticky notes

Source: [endpoints/stickies.py](../../backend/app/api/v1/endpoints/stickies.py).

Two boards. `board=chat` is the strip beside one conversation, keyed by `scope`
(a study id or `library`); `board=universal` is the standalone board.

⚠ **`board` is not redundant with `scope`.** `scope=library` already means the
library-wide *chat*, so without the board a note beside that chat and a note on
the universal board would be the same row.

| Route | Behaviour |
| --- | --- |
| `GET /stickies?board=universal` | The standalone board, pinned first then newest. |
| `GET /stickies?board=chat&scope=<studyId\|library>` | That conversation's strip. |
| `POST /stickies` | Body `{body?, color?, pinned?, board, scope?, document_ids?}` → the note. `201`. |
| `PATCH /stickies/{id}` | Body `{body?, color?, pinned?, board?, scope?, document_ids?}`. Omitted fields are left alone. |
| `DELETE /stickies/{id}` | `204`. **Reader-only**, see below. |

```json
{"id", "body", "color": "yellow|blue|green|pink|orange|plain", "pinned": false,
 "board": "chat|universal", "scope": "<studyId>|library",
 "origin": "user|assistant", "author_model": "gemma4:31b-cloud"|null,
 "papers": [{"document_id", "label"}], "created_at", "updated_at"}
```

⚠ **Send `board` AND `scope` together to move a note.** `board` alone is not
enough: `scope=library` is a real destination, not "not supplied".

⚠ **`origin` cannot be set or patched.** `POST` forces `user`, so no client can
forge an assistant note; the assistant writes its own by calling the repository
from `study_agent`. And an edit never launders one: the badge records where the
claim came from, not who typed last.

⚠ **Only the reader deletes.** That is structural, not a check here: there is no
delete tool in the agent's parser, and `study_agent` does not import
`sticky_repo.delete_sticky`. This endpoint is what the UI's × calls.

⚠ `color` is a name, not a hex value. The UI maps it to CSS variables so a note
reads as paper in both themes; a stored hex is a glare in the dark one.

---

## Models

Source: [endpoints/models.py](../../backend/app/api/v1/endpoints/models.py),
[llm/catalog.py](../../backend/app/llm/catalog.py).

### `GET /models`

Models that can answer a note, local first and cloud-hosted last.

```json
{
  "models": [{"name": "gemma4:26b", "is_cloud": false, "size_bytes": 18000000000}],
  "default": "<the resolver's current chat model>"
}
```

Read from Ollama's `/api/tags`. A model is `is_cloud` when its name ends in `cloud` **or** it
reports `size_bytes: 0`, meaning a cloud entry carries no local weights. Embedding models are filtered
out; they appear in the same tag list but cannot hold a conversation. Falls back to the single
configured cloud model when Ollama is unreachable.

---

## Ask

Source: [endpoints/ask.py](../../backend/app/api/v1/endpoints/ask.py).

### `POST /papers/{paper_id}/ask`

Request body:

```json
{
  "query":                     "What does this figure show?",
  "current_sequence_order":    3,
  "conversation_id":           "<uuid>",
  "visible_sequence_orders":   [3, 4, 5],
  "focused_element":           "figure:7" | "table:3" | null,
  "images_b64":                ["<raw base64>", ...]
}
```

Server resolves `current_sequence_order` → `current_chunk_id`, then
delegates to `chat.orchestrator.handle_ask`.

Response:

```json
{
  "answer":              "...",
  "context_type":        "LOCAL" | "GLOBAL" | "OVERVIEW" | "EXTERNAL" | "OUT_OF_SCOPE",
  "router_reason":       "Query references visible content (matched: 'this figure')",
  "citations":           [Citation, ...],
  "model":               "gemma4:31b-cloud",
  "conversation_id":     "<uuid>",
  "research_performed":  true | false,
  "research_summary":    "Studied N sources across M iterations" | null
}
```

`Citation`:

```ts
{
  chunk_id?: UUID,
  sequence_id?: number,
  page?: number,
  text_snippet?: string,
  url?: string,
  source?: "document" | "<engine name>"
}
```

### `GET /papers/{paper_id}/chat?conversation_id=<uuid>`

Returns saved conversation turns (oldest first) for a paper, optionally
filtered to one conversation.

### `GET /papers/{paper_id}/conversations`

Returns every distinct conversation thread for a paper:

```json
{
  "conversations": [
    {
      "conversation_id": "<uuid>",
      "turn_count": 5,
      "started_at": "...",
      "last_at": "...",
      "first_user_message": "..."
    }, ...
  ]
}
```

---

## Search (debug endpoints)

Source: [endpoints/search.py](../../backend/app/api/v1/endpoints/search.py). Not
called by the standard UI but useful for testing retrieval directly.

### `GET /search/vector?q=...&document_id=<uuid>&limit=10`

Embeds the query and returns the top-K chunks by cosine similarity.
`document_id` is optional: when omitted, searches across all papers.

### `GET /search/web?q=...&limit=5`

Bypasses the chat router and hits the web search cascade directly, with the same
ranking that EXTERNAL would apply.

---

## Errors

[`api/errors.py`](../../backend/app/api/errors.py) registers two
domain exceptions:

| Exception          | HTTP | Body                                        |
| ------------------ | ---- | ------------------------------------------- |
| `DocumentNotFound` | 404  | `{ "detail": "Document <id> not found" }`   |
| `ChunkNotFound`    | 404  | `{ "detail": "No chunk at sequence_order=N" }` |

Body too large causes `413` with description. Internal failures return
`500` with traceback in `detail`.

---

## Lifecycle headers

CORS ([main.py](../../backend/app/main.py)) allows the dev origins:
`localhost:5173`, `localhost:3000`, `127.0.0.1:5173`. Methods and
headers are wide-open (`*`).