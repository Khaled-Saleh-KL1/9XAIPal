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

## 1. Four formats, chosen in a four-step panel

All four formats export an explicit set of papers chosen in the export panel
(`components/ExportWizard.tsx`): *select* (search + Books/Research/Articles
chips + a checklist) → *format* → *running* (progress bar, a red Cancel that
aborts the request) → *done* (tick, "Exported successfully", Done back to the
library). The browser sends the chosen document ids to one authenticated POST
endpoint. Formatting remains synchronous because a personal research library
(dozens of papers, not thousands) is fast enough to build in one response.

⚠ **Selection lives only inside the panel — recorded because it was shipped
the other way first.** An intermediate version put a checkbox on every library
card, permanently, and its Export menu did nothing until papers were ticked
there: a visual tax on the whole library for an occasional action, and a flow
that read as "broken" rather than "waiting" — the API logs showed **zero export
requests had ever reached the server** from it, every click having died on an
`if (!count) return` guard before any request or progress UI. Everything to do
with choosing what to export now appears only after pressing Export.

- **BibTeX** (`services/export.py::to_bibtex`) — one entry per paper. `@article`
  when Semantic Scholar resolved real authors, `@misc` with an honest `note`
  field when it didn't, rather than a blank author field a reader might mistake
  for a gap in THEIR bibliography instead of an unresolved import. Every field
  passes through `_escape_bibtex` (`{`, `}`, `&`, `%`, `$`, `#`, backslash —
  backslash escaped first, or escaping any of the others would double-escape
  the backslash it just inserted) — an untested title with a real `%` or `&`
  in it (verified against one) would otherwise produce a `.bib` file that
  doesn't parse. Cite keys collide-proofed with an `a`/`b`/`c` suffix, not
  silently overwritten. ⚠ **`year` only when resolved.** The first version fell
  back to the year the paper was *added*, which put `year = {2026}` on
  *Attention Is All You Need* (2017): a bibliography year is a publication
  year, and a fabricated one is worse than none — a reader pastes it into
  their own paper and cites it wrong. The date-added lives in the CSV, whose
  column is honestly named `date_added`. Unresolved cite keys are the title
  slug cut at a word boundary (`attention-is-all-you-need`, not the
  `attention-is-all-you-nee2026` a hard 24-char cut plus that fake year gave).
- **Markdown** (`to_markdown_note` / `to_markdown_zip`) — one `.md` per paper,
  not one giant file and not one file per note. Obsidian (and anything that
  indexes a folder) treats each file as its own linkable unit; a paper with
  thirty notes as thirty tiny files would be worse to navigate than the same
  thirty notes as sections of one file named after the paper. **One paper is
  sent as the `.md` itself (`<title-slug>.md`); two or more as `notes.zip`.**
  Shipped zip-always first, and a single-paper export arrived as a ZIP with
  one file inside — friction with no benefit. The container only earns its
  place once there is more than one file to hold. The wizard's done screen
  reads the real filename off `Content-Disposition` for the same reason: a
  static label would name the wrong one.
- **Anki** (`to_anki_tsv`) — `question\tanswer` lines, Anki's own "Import File"
  reads this with zero setup. From `paper_notes` (the AI Q&A) only:
  `personal_notes` are free text, not a front/back pair, and are not silently
  reshaped into one. **Each field is the answer's Markdown rendered to the HTML
  Anki actually displays** (`markdown-it-py`, CommonMark, raw HTML escaped),
  with `$x$`/`$$x$$` re-delimited to MathJax's `\( \)`/`\[ \]` — done on the
  rendered HTML, *after* markdown-it, because `\(` is a CommonMark escape and
  converting first hands the renderer a bare `(N=6)`. Then flattened to one
  line: Anki's TSV contract, a literal tab or newline would break the row. The
  first version shipped the raw Markdown — `*   **Encoder:**`, `$N=6$` and the
  app's `[[30], [31]]` citation markers all appeared on the card verbatim. Zero
  qualifying cards is a `422` naming what would make cards exist, not a silent
  0-byte download.
- **Citation markers are stripped from every model answer on the way out**
  (`_strip_cite_markers`): `[[11]]`, `[[30], [31]]`, the desk's `[[P2:41]]`.
  The reader turns these into chips; anywhere else they are noise, and in
  Obsidian `[[11]]` is *wiki-link syntax* that creates a phantom note called
  "11". Only digit/`P`/colon content matches, so a reader's own `[[My Note]]`
  in a personal note is untouched.
- **Titles match the library** (`_display_title`): a rename wins, else the
  filename minus `.pdf` — the same rule as the frontend's `displayTitle`.
  Shipped as `title = {Attention Is All You Need.pdf}` before this was mirrored.
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

⚠ **Status codes were not enough.** Every format returned `200` with the right
headers from the first day, and four real defects sat behind those 200s until
the exported *files* were opened as a reader would, with the reader's own notes
(`.pdf` in titles, a fabricated `year`, citation markers and raw Markdown on
Anki cards, a silent 0-byte flashcards file). The formatters had no pytest
coverage at all; [`tests/test_export.py`](../../backend/tests/test_export.py)
now holds every one of those as a test, so they cannot come back. The list
below is what that file and the endpoint checks cover.

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
- **The panel itself, every step rendered for real** (2026-09-10): `ExportPanel` is
  a pure render of one step from props, so each screen is `renderToString`'d
  directly and asserted — 31 checks across select (list, chips, search, empty
  and no-match states, Next disabled at 0 selected), format (all four offered,
  Export disabled until one is picked, the post-cancel notice), running (bar
  width, the red Cancel, the label flip at 70%), done (tick, filename, Done),
  and error (message, Try again, Close). Not just `tsc`: the clickable-
  citations regression passed `tsc` and a build and threw on first render.
- **The wizard's exact POST against the real backend** for all four formats:
  `200`, correct `Content-Disposition`, real content; a bogus id → `404`, not a
  silent fall-back to exporting everything.

## 4. Explicitly out of scope

- A background job/polling system for exports; the current synchronous response is sufficient for the library size.
- Personal notes as Anki cards (not Q&A-shaped — see §1).
- Sticky notes / whiteboard notes (not a per-paper concept, different from a
  research library export).
- A real `.apkg` deck file (plain TSV decided instead — Anki imports it
  natively, no new dependency needed).
