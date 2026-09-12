# Area 3 — The paper / article reader (features 33–58)

> Part of the [feature catalogue](README.md). Companion architecture doc:
> [frontend.md](../02-architecture/frontend.md) (§ArticleReader onward).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

[`ReadingView.tsx`](../../frontend/src/views/ReadingView.tsx) fetches the document's metadata and
mounts [`ArticleReader.tsx`](../../frontend/src/views/ArticleReader.tsx) for `paper` and `article`
(it holds the frame for one round-trip rather than flashing the wrong reader). One
`GET /papers/{id}/document` call returns every block; reading is scrolling; asking is anchoring.

---

## 33. Whole-document article view

**What it does.** The paper as one continuous article — serif prose at 20 px / 1.72, headings by
depth, figures, math, tables — in the middle of a **three-column grid: margin · article · margin**.

**Where.** `ArticleReader.tsx` (layout, `layoutNotes`), `ArticleBlock.tsx` (feature 34),
`index.css` (`.article-*`).

**How it works.** Both margins are real grid columns whether or not they hold a card. Three tiers
by viewport width: `both` (≥ 1560 px), `right-only` (≥ 1180 px, left column present but empty so
centring holds), `inline` (below that, cards fall into normal flow under the article). Every block
carries `data-seq` and `data-chunk-id` — that is how a selection is traced back to a chunk and how
a card finds the element to sit beside.

**Why.** ⚠ **The article column never moves.** A note appearing cannot shift the text under the
reader's eye — the most disruptive thing a margin can do. The cost is empty space on a paper with
no notes, accepted.

---

## 34. Block renderer

**Where.** [`ArticleBlock.tsx`](../../frontend/src/views/ArticleBlock.tsx), memoised — without it
every keystroke in the composer would re-render the entire paper.

| Type | Rendering |
| --- | --- |
| `heading` | `article-h1/2/3` by the paper's own numbering depth (`services/outline.py::heading_level`), not `heading_path` depth (MinerU's `text_level` only distinguishes the title from everything else) |
| `figure` | Centred image + caption, hover "Ask about this figure" |
| `math` | Centred KaTeX, horizontally scrollable, hover ask; falls back to the page crop when KaTeX rejects the source (feature 35) |
| `table` | Real `<table>` from `table_json`, else markdown, in its own scroll box, hover ask |
| `code` | Fenced monospace block, with the page crop when one exists |
| `footnote` | Quiet side note with a rule |
| default | Serif prose |

**Tables get their own scroll box.** A paper's tables are the one part not the width of the
prose column. The old pair `overflow-x: auto` + `width: 100%` **never scrolled a single pixel**:
100 % told the table to fit, so it always fit, and it bought that fit by crushing `Params (B)` into
four stacked letters. Now `.article-table-scroll` (`overflow: auto`, `max-height: 70vh`), the table
`width: max-content; min-width: 100%`, a sticky header row, a styled scrollbar and edge shadows as
the cue there is more. Three landmines, all in `index.css`: body cells must stay transparent (the
edge shadows are painted on the scroller's background via the `local`/`scroll`
background-attachment pair, so an opaque `td` hides the cue); `table_json.headers` is routinely
empty because MinerU emits no `<thead>` — the renderer promotes `rows[0]` (fixed here, not in the
chunker, so already-ingested papers benefit); a stuck `th` loses its collapsed borders, so its
separators are drawn as `box-shadow: inset`, which travels with the cell.

