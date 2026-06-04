# Frontend

Vite + React + Tailwind, no router library — a tiny state machine in
[App.tsx](../frontend/src/App.tsx) toggles between four views.

## Top-level state ([App.tsx](../frontend/src/App.tsx))

```ts
type Route = 'library' | 'processing' | 'reading' | 'pdf-viewer';
```

Held in `useState<Route>`:

- **`library`** → `<LibraryView>`.
- **`processing`** → `<LibraryView>` underneath + `<ProcessingOverlay>` on top.
- **`reading`** → `<ReadingView>` (which mounts `<ChatPane>`).
- **`pdf-viewer`** → reserved for an in-browser PDF viewer.

`App.tsx` also owns:
- `activePaper` — the `Paper` currently open in `ReadingView`.
- `activePaperId` — the backend UUID.
- `uploadingFile` — UX data for the processing overlay.
- `pollRef` — the `setInterval` ref for status polling.

## The fetch client ([api.ts](../frontend/src/api.ts))

All calls go through `/api/v1` and are proxied by Vite to `http://localhost:8000`.

| Function                | Method/Path                                         |
| ----------------------- | --------------------------------------------------- |
| `listPapers()`          | `GET /papers` → `PaperMeta[]`                       |
| `uploadPaper(file)`     | `POST /papers/upload` (multipart)                   |
| `getPaperProgress(id)`  | `GET /papers/{id}/progress`                         |
| `getChunk(id, seq)`     | `GET /papers/{id}/chunks/{seq}` → `ChunkData`       |
| `askPaper(id, q, seq, conv)` | `POST /papers/{id}/ask` → `AskResponse`        |
| `checkHealth()`         | `GET /health`                                       |
| `getRawPdfUrl(id)`      | `/api/v1/papers/{id}/raw`                           |
| `getStaticPdfUrl(id)`   | `/static/assets/{id}.pdf`                           |

All functions throw on non-`2xx`.

## LibraryView ([views/LibraryView.tsx](../frontend/src/views/LibraryView.tsx))

Renders a paper grid/list. On mount, calls `listPapers()`. Features:
- Drag-and-drop and click-to-upload dropzone.
- Local search (substring match over title and authors).
- Local sort cycle: `recent → title → pages`.
- Two layouts: grid (cards) and list (rows).

## Upload + processing ([App.tsx](../frontend/src/App.tsx))

`handleFileUpload(file)`:

1. Sets `uploadingFile`, switches to `route='processing'`.
2. Calls `uploadPaper(file)`. Gets back `{id, status:'processing'}`.
3. Starts `setInterval` every 1000 ms polling `/progress`.
4. On `status === 'complete'`: switch to `route='reading'`.
5. On `status === 'failed'`: go back to `library`.
6. Clear interval on cancel/unmount.

## ReadingView ([views/ReadingView.tsx](../frontend/src/views/ReadingView.tsx))

Two-pane split: left is the reader, right is `<ChatPane>`.

### Reader pane

- Holds `chunks: ChunkData[]` — chunks revealed so far.
- On mount, fetches chunk #1, then chunk #2.
- "Reveal next" advances by 1 and fetches the next chunk.
- A 404 from `getChunk` sets `atEnd = true` (end-of-paper marker).
- After each append, scrolls to the newest chunk.

### Chunk renderer (`ApiChunkBlock`)

| Type      | Rendering                                                                 |
| --------- | ------------------------------------------------------------------------- |
| `heading` | Big serif `<h2>` using `plain_text`/`content_markdown`.                   |
| `figure`  | `<img src={image_url}>` + caption (`plain_text`).                         |
| `math`    | Monospace block with `content_markdown` (LaTeX via KaTeX).                |
| `table`   | Monospace `<pre>` with `content_markdown`.                                |
| default   | `<p class="prose-paper">{plain_text || content_markdown}</p>`.            |

Each chunk is dimmed (`opacity: 0.55`) unless it's the most recently
revealed one.

## ChatPane ([views/ChatPane.tsx](../frontend/src/views/ChatPane.tsx))

Local state:
- `messages: ChatMessage[]` — turn log.
- `input: string` — textarea value.
- `thinking: boolean` — while a request is in flight.
- `conversationId: string | null` — persisted across turns.

`send()`:

1. Optimistically appends the user turn.
2. Calls `askPaper(paperId, q, currentSequenceOrder, conversationId)`.
3. On success: stores the returned `conversation_id`, appends the assistant
   turn with citation chips (text snippet, source, or `§<sequence_id>`).
4. On failure: appends a polite error message.

Submit key bindings: **Enter** sends, **Shift+Enter** inserts a newline.

## Sub-threads

The chat pane supports nested sub-threads. A sub-threaded turn has a
`parentTurnId`. The main view renders threads indented, and sub-threads
show only the subtree of messages.

## Inline paper figures

When the model responds with `![caption](url)` markdown, a `SafeWebImage`
component renders it directly in the chat. This is used for inline paper
figures in LOCAL and GLOBAL responses.

## Other components

- [`components/Icons.tsx`](../frontend/src/components/Icons.tsx) — inline SVG icons.
- [`components/LogoMark.tsx`](../frontend/src/components/LogoMark.tsx) — the 9XAIPal wordmark.

## Styling

Tailwind utility classes with CSS variables (`--bg`, `--bg-2`, `--bg-3`,
`--fg`, `--muted`, `--accent`, `--ok`, `--border`) in
[src/index.css](../frontend/src/index.css). Dark, low-contrast canvas with
serif headlines and mono labels.