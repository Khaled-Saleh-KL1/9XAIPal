import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { getPageIndex, type DocBlock } from '../api';

/**
 * Which printed page a block came from, for everything that cites one.
 *
 * ⚠ **Exact pages only.** `ArticleReader`'s `rawPosition()` deliberately
 * approximates — walking back to the nearest block that carries a page, and
 * failing that estimating one from how far through the document you are —
 * because an approximate page beats opening the raw PDF at page 1. A citation
 * is the opposite case: a chip reading "p. 7" is a claim about where the words
 * are, and a reader who turns to page 7 and does not find them has been told
 * something false by a tool whose whole job is grounding. So this resolver
 * returns a block's own `page_start` or nothing at all, and every caller falls
 * back to the paragraph number, which is always exact.
 *
 * Pages are absent for a whole document at a time rather than block by block:
 * only `content_list.json` extraction carries them, so a PDF that fell back to
 * markdown chunking has none, and an imported article never has any (see
 * `extraction/pipeline_sync.py`). Hence `hasPages` — a reader can show the
 * paragraph currency without flickering between the two.
 */
export interface PageMap {
  /** The page a block is printed on, or null when this document has none. */
  pageFor: (sequenceId: number | null | undefined) => number | null;
  /** Whether any block in this document carries a page at all. */
  hasPages: boolean;
}

export const EMPTY_PAGE_MAP: PageMap = { pageFor: () => null, hasPages: false };

/**
 * A page map from `(sequence_id, page)` pairs — what `GET /papers/{id}/pages`
 * returns.
 *
 * The article reader builds its map from the blocks it already holds; the book
 * reader has only one chapter's window in memory at a time and needs the whole
 * index, since the agent cites blocks from chapters that window never loaded.
 */
export function buildPageMapFromPairs(pairs: [number, number][] | undefined | null): PageMap {
  if (!pairs?.length) return EMPTY_PAGE_MAP;
  const pages = new Map<number, number>(pairs);
  return {
    pageFor: (seq) => (seq == null ? null : pages.get(seq) ?? null),
    hasPages: true,
  };
}

export function buildPageMap(blocks: DocBlock[] | undefined | null): PageMap {
  if (!blocks?.length) return EMPTY_PAGE_MAP;

  const pages = new Map<number, number>();
  for (const b of blocks) {
    if (b.page_start != null) pages.set(b.sequence_order, b.page_start);
  }
  if (!pages.size) return EMPTY_PAGE_MAP;

  return {
    pageFor: (seq) => (seq == null ? null : pages.get(seq) ?? null),
    hasPages: true,
  };
}

/**
 * The open document's page map, for the cards in the margin.
 *
 * A context rather than a prop because the things that cite a block sit four
 * or five components below the reader that owns the blocks — a note inside a
 * deck inside the marginalia panel — and none of the components in between
 * have any other reason to know about pages.
 */
const PageMapContext = createContext<PageMap>(EMPTY_PAGE_MAP);

export const PageMapProvider = PageMapContext.Provider;

export function usePageMap(): PageMap {
  return useContext(PageMapContext);
}

/** Memoized `buildPageMap`, for the reader that provides the context. */
export function useBuiltPageMap(blocks: DocBlock[] | undefined | null): PageMap {
  return useMemo(() => buildPageMap(blocks), [blocks]);
}

/**
 * The page index for one document, fetched once.
 *
 * For the book reader, which cannot build a map from what it has in memory.
 * A failed fetch is not an error worth surfacing: the map falls back to empty
 * and every chip shows its block number, exactly as it did before pages
 * existed.
 */
export function useFetchedPageMap(paperId: string | null | undefined): PageMap {
  const [pairs, setPairs] = useState<[number, number][] | null>(null);

  useEffect(() => {
    if (!paperId) { setPairs(null); return; }
    let alive = true;
    setPairs(null);
    getPageIndex(paperId)
      .then((p) => { if (alive) setPairs(p); })
      .catch(() => { if (alive) setPairs([]); });
    return () => { alive = false; };
  }, [paperId]);

  return useMemo(() => buildPageMapFromPairs(pairs), [pairs]);
}

/**
 * How a page is written wherever one is shown: "p. 7".
 *
 * Centralised so the reader, the desk and the book chat cannot drift into
 * three spellings of the same fact.
 */
export function formatPage(page: number | null | undefined): string | null {
  return page == null ? null : `p. ${page}`;
}

/**
 * The label for a chip that points at one block: its page when the document
 * has pages, its paragraph number when it does not.
 *
 * `title` carries both, always, so the paragraph number stays reachable for
 * anyone who wants to jump precisely rather than turn to a page.
 */
export function citeChipLabel(
  seq: number,
  page: number | null,
): { label: string; title: string } {
  return page == null
    ? { label: `¶${seq}`, title: `Paragraph ${seq}` }
    : { label: `p. ${page}`, title: `Page ${page}, paragraph ${seq}` };
}

export interface CiteChip {
  /** Where clicking lands: the first block cited on this page. */
  seq: number;
  label: string;
  title: string;
}

/**
 * One chip per *place*, not per block.
 *
 * ⚠ Grounded answers routinely cite three consecutive paragraphs, which under
 * paragraph labels reads "¶41 ¶42 ¶43" — three distinct destinations, fair
 * enough — but under page labels becomes "p. 7 p. 7 p. 7", which tells the
 * reader nothing three times and buries the one citation that was on a
 * different page. So blocks sharing a page collapse into a single chip that
 * jumps to the first of them, with every paragraph it covers named in the
 * tooltip. Order of first use is preserved; it is the order the answer used
 * them in, and it is what makes a chip row scannable against the prose.
 */
export function citeChips(cited: number[], pageFor: PageMap['pageFor']): CiteChip[] {
  const chips: CiteChip[] = [];
  const byPage = new Map<number, { chip: CiteChip; seqs: number[] }>();

  for (const seq of cited) {
    const page = pageFor(seq);
    if (page == null) {
      const { label, title } = citeChipLabel(seq, null);
      chips.push({ seq, label, title });
      continue;
    }
    const existing = byPage.get(page);
    if (existing) {
      existing.seqs.push(seq);
      existing.chip.title = `Page ${page}, paragraphs ${existing.seqs.join(', ')}`;
      continue;
    }
    const { label, title } = citeChipLabel(seq, page);
    const chip: CiteChip = { seq, label, title };
    byPage.set(page, { chip, seqs: [seq] });
    chips.push(chip);
  }

  return chips;
}
