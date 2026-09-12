# Area 4 — The book reader (features 59–65)

> Part of the [feature catalogue](README.md). Companion:
> [frontend.md § BookReadingView / ChatPane](../02-architecture/frontend.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, c099d90).

[`BookReadingView.tsx`](../../frontend/src/views/BookReadingView.tsx) is the original reader,
preserved for `doc_kind='book'`: a chapter is revealed one small unit at a time, with
[`ChatPane`](../../frontend/src/views/ChatPane.tsx) beside it. Nothing on the paper path mounts
`ChatPane`.

---

## 59. Chapter-by-chapter reveal reader

**What it does.** A chapter list on the left; pick one and read it in revealed pieces; the last
chapter you were in auto-resumes; finishing a chapter offers "Continue into *next*".

**Where.** `BookReadingView.tsx` (`startReading`, `fetchAndAppend`), `GET /papers/{id}/chapters`,
[`services/book_outline.py`](../../backend/app/services/book_outline.py),
`lib/readingPosition.ts` (`seqByChapter`, `finishedChapters`).

**How it works.** Chapters come from the **PDF's own embedded `/Outlines` tree** — what a reader
app shows as its bookmarks sidebar: exact titles, exact pages, real nesting — mapped to sequence
ranges. Restoring a position uses one bulk `GET /chunks/range?after=&limit=` instead of one
`getNextChunk` round trip per chunk (a deep chapter used to cost hundreds of sequential HTTP calls
just to restore where you left off). Chunks are turned into **units** (`chunkToUnits`): a text
chunk becomes one unit per paragraph, with display math (`$$…$$`, `\[…\]`, `\begin{equation}`)
pulled out into its own centred KaTeX unit (mirrors the backend split so chunks ingested before
that fix still display correctly); tables, figures, code and footnotes are atomic units. A table
whose reconciled structure the chunker judged unreliable arrives with no `table_json` and is shown
as MinerU's page crop instead of a guessed grid.

**Why the outline, not the headings.** On a 322-page trade book the heading-derived list produced
18 "chapters", 6 of them figure captions MinerU mislabelled as headings and 3 mid-chapter
subsections, while missing 5 of the 8 real chapters because MinerU had put the real titles at two
different heading levels and the picker could only choose one. The embedded outline gave all 10
real chapters plus front/back matter, correctly. The publisher already told us the chapter list —
we were guessing instead. Heading-derived chapters remain the fallback for a PDF with no outline.

---

## 60. One-unit-at-a-time reveal

**What it does.** Each press reveals exactly one paragraph / table / figure / equation. A
restored session shows everything already read; a new one starts with one unit.

**Where.** `revealNextUnit`, `revealedUnits` / `pendingUnits` state, the reveal bar.

**How it works.** A fetched chunk's units go into `pendingUnits`; each reveal moves one to
`revealedUnits`; when pending is empty the next chunk is fetched (`GET /chunks/after/{seq}` — the
gap-tolerant endpoint, so a hole in sequence ids never ends a chapter early) and again only its
first unit is shown. The chapter end (`sequence_order > chapter.end_sequence`) or the document end
(404) sets `atEnd` and marks the chapter finished.

**Why.** Before the 2026-09-10 audit, `startReading` loaded two chunks and revealed *all* their
units, and `fetchAndAppend` appended a whole chunk at once — the "reveal" was per chunk, not per
paragraph, so a reader saw paragraphs they had not asked for
([docs/issues/014](../issues/014-book-paragraph-reveal-shows-content-in-advance.md)). The
`D`+`↓` chord (not plain `↓`) is so scrolling the page never accidentally reveals.

---

## 61. AI-corrected reading order toggle

**What it does.** When a paper's reading order has been reconstructed (feature 17), a checkbox
"Use AI-corrected reading order (N items)" makes the reader walk that order instead of the physical
sequence.

**Where.** `useLogicalOrder`, `readingOrder` (from `documents.reading_order`), `startReading` /
`fetchAndAppend`.

**How it works.** In logical mode the reader fetches by exact sequence id (`GET /chunks/{seq}`)
following `reading_order` filtered to the active chapter's range, never the physical `after/`
cursor. Toggling reloads from the first unit of the chosen order (or restores to the saved sequence
if it is in the order). The "Reconstruct" button dispatches the task and polls until
`reading_order` appears.

**Why.** The toggle existed for months as a no-op — the reveal loop always walked document order
and a TODO said so ([docs/issues/015](../issues/015-ai-reading-order-toggle-is-a-no-op.md)).
Verified in a real DOM: order `[3, 1, 2]` is followed and no physical `after/` call is made.