The markdown inside every block goes through the shared pipeline
([`lib/markdown.ts`](../../frontend/src/lib/markdown.ts)): remark-gfm + remark-math →
**rehype-raw → rehype-sanitize (GitHub allowlist + the KaTeX classes) → rehype-katex**. Raw HTML is
needed because MinerU emits `<table>` HTML and models emit inline HTML, and raw HTML without
sanitisation is an XSS vector (a jailbroken answer or a hostile web snippet could inject
`<script>`/`onerror`). KaTeX runs *after* sanitisation so its spans are unaffected. Links open in
a new tab (react-markdown's default `<a>` has no `target`/`rel`).

---

## 35. Equation fallback to the page crop

**What it does.** A formula whose LaTeX KaTeX cannot parse shows MinerU's crop of the equation as
it appears on the page, instead of raw TeX source.

**Where.** `ArticleBlock.tsx::MathBlock`; the book reader's `GranularUnit` does the same.

**How it works.** `useLayoutEffect` checks the rendered output for a `.katex-error` span and, if
the block has an `image_url`, swaps in the image. It checks the *rendered* output rather than
probing `katex.renderToString` because the app's `katex` package and rehype-katex's private
dependency copy are different versions — a direct import would bundle a second full copy of the
library. `useLayoutEffect` runs before paint, so a formula that needs the fallback is never visible
as broken text first. KaTeX's default error colour (#cc0000) is replaced by the app's muted colour
for the no-image case.

**Why.** Garbled OCR of a piecewise `\begin{array}` is not something to auto-repair without risking
silently wrong math; the page crop is the real notation. This is the fix behind the
`equation-array-rendered-as-code.png` screenshot in `docs/issues/`.

---

## 36. Stepped reading (papers only)

**What it does.** `RevealModeToggle` in the header switches a paper from whole-article to
one-block-at-a-time, like the book reader. Questions asked in that mode are answered only from
what has been revealed.

**Where.** `ArticleReader.tsx` (`revealCursor`, `visibleBlocks`, `jumpTo`, `topmostBlock`),
[`lib/revealMode.ts`](../../frontend/src/lib/revealMode.ts),
[`components/RevealModeToggle.tsx`](../../frontend/src/components/RevealModeToggle.tsx),
`max_sequence_id` on `/notes/stream`.

**How it works.** One cursor, the last block handed over; `visibleBlocks` slices `doc.blocks` at
it. Preference and per-paper cursor live in `localStorage` — a reading style, not a property of
the paper. ⚠ **The cursor is stored, not recomputed from scroll**: deriving it from scroll position
was unreliable in the way a reader immediately notices — scroll to the end to look at a figure,
toggle on, and the whole paper counts as read. It changes only on Next or a jump, survives reload
and a round trip through Whole mode, never moves backward. First seeding uses evidence — the
furthest of any note anchor, bookmark, saved position, or the viewport — resolved against the
block list, not by numeric id (sequence ids have gaps; a stale mark from a re-chunk is discarded).
Three things break if the cursor is a simple filter and each is handled: `topmostBlock` searches
`visibleBlocks` (a binary search over an unrendered second half finds nothing and pins "where am
I" to block 0); `jumpTo` reveals before scrolling (scrolling to a block not in the DOM does
nothing — asking to go somewhere is consent to see it); margin cards with an unrevealed anchor are
not laid out. **Retrieval is clamped**: every question carries `max_sequence_id`, the paper agent
filters the chunk list it derives everything from (contents index, SECTION/READ/SEARCH, the anchor
window) and appends `READING_COMPANION_INSTRUCTIONS`, so the model neither reads ahead nor
apologises for material it was never given.

**Why.** Reader feedback split cleanly: some want segmentation for the same cognitive-load reason
the book reader exists; others find a keypress per paragraph intolerable. So a preference, with the
whole-article default unchanged. Papers only — a book already reads this way and an article is a
web snapshot.

---

## 37. Text-selection "Ask" pill → margin note

**What it does.** Drag-select inside a block; an "Ask" pill appears at the selection; the composer
opens in the margin with the quote; the answer streams into a card beside the passage.

**Where.** `ArticleReader.tsx::openComposerFromSelection`, `captureSelection`,
[`AskComposer.tsx`](../../frontend/src/views/AskComposer.tsx), `POST /papers/{id}/notes/stream`,
[`endpoints/notes.py`](../../backend/app/api/v1/endpoints/notes.py).

**How it works.** The selection is traced to a block via `data-seq`; the anchor is
`{kind:'text', sequence_id, chunk_id, quote}`. The composer never touches the network — the reader
owns the request so the resulting note can be placed and streamed into. The server picks the
**less crowded margin** for the new note (`_choose_margin`: count anchor-scope notes within a
window of sequence ids on each side; cards stack downward when they collide, so putting every note
on one side pushes later ones far from their paragraph). The stream carries `created` → `status`
→ `step` (tool calls) → `token` → `done` → `grounding`. Tokens go through the **pacer**
(feature 40) before display.

**Why.** ⚠ A selection inside a *table* does not produce a text anchor — it is promoted to the
whole table (feature 38): "8.4 12.1 91.2 7B" is unanswerable in a way that looks answerable.

---

## 38. Ask about a figure, equation, or table

**What it does.** Hover a figure / formula / table → an "Ask about this …" button; the composer
opens with the crop attached and, for equations and tables, the transcription as the quote.

**Where.** `ArticleBlock.tsx` (hover buttons), `notes.py::_to_storage_path`,
`llm/multimodal.py::build_multimodal_messages`, `test_note_asset_security.py`.

**How it works.** The anchor carries `image_url` (the served asset URL). The server strips it back
to a storage-relative path **only if it has this app's own `/api/v1/papers/{id}/assets/` shape for
this document** and no `..`/absolute components, then confirms the path names a `chunk_assets` row
of *this* document before the file is opened and base64-attached to the model call. The prompt for
these kinds tells the model to trust the attached crop over the transcription, which is why a
`PendingNote` keeps `imageUrl` — a retry without it would ask the model to trust an image that is
not there.

**Why.** None of the three can be drag-selected usefully: one is an image, one a tree of KaTeX
spans that selects into gibberish, and the third selects into cell values stripped of the header
and row label. `image_url` is attacker-controlled (a request body field), so two independent checks
stand between it and disk.

---

## 39. Ask about the block in view (`A`)

**What it does.** Press `A` with nothing selected → the composer anchors to the block at the top of
the viewport (`kind:'block'`). With a selection, `A` is the same as the pill.

**Where.** `ArticleReader.tsx` key handler, `topmostBlock` (a binary search over element
positions, run on every scroll frame — a scan meant one `getBoundingClientRect` per block per
frame, several hundred forced reflows on a long paper).

---

## 40. Margin notes (AI)

**What it does.** The card: eyebrow (grip, tone dot, "AI", `¶N`/`p. N`), the quote, the question,
the streamed answer with citation chips, the model tag, the agent trail, the evidence panel, a
follow-up box, a flip-margin control, collapse at 300 px, delete.

**Where.** [`NoteCard.tsx`](../../frontend/src/views/NoteCard.tsx),
[`NoteChrome.tsx`](../../frontend/src/views/NoteChrome.tsx), `paper_notes` table,
`PATCH /notes/{id}/margin`, `DELETE /notes/{id}`, [`lib/pacer.ts`](../../frontend/src/lib/pacer.ts).

**How it works.**
- **Follow-ups** chain beneath the root in one card (`parent_note_id`), inherit the root's model,
  and the agent is given the thread as context.
- **Streaming is paced.** Measured on a real answer: 77 token events, median 5 characters, 19 %
  a single character, gaps 79 ms median / 474 ms p90 / 751 ms worst. Rendering each event as it
  lands reproduces that cadence — a letter, a stall, a clump — and reads as broken. So arrival and
  display are decoupled: a buffer, an animation-frame loop, a 24-char reserve to ride out a stall
  (sized from the p90 gap), a 400 ms drain for anything above it, a 14 cps trickle when idle,
  a 260 cps ceiling so a burst does not flash past unread, 140 cps once the stream ends, and no
  more than one repaint per 55 ms (each repaint re-parses markdown and KaTeX).
- **Chips** `[[42]]` → `p. 8` (or `¶42` when the block has no page) and jump to the block.
  Markers with no digits (a model's invented `[[WEB]]`) are stripped as a backstop.
- **Layout**: `layoutNotes` is one top-to-bottom pass per margin, cards pushed down past each
  other, cursor never moving up — cheap, and the reason decks exist (feature 42).
- **Chrome**: cards are told apart by shape and colour, not a border tint, so a margin card is
  read peripherally. `Collapsible` clamps long answers; the trail and evidence panel sit *outside*
  the clamp so they stay reachable.

---

## 41. Personal notes

**What it does.** Your own note on a passage (`N` with a selection, or the pill's second action),
editable in place, in the same margin system as AI notes.

**Where.** [`PersonalNoteCard.tsx`](../../frontend/src/views/PersonalNoteCard.tsx),
[`lib/personalNotes.ts`](../../frontend/src/lib/personalNotes.ts), `personal_notes` table,
`/papers/{id}/personal-notes` CRUD.

**How it works.** Writes are optimistic with rollback **except creating a note, which waits for
the server**: a card rendered under a temporary id cannot be dragged into a deck (membership is a
foreign key). The composer keeps its draft until the save lands, so a failure loses nothing typed.

**Why.** Personal state used to live in `localStorage`; it moved server-side on 2026-07-28 with a
one-way migration built to be safe under repetition (feature 44).

---

## 42. Decks (flashcards)

**What it does.** Drag one card onto another and they collapse into a deck the height of one card,
with a pager; flip through them; **study mode** hides each answer behind Reveal and re-hides on
every flip — a deck of flashcards, not a folder.

**Where.** [`DeckCard.tsx`](../../frontend/src/views/DeckCard.tsx), `ArticleReader.tsx::stackDecks`
(pure), `pruneDecks`, `commitDecks`, `NoteChrome.tsx::useCardDrag`, `PUT /papers/{id}/decks`,
`note_decks` + `note_deck_members`, `repositories/personal.py::replace_decks`.

**How it works.** A deck parks at the **lowest sequence id among its members** so flipping never
makes it drift. `stackDecks` is pure because *its result is what gets written*: the whole
arrangement is `PUT` in one request, so it must be right on its own, not as a sequence of state
updates. Every drop (card→card, card→deck, deck→card, deck→deck) reduces to one sentence: whatever
was sitting still keeps its place, and the dragged thing joins it. Server side: members are cleared
for the whole document before any are inserted (the unique index that stops a card being in two
decks does not care that the colliding row is about to be deleted), a deck under two cards is not
a deck, and a supplied deck id may only update a deck **of this document** (a foreign id → 404 and
rollback — [docs/issues/003](../issues/003-deck-upsert-can-mutate-another-document.md)); members
that are not this paper's notes are dropped.

**The flip** is a card being turned over, not a crossfade: a two-phase Y rotation on one element,
content swapped at the midpoint where the card is ~86° to the viewer and unreadable. Mounting two
faces would double every card's state and leave the hidden one in the tab order. The stage height
is pinned for the turn and eased out, so a short card after a tall one cannot snap the margin
upward. ⚠ `FLIP_OUT_MS`/`FLIP_IN_MS` duplicate the `deck-turn-*` keyframe durations — change both.

**Why pointer events, not HTML5 drag-and-drop.** DnD gives a drag image and autoscroll for free
but **does not exist on touch**, and this app is meant to be opened from a tablet over the LAN.
The dragged card gets `pointer-events: none` mid-drag so `elementFromPoint` reports what is
underneath; targets are found via `[data-drag-id]`.

---

## 43. Bookmarks

**What it does.** Several per paper. Three surfaces, one state: a Bookmark chip in the bar that
toggles the mark on the block at the top of the viewport (or the selection, which is a more
specific intent) and a Resume chip pointing at the newest mark ("You're here" when on it); a tick
per bookmark on the progress rail, clickable; a ribbon in the margin of each bookmarked block,
which also removes it. Key: `B`.

**Where.** `ArticleReader.tsx`, `/papers/{id}/bookmarks` CRUD (`label` editable).

**Why.** The rail is a map, not just a fill; a single "resume" pointer hides every other mark you
made. A wash is easy to scroll past on a return visit; a silhouette is not.

---

## 44. Reading position memory (and the personal-state migration)

**What it does.** Reopen a paper or book where you left off.

**Where.** [`lib/readingPosition.ts`](../../frontend/src/lib/readingPosition.ts) (both readers),
[`lib/personalState.ts`](../../frontend/src/lib/personalState.ts).

**How it works.** `localStorage` under `pal:progress:<paperId>`, BookReadingView's original key
and shape, deliberately unchanged — every book on a reader's machine already has a position stored
in exactly that form and a tidier schema would silently reset them all. A chapter-less document
uses the `LINEAR = -1` slot the book reader always used for "no chapter". Every access is wrapped:
Safari private mode and "block site data" make `localStorage` throw rather than return null.

**Why local, not server.** Per-device by nature (where you are on the laptop is not where you are
on the phone) and written on every block boundary — not traffic worth sending. Notes and
bookmarks, which *do* follow you, live in Postgres. The migration of those (2026-07-28) is built to
be safe under repetition: concurrent loads share one in-flight promise (StrictMode mounts every
effect twice in dev; without this both mounts found an empty server and migrated twice), bookmarks
upsert by block, decks are a whole-collection replace, notes match on `(anchor, body)` before
insert, and `localStorage` is erased only after everything is stored — the worst outcome is "still
local, try again", never "some are gone".

---

## 45. Marginalia panel (`I`)

**What it does.** Contents, Bookmarks and Notes behind one search — structure, marks and
annotations are the same question asked three ways.

**Where.** [`MarginaliaPanel.tsx`](../../frontend/src/views/MarginaliaPanel.tsx),
`services/outline.py::heading_level`.

**How it works.** Contents nests by the paper's own numbering (`2.1.1.1` under `2.1.1`), with depth
as a CSS custom property (`--depth`) and one `calc()`, not a class per level — the old
`outline-l${min(level,4)}` silently flattened anything deeper. A 1 px guide rail per level,
`--accent` on the current section, is what makes a 300 px panel readable; the indent alone reads as
a ragged edge. Notes of the old `scope='document'` kind (from a retired whole-paper panel) still
appear here — they belong to the paper and dropping them would make them unreachable.

---

## 46. Quote highlights

**What it does.** After a reload, a note's quote is painted on the article again.

**Where.** [`lib/highlight.ts`](../../frontend/src/lib/highlight.ts).

**How it works.** The **CSS Custom Highlight API**, not `<mark>` wrapping. Wrapping means mutating
DOM React owns — the next re-render discards the marks — and node-splitting inside a KaTeX subtree
corrupts the equation. Highlight ranges live outside the DOM tree. Browsers without the API, or a
block whose quote no longer matches after a re-chunk, get a block-level tint via a class: coarse
but never wrong.

---

## 47. Clickable bibliography citations

**What it does.** A `[12]` (or `[5, 2, 35]`) in the body becomes a chip; open it and each number
is resolved against Semantic Scholar to a real paper — title, authors, year, a **PDF ↗** link when
open-access — with "Add to library" (queues ingestion in the background while you keep reading)
or "Open →" if it is already there.

**Where.** [`lib/references.tsx`](../../frontend/src/lib/references.tsx) (`remarkCitationRefs`),
[`BibCitationRef.tsx`](../../frontend/src/views/BibCitationRef.tsx),
`GET /references`, `/resolve`, `/resolve/stream`, `POST /add`,
[`search/semantic_scholar_client.py`](../../backend/app/search/semantic_scholar_client.py),
`services/references.py::title_candidates`. Design: [clickable-citations.md](../plans/clickable-citations.md),
[citation-queue.md](../plans/citation-queue.md).

**How it works.** A remark plugin walks text nodes for a `[5, 2, 35]`-shaped run and, **only when
every number is a known reference for this paper**, replaces it with a
`<span class="citation-ref" data-numbers>` marker — a real tag, because an unknown element would be
stripped by rehype-sanitize; a `span` component override swaps it for the chip and passes every
other span (KaTeX's included) through. Resolution is lazy, per chip opened. ⚠ Semantic Scholar's
`/paper/search/match` is a **title** matcher: handed the whole entry it 404s, so `title_candidates`
guesses the title (initials, `Proc.`, `et al.`, quoted IEEE titles, APA years handled) and each
guess is tried in turn. `resolved`/`no_match` are cached as final; `unavailable` is retried.
Adding calls `POST /add` directly and polls `getPaperProgress` inline — not App's full-screen
import overlay, which is right for "import what I'm about to read" and wrong for "queue this cited
paper while I keep reading". The PDF URL is read from the row, never trusted from the client.

**Why whole-bracket.** A bracket with one unrecognised number stays plain text — a half-clickable
control is worse than an inert one — and no heuristic beyond "is this number a real reference"
ever mistakes an index or footnote marker for a citation.

---

## 48. Citation lookup queue

**What it does.** Semantic Scholar allows the whole box one request per second. Simultaneous
readers are served in arrival order and told their place: *"In the queue — #3, the link will open
shortly…"*.

**Where.** [`core/pacer.py`](../../backend/app/core/pacer.py), `GET …/resolve/stream`,
`BibCitationRef.tsx` (queue state), `SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS` (1.05),
`SEMANTIC_SCHOLAR_MAX_ATTEMPTS` (4).

**How it works.** One Lua script in Redis: read the timestamp the next request may fire at, push
it forward one interval, hand the caller its slot; sleep the delta. Redis's `TIME` is the clock so
both API workers agree. A slot is reserved whether or not the caller lives to use it (a closed tab
costs one idle second; releasing slots would need a lock held across the sleep). Measured live,
a third to a half of correctly spaced requests still get 429 from Semantic Scholar, so a 429
**re-queues** (a fresh slot, fair to others) with backoff, up to 4 attempts, before it counts as
`unavailable`. The SSE stream sends `queued {position, wait_seconds}` before the sleep (and again
on a re-queue), then `resolved {entry}`. The link is a link the reader clicks, not a tab opened for
them — a `window.open` seconds after the click, from a stream callback, is exactly what popup
blockers stop.

---

## 49. Citation chips with page numbers

**What it does.** Every surface that quotes a document says which printed page: `p. 8` on margin
note chips, book chat chips, desk chips (`P2 · p. 8`), and the evidence panel.

**Where.** [`lib/pageMap.ts`](../../frontend/src/lib/pageMap.ts), `GET /papers/{id}/pages`,
`chunks.page_start`. Design: [page-numbers-in-citations.md](../plans/page-numbers-in-citations.md).

**How it works.** The chunker has always recorded `page_start` from MinerU's `page_idx`
(1-based); `page_start = page_end` on all 6132 rows in the corpus. What was missing was the
*display* — every surface threw the page away at the last step (ChatPane ignored the `page` field
and labelled chips with 200 chars of quote). `pageMap` resolves a block to its **own** `page_start`
or to nothing, and every caller falls back to `¶<sequence_id>`. ⚠ It deliberately does *not* reuse
`rawPosition()`'s approximation (nearest preceding block with a page, else an estimate): "p. 7" on
a chip is a claim about where words are printed, and a reader who turns to page 7 and does not
find them has been misled by the one feature whose purpose is letting them check. Articles have no
pages and always show `¶N`.

---

## 50. The agent trail

**What it does.** Above the answer, what the model fetched before it wrote: `SECTION 31`,
`SEARCH "sliding window"`, `READ 40–52`, `WEB …`, `THINK …` — each with its result count, live
while running, collapsed to one line ("How this was answered · 2 from the paper · 1 from the web")
once saved.

**Where.** [`AgentTrail.tsx`](../../frontend/src/views/AgentTrail.tsx), `step` SSE events,
`paper_notes.agent_steps` / `conversation_turns.agent_steps`. Protocol:
[chat-and-ask.md § The trail](../02-architecture/chat-and-ask.md#the-trail-every-fetch-is-reported-not-just-logged).

**How it works.** ⚠ Upsert by `step.id`, never append: each call arrives twice (`running`, then
`done`); appending renders every fetch as two rows, the first spinning forever. The trail stays up
while the answer types itself out — collapsing on the first token would snatch away the record of
the fetches at the exact moment they become checkable. A `WEB` step is coloured differently
(`--deck`, not `--accent`): whether an answer drew on anything outside the paper is the one
distinction worth seeing without reading. `THINK` executes nothing and costs no round; it is the
model's own reason for the round, shown above the fetches it triggered.

**Why.** "An agent that silently disappears for twenty seconds and comes back with an answer is
indistinguishable from one that hallucinated." Every fetch is reported, not just logged.

---

## 51. The evidence panel

**What it does.** Under every AI answer (notes, book chat, desk): "6 of 7 claims verified · 1 not
from the paper". Open it and each sentence is marked ✓ in the paper · ◐ partly · ⚠ not in the
cited passage · ○ not from the paper, with the passage **quoted inline** and a jump to it. It
flags; it never rewrites.

**Where.** [`EvidencePanel.tsx`](../../frontend/src/views/EvidencePanel.tsx),
[`chat/grounding.py`](../../backend/app/chat/grounding.py), the trailing `grounding` SSE event,
`paper_notes.grounding` / `conversation_turns.grounding`, `GROUNDING_CHECK`. Design:
[answer-evidence.md](../plans/answer-evidence.md).

**How it works.** After `done`, one judge call (temperature 0) receives the answer split into
claims — each with the refs cited *in that sentence*, parsed from whichever marker the surface uses
(`[[11]]`, `[seq:12]`, `[[P2:41]]`) — and an evidence pool: the cited blocks first, then everything
the agent's SECTION/READ steps fetched, under a 6 k-char budget. Verdicts must come back as exactly
one object per claim from the fixed vocabulary or the whole report is `unavailable` — a partial
array applied to the first n claims would look complete and not be. An uncited claim is checked
against the whole pool first (a sentence the model forgot to mark but that *is* in the paper comes
back `supported`); an admission ("not present in the retrieved sections") is `supported` with note
`admission` — the model is never penalised for doing the right thing (the first cut short-circuited
an empty pool and flagged a pure refusal as two unsupported claims). Headings and lead-ins are not
claims (the first live run reported "9 of 9 verified" with four rows nobody could check); LaTeX
underscores survive the markdown stripping (`d_{model}` was becoming `d{model}`). The judge names
the passage it judged against; `_locate` tolerates "32, 82" by taking the first key in the pool,
then the claim's own first citation.

**Why.** The app already told the model to ground every claim and say "not present" rather than
invent; what was missing was *checking* and *showing*. A plausible wrong citation is the
fabrication that matters most because it looks grounded; an uncited sentence rendered identically
to a cited one is the model's own inference made invisible. A silently corrected answer would be its
own kind of fabrication; a judge failure degrades to "Couldn't verify", never a false tick.

---

## 52. Strict-scope toggle

**What it does.** A header pill per document: whether this document's own chat may answer from
outside what the document says.

**Where.** [`components/StrictScopeToggle.tsx`](../../frontend/src/components/StrictScopeToggle.tsx)
(shared by both readers), `PATCH /papers/{id}/strict-scope`, `documents.strict_scope`.

**How it works.** `TRUE` (default): the assistant stays scoped — it will not silently reach for
general knowledge or the web because retrieval came up empty. It still can when the *reader's*
question calls for it: an explicit "search the web for…" or a comparison against something outside
the document, because that is the user asking, not the model wandering. `FALSE` restores fully open
behaviour for that one document permanently, rather than relying on catching that phrasing each
time. The toggle is fetch → optimistic flip → revert on failure.

**Why per document.** Not per user or global: the desk is unaffected either way — reaching across
every paper in a study is its purpose, not a leak to plug. Shared component rather than two copies
so "scoped" cannot quietly acquire two definitions.

---

## 53. Per-note model picker

**What it does.** The composer offers the models `/models` lists; the choice rides on the note
(and its follow-ups inherit it).

**Where.** `AskComposer.tsx` (`model-picker`), `GET /models`, `paper_notes.model`,
[ai-backend.md § 3b](../02-architecture/ai-backend.md).

**Why.** A quick factual note and a deep derivation deserve different models; making it per note
avoids a global setting the reader forgets to switch back.

---

## 54. Image lightbox

**What it does.** Click any content image — a figure, an equation crop, a VLM-rendered book figure,
an image inside an answer — to see it full-screen over a blurred page.

**Where.** [`components/ImageLightbox.tsx`](../../frontend/src/components/ImageLightbox.tsx).

**How it works.** A single delegated `click` listener on `document`, **opt-out** by an exclusion
list (the lightbox's own image, covers, thumbnails, controls). Five different components build
`<img>`s and several from raw markdown — there is no one place to hook, and delegation catches
images that don't exist yet.

---

## 55. Mermaid diagrams in answers

**What it does.** A ```` ```mermaid ```` fenced block in any AI answer is drawn as a diagram.

**Where.** [`components/MermaidDiagram.tsx`](../../frontend/src/components/MermaidDiagram.tsx),
wired in `lib/markdown.ts::MARKDOWN_COMPONENTS`.

**Why.** It lives in the shared pipeline because every AI surface renders through it — a model
that can draw in a margin note should draw in the desk too. Mermaid is ~1 MB and most answers have
no diagram, so it is imported lazily on first use.

---

## 56. Responsive tiers

**What it does.** Three layouts by width — both margins ≥ 1560 px, right-only ≥ 1180 px, inline
below; header controls overflow into a horizontal swipe on phones rather than a silent clip.

**Why.** ⚠ Layout regressions here have been WebKit-only twice (multi-column fragmentation of
notes on the desk wall, `inline-block` not enough in Safari). The doc's standing rule: check
Safari or drive WebKit headlessly and assert no element reports more than one client rect.

---

## 57. Keyboard shortcuts

| Key | Action |
| --- | --- |
| `A` | Ask about the selection, or the block at the top of the viewport |
| `N` | Personal note on the selection |
| `B` | Bookmark the selection or the topmost block |
| `I` | Toggle the Marginalia panel (contents) |
| `P` | Go to the desk scoped to this paper |
| `Esc` | Close the composer / panel |
| Book reader: `D`+`↓` | Reveal the next unit |

**Where.** `ArticleReader.tsx` key handler; `BookReadingView.tsx`.

---

## 58. The door to the desk (`P`, bottom-left button)

**What it does.** Navigates to the desk scoped to a study that contains this paper — **only if
there is exactly one** — else to the library scope with the studies rail right there.

**Where.** `ArticleReader.tsx::onOpenDesk`, `deskScopeRef`.

**Why.** It has been three things in three iterations: first an "Ask" and a "Note" button anchoring
to whatever block was at the top of the viewport (a worse version of highlighting, offered more
prominently); then one button opening a docked whole-paper panel; now a door. A question about the
paper as a whole, or several papers, is not something you do *on top of* a document you are
reading. Passage-level work belongs entirely to the pill and the `A`/`N` keys. Two or more
matching studies is ambiguous, and guessing is worse than landing one click away.
