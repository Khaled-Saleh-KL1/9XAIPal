# Decisions & scar tissue

> **What this is:** approaches tried and abandoned, non-goals decided on purpose, and ugly things
> kept deliberately. One line each, newest last. Append; never rewrite an existing entry.
>
> **Why it exists:** none of this is recoverable from the code at any price. A rejected approach
> leaves no trace, so without a record it gets re-proposed every few months.
>
> **Format:** `DATE  Tried/Chose/Kept: <what>.  Because: <why>.`

---

2026-07-28  Chose: whole-collection `PUT /papers/{id}/decks` instead of per-deck CRUD.  Because: one drag can dissolve a deck, create another, and move a card between two more — granular calls turn that into an ordered sequence with a half-applied state between each, and a dropped request leaves a card in two decks or none.

2026-07-28  Chose: two nullable FKs (`ai_note_id`, `personal_note_id`) + a `CHECK`, over one polymorphic member id.  Because: it buys real cascade behaviour — deleting a note removes it from its deck, instead of leaving a dangling reference the client has to notice and skip.

2026-07-28  Tried: HTML5 drag-and-drop for stacking margin cards.  Failed: no touch support at all, and the app is meant to be opened from a tablet over the LAN. Replaced with pointer events, which cost a hand-rolled hit test via `elementFromPoint`.

2026-07-28  Tried: gating the localStorage→server migration on "server has none of this collection".  Failed: StrictMode's double mount ran it twice and imported every note in duplicate. Replaced with a shared in-flight promise plus `(anchor, body)` matching, which also makes a half-finished migration resumable.

2026-07-28  Kept ugly on purpose: the API image installs the full `requirements.txt`, MinerU and torch included, though only the Celery worker extracts PDFs.  Because: one requirements file is the repo's single source of truth, and splitting it into api/worker sets is a second thing to keep in sync. Known cost: a multi-GB API image. Do not grow it; split the requirements if it starts hurting.

2026-07-28  Chose: `requirements.txt` in both Dockerfiles, not `pyproject.toml` + `uv.lock`.  Because: both are gitignored at the repo root, so they can never exist in a clone — every `docker compose build` died at the `COPY`. uv still does the install, so the speed was never the point.

2026-08-18  Chose: give the paper agent the anchored passage plus the paper's contents index, never the paper.  Because: a note is a question about one passage, and a paper that merely fits in the window is not a reason to spend the window on it — the model drifts into summarising the document instead of reading the sentence. `PAPER_WHOLE_DOCUMENT_CONTEXT` keeps the old behaviour reachable, off by default. What makes it safe is the index, not the tools: a model that can see the document's shape can name the section it wants, where one with neither has only guesses at the paper's vocabulary.

2026-08-18  Kept ugly on purpose: the default note path answers from a NON-streamed probe call, so the answer lands whole instead of typing itself out.  Because: with a text tool protocol any reply may turn out to be a `<tool>` block, and streaming one into the margin would put "SEARCH: …" on screen. Detecting it mid-stream was considered and rejected — a model that writes a sentence of preamble before its tool block would have already leaked that sentence, and recovering means either resetting a card the reader is watching or discarding a real answer. Generation time is unchanged; only the reveal is, and `lib/pacer.ts` still paces it. Do not "fix" this without solving the preamble case.

2026-08-18  Chose: promote a drag-selection inside a table to a whole-table anchor, rather than quoting the cells that were crossed.  Because: dragging a table yields "8.4 12.1 91.2 7B" — values stripped of the header saying which metric and the row label saying which model. That quote is not merely worse, it is unanswerable in a way that looks answerable, and the model would confidently answer about numbers it cannot attribute. A table is one unit, like a figure: the MinerU crop goes to the model and the transcription rides along as fallible.
