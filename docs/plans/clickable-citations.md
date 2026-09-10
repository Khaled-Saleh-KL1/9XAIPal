# Clickable bibliography citations

> **What this is:** the change that turned a paper's own in-body citations ("[12]") from inert
> text into a control that shows what paper that is and adds it to the library in one click.
>
> **How to read it:** §1 the problem and scope → §2 how a reference is found in the paper's own
> text → §3 how it's resolved to a real paper → §4 how it's added → §5 what was verified and how →
> §6 what this deliberately does not do.
>
> **Companions (detail):**
> [api.md](../03-reference/api.md) (`## References`): the three endpoints ·
> [database-schema.md](../03-reference/database-schema.md) (`paper_references`): the table ·
> [frontend.md](../02-architecture/frontend.md) (`Bibliography citations`): the reader-side wiring.
>
> **Status:** shipped · **Reflects code as of:** 2026-09-09

---

## 1. The problem, and the scope decided up front

A paper's body cites its own References list with markers like `[12]` or `[5, 2, 35]`. Before this,
that was inert text — reading it meant scrolling to the References section, finding entry 12 by
hand, then searching for that paper yourself if you wanted to read it.

**Papers only.** Numbered `[N]` + a parseable References list is a paper-specific convention: books
here load one chapter's window at a time and their bibliographies (when they use numbered
citations at all — many academic books don't) sit in back matter outside that window, and articles
are web snapshots with no numbered citations at all. Consistent with how page numbers were already
scoped to skip articles.

**Resolution goes through Semantic Scholar's Graph API**, not arXiv's own API: one call returns
title/authors/year plus arXiv ID, DOI, and often a direct open-access PDF URL, covering far more
than arXiv alone (workshop papers, older NLP papers, non-arXiv venues). ⚠ **The unauthenticated
tier is already rate-limited from this box** — verified during design, every call came back 429,
including after backing off. `SEMANTIC_SCHOLAR_API_KEY` (free, from
https://www.semanticscholar.org/product/api) is effectively required, not optional, for resolution
to ever succeed here; without it every reference sits at `resolve_status='unavailable'` forever,
which is a real, surfaced state (see api.md), not a silent failure.

## 2. Finding a reference in the paper's own text

[`services/references.py`](../../backend/app/services/references.py)'s `parse_references` is a
pure function: chunks in, entries out, no DB. Two things about the corpus it depends on, both
verified against real papers rather than assumed:

- **A reference entry is not one chunk.** MinerU emits the whole bibliography as consecutive
  `chunk_type='text'` chunks, several entries concatenated per chunk with a literal `- [N] ` list
  marker starting each entry's own line (chunk 119 in the Attention-paper corpus holds both `[1]`
  and `[2]`). Entries are told apart by where a NEW `- [N]` starts a line, not by newlines alone —
  an entry's own text can wrap onto a following line without that marker.
- **Not every paper has one.** Parsed 4 real papers in the corpus: two (40/40 and 47/47 references,
  zero gaps) parse cleanly. Two come back empty — not a bug: both cite by author-year with an
  alphabetical bibliography (no `[N]` markers anywhere, e.g. `- Jonas Gehring, ... 2017.` with no
  bracket number), a citation convention this feature doesn't cover. `[]` from
  `GET /papers/{id}/references` is this same real answer, not a failure.

## 3. Resolving a reference

[`search/semantic_scholar_client.py`](../../backend/app/search/semantic_scholar_client.py)'s
`match_reference` calls `/graph/v1/paper/search/match` — the endpoint Semantic Scholar built
specifically for "given a citation string, find the paper" — and returns either a `ReferenceMatch`
or an `Unresolved` enum value (`NO_MATCH` vs. `UNAVAILABLE`), never raises. The distinction matters
downstream: `resolve_status` only becomes permanent (`resolved`/`no_match`) on a completed lookup;
`unavailable` (no key, 429, network error) is retried on the next call rather than cached as a dead
end — fixing the key or waiting out the rate limit should turn it into a real answer, and a row
stuck at `unavailable` forever because of a first bad attempt would defeat that.

Cached in `paper_references` (see database-schema.md) so a second `/resolve` call, or a different
citation elsewhere pointing at the same paper, doesn't re-hit the API.

## 4. Adding a resolved reference to the library

`POST /papers/{id}/references/{n}/add` reuses `documents.py`'s `import_article` path exactly —
`check_queue_capacity` → `create_document(doc_kind='article', source_url=...)` →
`create_ingestion_job` → `process_article_ingestion.delay(..., kind='paper')` — rather than a new
ingestion route. Semantic Scholar's "open access PDF" link isn't always actually a raw, fetchable
PDF, so what it turns out to be is only known once the ingestion task fetches it, the same
uncertainty `import_article` already handles for any pasted URL.

Deduped two ways, both verified against a real add: `added_document_id` on the row itself (a
repeat `/add` on the same reference), and `documents.source_url` (a **different** reference,
possibly on a different paper, resolving to a URL already in the library) — via the new
`get_document_by_source_url` repo function. Neither re-ingests; both link the existing document and
return `already_existed: true`.

Frontend-side, adding does **not** reuse `App.tsx`'s `handleArticleImport`/`submitImportUrl` — see
frontend.md for why (that navigates the whole app to the upload overlay, wrong for a background
add while still reading the citing paper).

## 5. What was verified, and how

Everything below ran against a throwaway Postgres/Redis stack and real HTTP requests through the
actual container image — the live library (11 documents) was untouched throughout.

- `parse_references` against 4 real papers (§2).
- The full HTTP surface, logged in as a real user: `/references` (cache-on-first-call, second call
  doesn't re-parse), `/resolve` (404 on a bad number, graceful `unavailable` with no key
  configured — the real production state until a key is added, retryable not stuck), `/add` (422
  before resolved, real ingestion job created, both dedupe paths, ownership scoping).
- ⚠ **`/resolve` was missing its ownership check** — found in this same verification pass (a
  second user could resolve/read another user's paper's references) and fixed before shipping;
  re-verified with a two-user test.
- The remark plugin end-to-end through the actual `unified`/`remark-parse`/`remark-rehype`
  pipeline (not just unit logic) against a real sampled sentence: `[5, 2, 35]` → one marker span
  with all three numbers when all are known refs; `[999]` (not a known ref) → left as plain text;
  `[Note]` → untouched (the regex only matches digit lists).
- The `rehype-sanitize` schema addition specifically: proven **necessary**, not just harmless — run
  through the real pipeline both with and without the `markdown.ts` schema edit. Without it the
  `<span>` element survives sanitization but its `properties` come back `{}` — `className` and
  `data-numbers` both silently stripped, which would have made every citation chip permanently
  inert with no error anywhere.
- `npm ci` (the exact command CI runs) followed by `tsc && vite build`, clean, from the lockfile
  this change updates (`unist-util-visit`, `@types/mdast` — added as explicit dependencies rather
  than relied on as another package's undeclared transitive install).

## 6. Explicitly out of scope

- Books and articles (§1).
- A reference the paper's own extraction never captured a full citation string for.
- A citation *graph view* (browsing "papers that cite this paper," a visual web) — this ships the
  primitive (resolve + add) a graph would sit on top of, not the graph itself.