---

## 62. Error recovery in the reader

**What it does.** A failed chunk fetch shows the error and a **Retry** button; it never pretends
the book ended.

**Where.** `loadError` state, the retry control under the units.

**Why.** Every catch in the fetch path used to set `atEnd = true`, so a transient 500 or a dropped
connection permanently became "end of content" until the page was reloaded
([docs/issues/016](../issues/016-book-fetch-errors-are-reported-as-end-of-content.md)).

---

## 63. The side chat pane

**What it does.** A conversation about the book beside the text: streamed answers with citation
chips (`p. N`), a list of this book's past chats ("Chats · 3", "+ New chat"), image attachments
(📎 or paste — sent as base64 to a vision model), Enter to send / Shift+Enter for a newline, and
sub-threads.

**Where.** [`ChatPane.tsx`](../../frontend/src/views/ChatPane.tsx), `POST /papers/{id}/ask/stream`,
`GET /papers/{id}/chat`, `GET /papers/{id}/conversations`, `lib/markdown.ts`.

**How it works.** The pane keeps `messages`, the `conversationId` (minted by the server on the
first turn and passed back on every later one), `thinking`, `attachments`. Answers stream as
`step`/`token`/`done`/`grounding` events, so the pane shows the trail (feature 50) and the evidence
panel (feature 51) like the margin notes do. Model math is normalised before rendering
(`\(…\)` → `$…$`, orphan `\begin{env}` wrapped, stray `$$` removed) and a trailing
"Sources: None" line a stubborn model emits is stripped. Inline `![caption](url)` figures render
through `AnswerImage` (feature 72).

**Why the paper agent answers books now.** The streaming path sends a book question to the same
paper agent that answers margin notes (`orchestrator.py::_stream_book_agent`), not to the router +
pgvector path. The routed path committed to one retrieval mode — usually GLOBAL, a single
similarity pass — and on a 322-page book reliably answered "the specific names are not listed in
the provided excerpts" to questions the book answers on one page, because similarity search
returns passages *about* a topic, not the passage that enumerates it. The agent asks for the
section it needs and reads it. Same tools, same citation format — the two readers behave alike.
A book gets **different prompts**, not the paper prompt with "book" swapped in: a book reader
wants a conversation, not a citation-heavy margin annotation.

---

## 64. The progress ceiling

**What it does.** A question asked mid-book is answered only from what has been revealed. The
model does not spoil the ending, and does not apologise for material it was never given.

**Where.** `maxSequenceId` prop → `max_sequence_id` on `/ask/stream`;
`paper_agent.answer_paper_question` (filters the chunk list at the source),
`agent_tools.read_range` (clamps `READ`), `overview_context` (withholds unfinished sections),
`prompts.py::READING_COMPANION_INSTRUCTIONS`.
Detail: [chat-and-ask.md § The progress ceiling](../02-architecture/chat-and-ask.md#the-progress-ceiling).

**How it works.** Applied once, at the source: everything reachable — the CONTENTS index shown to
the model, what `SECTION` resolves to, the anchor window, `SEARCH` (separately, because it queries
the DB) — is derived from the filtered list. Two leaks were found and closed on 2026-09-08:
`READ` took its range from numbers the *model* wrote (`READ 1-400` on a 3663-block book with a
ceiling of 20 returned 20 blocks past it), now clamped before the query; and OVERVIEW handed every
part-way reader the whole-document summary (level-0 rows have NULL bounds, so the guard was never
true) and a section's whole summary the moment the reader was inside it — both now withheld, not
merely labelled: a label does not unsay an ending.

**Why.** The book is read in order on purpose. The same ceiling is what stepped paper reading
(feature 36) sends.

---

## 65. Jump-to-sequence from the desk

**What it does.** A desk citation `[[P2:41]]` on a book, or the PDF viewer's "Read structured",
opens the book reader at that block — the right chapter selected, the position restored to it.

**Where.** `jumpToSequence` / `onJumped` props, `handledJumpRef`, `App.tsx::openPaperById`,
`lib/pageMap.ts::pageToSequence`.

**How it works.** The reader loads the chapter list itself rather than waiting on the auto-resume
effect (which deliberately skips while a jump is pending — firing both would race two
`startReading` calls), finds the chapter containing the sequence, saves it as the position, and
starts there. `handledJumpRef` stops the effect re-firing for the same jump when its own
`setChapters` changes a dependency. A navigation generation counter in `App.tsx` stops an
in-flight open from landing on top of a paper clicked afterwards.
