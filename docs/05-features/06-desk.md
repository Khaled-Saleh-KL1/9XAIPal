# Area 6 — The desk: studies, cross-paper chat, notes (features 76–85, 110)

> Part of the [feature catalogue](README.md). Companions:
> [frontend.md § The desk](../02-architecture/frontend.md),
> [chat-and-ask.md § Part 1b](../02-architecture/chat-and-ask.md).
>
> **Reflects code as of:** 2026-09-12 (`main`, cb67f64 + done-reading).

The desk (`#/desk`) is **a place to work on papers without opening them** — a page, not a panel,
because an overlay implies the document underneath is the subject. Three columns, each a question:
*what am I working on?* (the studies rail) · *what do they say?* (the chat) · *what do I think?*
(the notes board). The chat column is the only elastic one; below 1180 px the board folds first,
because notes are a companion and the chat is what stops being usable when squeezed.

---

## 76. Studies

**What it does.** A named group of papers that scopes an answer. A paper can sit in several
studies; removing it from one takes nothing away from the library or the others. The **whole
library** is a scope too.

**Where.** `studies`, `study_papers` (`position`), [`endpoints/studies.py`](../../backend/app/api/v1/endpoints/studies.py)
(`GET/POST /studies`, `PATCH`, `DELETE`, `PUT /studies/{id}/papers`), `DeskView.tsx` rail,
`STUDY_MAX_PAPERS` (24).

**How it works.** `study_id IS NULL` on a turn is the **library-wide** scope — every finished paper
— and that is a real scope, not a missing value; code that "repairs" it deletes the reader's main
conversation. Its route segment is the literal `library`. The library scope's paper order is the
library's own (newest first); a study's is `study_papers.position`. The cap on papers per study
exists because every paper is a full chunk load per question — a guard against "a study of the
entire library".

---

## 77. The paper picker

**What it does.** The dialog that assembles a study: what is in it, in order, re-orderable; and
what else the library holds, with covers.

**Where.** [`PaperPicker.tsx`](../../frontend/src/views/PaperPicker.tsx), `PUT /studies/{id}/papers`.

**Why order is not decoration.** Answers cite `[[P2:41]]` and the number comes from this list's
order, so the dialog shows the numbering explicitly. The flat checkbox list it replaced hid both
facts — you could not see the numbering and could not tell chosen from unchosen without reading
every box. Membership is held locally and written on Save: a whole-collection write, so a live
request per checkbox would be a round trip per click *and* would renumber the study under the
reader mid-edit. Covers, not titles alone, for the same reason the library grew thumbnails — a
study is assembled by recognising papers, half of them still called `2607.24653v2`.

---

## 78. The study agent

**What it does.** Answers across every paper in the scope, deciding first *which* papers the
question turns on, then reading them.

**Where.** [`chat/study_agent.py`](../../backend/app/chat/study_agent.py),
`POST /studies/{scope}/chat/stream`, `STUDY_AGENT_MAX_STEPS` (8), `STUDY_HISTORY_TURNS`.

**How it works.** The model is handed **every paper's heading spine and nothing else** — the
study index:

```text
P1 (BDH-CQ): In-Context Learning with Recurrent Latent Reasoning (17 pages)
   [[P1:6]] Abstract
   [[P1:28]] 3 Introducing BDH-CQ
P2 (Kimi K3): Open Frontier Intelligence (47 pages)
   [[P2:29]] 2 Model Architecture
```

