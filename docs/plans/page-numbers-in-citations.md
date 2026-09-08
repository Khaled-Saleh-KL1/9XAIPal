# Page numbers in citations

> **What this is:** the change that made every surface which quotes a document say which printed
> page the quote came from, and the record of what the roadmap got wrong about why it did not.
>
> **How to read it:** §1 the premise, and how it was wrong → §2 where a page is exact and where it
> is a guess → §3 the surfaces → §4 collapsing chips by page → §5 what still has no page.
>
> **Companions (detail):**
> [database-schema.md](../03-reference/database-schema.md): the `chunks.page_start` column ·
> [chat-and-ask.md](../02-architecture/chat-and-ask.md): the routes that build citations ·
> [ingestion-pipeline.md](../02-architecture/ingestion-pipeline.md): where pages are extracted.
>
> **Status:** shipped · **Reflects code as of:** 2026-09-08

---

## 1. The premise, and how it was wrong

The roadmap said, under Data & schema:

> **`chunks.page_start` / `page_end` are nullable and never populated**: MinerU page metadata is
> not wired through. Page-based citation is therefore impossible today.

Every clause of that except "nullable" was false, and it had been false since the
`content_list.json` chunker was written. [`extraction/chunker.py`](../../backend/app/extraction/chunker.py)
reads `page_idx` off each entry and converts it from MinerU's 0-based index to a 1-based page;
[`pipeline_sync.py`](../../backend/app/extraction/pipeline_sync.py) writes both columns;
[`chat/citations.py`](../../backend/app/chat/citations.py) has always copied `page_start` into
`Citation.page` and shipped it to the client.

Measured on the live database before touching anything:

| | chunks | with a page |
| --- | --- | --- |
| `doc_kind='book'` | 5255 | 5255 |
| `doc_kind='paper'` | 877 | 877 |
| `doc_kind='article'` | 389 | 0 |
| **total** | **6521** | **6132** |

So the data was complete for every document that has pages at all, and `page_start = page_end` on
all 6132 rows (no `content_list.json` block straddles a page break).

**What was actually missing was the display.** Every surface that points at a passage threw the
page away at the last step — most by labelling the chip with an internal block number, and
`ChatPane` by ignoring the `page` field the backend had been sending it all along and labelling
the chip with 200 characters of quoted text instead.
The fix is a display change plus one backend query, not a pipeline change. Worth recording,
because the roadmap entry had made this look like an extraction project for months.

## 2. Where a page is exact, and where it is a guess

`ArticleReader`'s `rawPosition()` already resolved a page, and deliberately approximates: it walks
back to the nearest preceding block that carries one, and failing that estimates a page from how
far through the document the reader is. That is correct for what it does — open the raw PDF near
where I am — where an approximate page beats page 1.

**A citation is the opposite case and must not reuse it.** A chip reading "p. 7" is a claim about
where words are printed. A reader who turns to page 7, does not find them, and realises the
number was interpolated has been misled by the one feature whose entire purpose is letting them
check the model. So [`lib/pageMap.ts`](../../frontend/src/lib/pageMap.ts) resolves a block to its
own `page_start` or to nothing, and every caller falls back to `¶<sequence_id>`, which is always
exact. The two resolvers live side by side on purpose, with the reason written at both.

## 3. The surfaces

| Surface | Was | Now | How it gets the page |
| --- | --- | --- | --- |
| Margin note citations (`NoteCard`) | `¶41` | `p. 7` | `PageMapContext`, from blocks already loaded |
| Agent trail (`AgentTrail`) | `¶41` | `p. 7` | same context; falls back outside the reader |
| Card eyebrow (`NoteChrome`) | `¶41` | `p. 7` | same context; this one is the note's own anchor rather than a citation |
| Desk citations (`CitationRef`) | `P2:41` | `P2 · p. 7` | new: `cited_refs_with_pages` |
| Book chat (`ChatPane`) | the quoted text | `p. 7` | `Citation.page`, already on the wire |

`PageMapContext` is a context rather than a prop because the cards that cite a block sit four or
five components below the reader that owns them (a note, inside a deck, inside the marginalia
panel) and nothing in between has any other reason to know about pages.

The desk keeps its paper number in the label: a desk answer spans several papers at once, so a
bare "p. 7" would be ambiguous in exactly the case the desk exists for. Its pages come from
[`chunks.get_pages_for_refs`](../../backend/app/database/repositories/chunks.py) — one tuple-IN
query for the whole citation list, since an answer routinely cites a dozen blocks across four
papers and a page number is not worth a dozen round trips. `cited_refs` itself stays sync and
DB-free so the citation *parsing* remains testable without Postgres; `cited_refs_with_pages`
only decorates what it returned.

Turns stored before this change have no `page` key in their `cited` JSON, which is why the field
is optional client-side and absent means "show the block number".

## 4. Collapsing chips by page

Under block numbers, an answer citing three consecutive paragraphs renders `¶41 ¶42 ¶43` — three
real destinations. Under page numbers the same answer renders `p. 7 p. 7 p. 7`, which says one
thing three times and visually buries the one citation that was on a different page.

So `citeChips()` collapses blocks that share a page into a single chip that jumps to the first of
them, naming every paragraph it covers in the tooltip, and preserves order of first use. In the
agent trail this happens **before** the six-chip cap, so a `SECTION` call returning forty blocks
over three pages is three chips and the cap hides nothing.

## 5. What still has no page

- **PDFs that fell back to markdown chunking.** Only the `content_list.json` path carries
  `page_idx`. These cite by paragraph, correctly and without a wrong number.
- **Imported articles** (`doc_kind='article'`), which are web pages and have no pages to carry.
  All 389 page-less chunks in the corpus are these.
- **`OVERVIEW`-route citations**, built from `section_summaries` in `citations_from_overview`,
  which anchors on a summary rather than a block and never had a page.

None of these is a regression, and each degrades to `¶N` on its own. The remaining work, if
"every citation has a page" is wanted, is a page for the markdown fallback path.
