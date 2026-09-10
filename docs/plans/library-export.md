# Exporting your library and notes

> **What this is:** the change that let a researcher get their highlights, notes,
> and library metadata back out of the app — into a bibliography, into Obsidian,
> into Anki, into a spreadsheet.
>
> **How to read it:** §1 the four formats and why each is shaped the way it is →
> §2 where BibTeX's author/year comes from, given the app never stored it → §3
> what was verified and how → §4 what this deliberately does not do.
>
> **Companions (detail):**
> [api.md](../03-reference/api.md) (`## Export`): the four endpoints ·
> [database-schema.md](../03-reference/database-schema.md) (`documents`): the
> three new columns · [clickable-citations.md](clickable-citations.md): the
> Semantic Scholar client this reuses, unchanged.
>
> **Status:** shipped · **Reflects code as of:** 2026-09-10

---

## 1. Four formats, library-wide or selected

All four formats can export the WHOLE library or an explicit set of selected
papers. The browser sends the selected document ids to one authenticated POST
endpoint. Formatting remains synchronous because a personal research library
(dozens of papers, not thousands) is fast enough to build in one response; the
frontend shows a waiting/progress screen while it runs.

- **BibTeX** (`services/export.py::to_bibtex`) — one entry per paper. `@article`
  when Semantic Scholar resolved real authors, `@misc` with an honest `note`
  field when it didn't, rather than a blank author field a reader might mistake
  for a gap in THEIR bibliography instead of an unresolved import. Every field
  passes through `_escape_bibtex` (`{`, `}`, `&`, `%`, `$`, `#`, backslash —
  backslash escaped first, or escaping any of the others would double-escape
  the backslash it just inserted) — an untested title with a real `%` or `&`
  in it (verified against one) would otherwise produce a `.bib` file that
  doesn't parse. Cite keys collide-proofed with an `a`/`b`/`c` suffix, not
  silently overwritten.
- **Markdown** (`to_markdown_zip`) — one `.md` file per paper in a ZIP, not one
  giant file and not one file per note. Obsidian (and anything that indexes a
  folder) treats each file as its own linkable unit; a paper with thirty notes
  as thirty tiny files would be worse to navigate than the same thirty notes as
  sections of one file named after the paper.
- **Anki** (`to_anki_tsv`) — plain `question\tanswer` lines, Anki's own "Import
  File" reads this with zero setup. From `paper_notes` (the AI Q&A) only:
  `personal_notes` are free text, not a front/back pair, and are not silently
  reshaped into one. A literal tab or newline inside a field is escaped (tab →
  space, newline → `<br>`, Anki's own convention) so a multi-paragraph answer
  can't split into a third column or end the row early.
- **CSV** (`to_library_csv`) — the library index, one row per paper: title,
  resolved authors/year, doc_kind, status, date added, page count. Built with
  the stdlib `csv` module, never hand-joined strings — a title with a comma or
  a quote in it (common: an arXiv filename, a colon-subtitled paper) would
  silently misalign columns on exactly the row worth reading.

## 2. Where BibTeX's authors/year come from

`documents` stores no author/year at all — only `title` (nullable, a reader
override; the fallback is `original_filename`, which is "often an arXiv id" per
its own column comment). Rather than a new lookup, export reuses
`search/semantic_scholar_client.py::match_reference` exactly as the citation
feature calls it, just queried with the paper's OWN title instead of a
bibliography entry's raw text — same function, same `ReferenceMatch |
Unresolved` return, same graceful degradation.

Three new columns on `documents` (`resolved_authors`, `resolved_year`,
`self_resolve_status`) mirror `paper_references.resolve_status`'s exact
semantics: `resolved`/`no_match` are final (a completed lookup), `unavailable`
(no key, rate-limited, network error — the real state on this box today, no
key configured yet) is retried on every export rather than cached as a dead
end. **Caught getting this backwards once already**, in this exact feature: the
first version of `_resolve_pending` skipped resolution whenever
`self_resolve_status != 'pending'`, which — after the FIRST export set every
paper to `'unavailable'` — meant every export after that silently never
retried anything, forever. Fixed to skip only the two final states, matching
the citation endpoint's own (already-correct) check.

## 3. What was verified, and how

Against a throwaway Postgres/Redis stack and the real container image, logged
in as a real user — the live library (11 documents) was untouched throughout.

- All four pure formatters directly, against realistic data including the
  named edge cases: a title with `{`, `}`, `&`, `%`, `$` (BibTeX escaping); two
  papers colliding on the same cite key (the `a`/`b` suffix); a note with an
  embedded literal tab and a CRLF (Anki TSV); a title with a comma (CSV);
  two similarly-titled papers producing distinct, non-colliding Markdown
  filenames; a paper with no notes at all in the Markdown export.
- All four endpoints over real HTTP: correct `Content-Disposition`/media type,
  real self-resolution degrading to `unavailable` (no key configured — the
  actual state on this box) without failing the export, and — critically — a
  **second** export call actually retrying resolution rather than silently
  giving up forever (§2's bug, caught and fixed before shipping).
- Ownership scoping on all four: a second user's export came back completely
  empty (empty BibTeX, header-only CSV, empty Anki file, a zero-file ZIP) —
  never a peek at the first user's papers or notes.
- `npm ci` + `tsc && vite build`, clean.

## 4. Explicitly out of scope

- A background job/polling system for exports; the current synchronous response is sufficient for the library size.
- Personal notes as Anki cards (not Q&A-shaped — see §1).
- Sticky notes / whiteboard notes (not a per-paper concept, different from a
  research library export).
- A real `.apkg` deck file (plain TSV decided instead — Anki imports it
  natively, no new dependency needed).