Ten papers is easily a million tokens of body; the spine of all ten is a few thousand — the entire
reason a cross-paper agent is affordable. Tools are paper-qualified: `SECTION: P2:31`,
`READ: P1:40-52`, `SEARCH` (every paper at once, hits labelled by paper), `WEB`, `IMAGE`, `THINK`,
and two writes — `NOTE:` (this chat's board) and `NOTE ALL:` (the universal board). `_plan` runs
`NOTE` last so the trail reads "looked, then wrote". Eight rounds rather than four because the
first round or two are spent working out which papers matter. **Repeated calls are
short-circuited**: observed, a broad question made the model re-request the same three sections on
four consecutive rounds (twelve identical fetches — a fresh copy of the same text reads to it as
confirmation rather than repetition); a signature set per question now answers a repeat with "you
already fetched this: ask for something else, or answer with what you have". `NOTE` is exempt —
duplicate notes are caught by de-dup against the board instead.

**Why the P-numbers are load-bearing.** A block number means nothing once there is more than one
paper, so every reference carries `P<n>:` — from `study_papers.position`, which is why membership
is written as one ordered collection. `prefix` in every formatter (`""` or `"P2:"`) is what keeps
the paper agent's and the study agent's citation schemes from drifting.

---

## 79. Cross-paper citations that open where they sit

**What it does.** `[[P2:41]]` in an answer is a chip; click it and the cited block expands
**inline** (fetched once on first expand and kept); "open in reader" is the optional second exit.
A citation into a paper the study no longer holds renders struck-through.

**Where.** [`CitationRef.tsx`](../../frontend/src/views/CitationRef.tsx),
`StudyChat.tsx::withCitationLinks`.

**How it works.** The markers become links *before* markdown runs, not fragments around it —
splitting the answer on markers made every fragment its own block, breaking a paragraph at a
mid-sentence citation and stranding a lone full stop on its own line; a `components.a` override
swaps the link for the chip with the paragraph intact. The peek is built from `<span
style="display:block">`s because it renders inside a `<p>`, where a `<div>` is invalid HTML React
will not nest cleanly. A dead control that looks like evidence is worse than one that says it is
gone — hence strike-through, not a button.

---

## 80. Streaming transcript with the trail

**What it does.** Steps and tokens live; the trail above each answer; the evidence panel below;
the composer with a model picker; **autoscroll only when already at the bottom** (within 120 px).

**Where.** [`StudyChat.tsx`](../../frontend/src/views/StudyChat.tsx), `DeskView.tsx`.

**Why.** A long answer streaming in while the reader is looking at an earlier turn must not drag
the view away from what they are reading.

---

## 81. Sticky notes: the chat board

**What it does.** A strip of notes beside one conversation (`board='chat'`, keyed by scope);
collapsible to a 38 px rail that still shows the count.

**Where.** [`StickyBoard.tsx`](../../frontend/src/views/StickyBoard.tsx),
[`StickyNote.tsx`](../../frontend/src/views/StickyNote.tsx), `sticky_notes` table,
[`endpoints/stickies.py`](../../backend/app/api/v1/endpoints/stickies.py).

**Why.** `board` is not redundant with `scope`: `scope='library'` already means the library-wide
*chat*, so without the board a note beside that chat and a note on the universal board would be
the same row. Collapsed is not the same as empty — the rail shows the count — and the state is in
`localStorage`: a reader who hid it wants it hidden tomorrow.

---

## 82. Sticky notes: the universal wall

**What it does.** `#/desk/notes`: a corkboard of notes from every conversation and none — tacked,
tilted, with a filter box.

**Where.** [`NoteWall.tsx`](../../frontend/src/views/NoteWall.tsx).

**How it works.** The tilt is derived from the note's id, never random — a `Math.random()`
rotation re-rolls on every render, and every keystroke in the filter would make the wall twitch.
The wall is a CSS **grid, not multi-column**: `columns: 260px` *fragments* its children — a note
taller than the remaining column height is split and the remainder redrawn at the top of the next
column, pin and dashed border and all; `display: inline-block` is the usual charm and it is not
enough in WebKit (the reported symptom: two orphaned note-bottoms with their own pins stranded
under the board). Grid items never fragment in any engine; the cost is ragged rows, which for a
corkboard is no loss. The clipped-note fade comes from a measurement (`scrollHeight` vs
`clientHeight`, re-measured by a `ResizeObserver` because a note that fits at first paint can
overflow once the serif face swaps in), not from a `:has(> :nth-child(3))` selector that was wrong
for the common case of one long paragraph.

---

## 83. Agent-written notes, and who wrote them

**What it does.** The agent can pin notes to either board (`NOTE:` / `NOTE ALL:` in a tool block,
or `<note>…</note>` / `<note board="all">` in its final answer). Every note carries `origin`; an
assistant note is marked twice over — a dashed border (the one border treatment nothing else uses,
legible before anything is read) and a badge naming the model. **Only the reader can delete.**

**Where.** `study_agent.py::_pin_written_notes`, `agent_tools.py::extract_notes`, `stickies.py`.

**How it works.** The agent reads both boards too — every pinned note rides in the prompt labelled
"(you wrote)" / "(the reader wrote)"; the label is load-bearing, because without it the model
re-pins its own notes every few turns and the reader ends up with the same observation five times.
The `<note>` tag exists because the model *invented* it: asked to pin a note on the forced final
turn (which is told it has no tools), it improvised the tag on the first try; parsing it is meeting
the model where it is. `NOTE ALL:` is matched before `NOTE:`, and the plain pattern refuses to match
it, or a universal note lands on the chat board with a body beginning "ALL:". Both exits from the
loop extract notes (the model can answer on the first probe without a tool, and that path never
touched the streamed branch — the first failure). Notes de-dup by body against the destination
board on collapsed whitespace; two per answer, hard.

**Why deletion is structural, not a rule.** There is no delete tool in the parser or the plan;
`study_agent` does not import `delete_sticky`; `POST /stickies` forces `origin='user'` so no client
can forge an assistant note either; `origin` is not patchable, so an edit cannot launder a note into
looking like the reader's own. Delete is armed rather than instant: a note can be a week-old
thought, and the assistant cannot recreate one it was never asked to write again.

---

## 84. Clear chat, rename, delete study

**What it does.** `DELETE /studies/{id}/chat` clears the transcript (the notes stay);
`PATCH /studies/{id}` renames; `DELETE /studies/{id}` removes the study and its chat, never the
papers. The synthetic library study has no id and cannot be renamed.

**Where.** `endpoints/studies.py`, `DeskView.tsx` (in-app confirm on destructive actions).

---

## 85. Deep links

**What it does.** `#/desk` (the library scope), `#/desk/<studyId>`, `#/desk/notes` (the wall);
`#/paper/<id>`, `#/raw/<id>`. Back/forward work; a hard refresh lands where you were.

**Where.** `App.tsx::parseHash`, `HashState`.

**Why.** No router library: a tiny state machine over the hash. `notes` is not a valid study id, so
the two desk pages cannot collide. Real back-button history arrived with the lightbox
(2026-09-02) so closing an overlay with Back does not leave the page.

---

## 110. Shelved paper lists: the rail and the picker as a file tree

**What it does.** The Desk's left rail ("Every paper" / "In this study") and the picker's "Your
library" column no longer list papers flat. They are grouped into collapsible shelves — **Books,
Research, Articles, Done** — and the Done shelf shows the reader's folders (feature 109) nested
one level in, each collapsible too, so a long library folds down to what is in play: open
Research, pick, collapse it, open Books. Every shelf and folder in the picker has its own **Add
all**. Done papers stay fully available to the Desk — finishing a book is not leaving it out of a
study.

**Where.** [`components/ShelfGroups.tsx`](../../frontend/src/components/ShelfGroups.tsx),
[`lib/shelves.ts`](../../frontend/src/lib/shelves.ts) (`groupByShelf`, `shelfOf`, `SHELVES`),
[`DeskView.tsx`](../../frontend/src/views/DeskView.tsx) (`railItems`),
[`PaperPicker.tsx`](../../frontend/src/views/PaperPicker.tsx).

**How it works.** One grouping function for all three surfaces (the library's kind chips, the
rail, the picker) so "Done" cannot mean something slightly different in each: `shelfOf` is
`done` when `done_at` is set, else the kind (older rows with no `doc_kind` are papers by schema
default); `groupByShelf` returns the four shelves in fixed order, empty ones dropped, input order
preserved inside each so a caller's sort survives, folder names alphabetical. The study endpoint
returns papers as `(id, title, P-number)` only, so the rail joins each against the library list
it already loads to learn the shelf. **P-numbers are unchanged** — they are the study's citation
order (feature 77), not the display order — which is why grouping is purely visual. Open/closed
state is per mount and per tree: the rail and the picker are different trees, and remembering a
collapse from one in the other would be surprising. Everything starts open: the ask was to be
able to *minimise* what is not in play, not to hunt for what is.
