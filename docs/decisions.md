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
