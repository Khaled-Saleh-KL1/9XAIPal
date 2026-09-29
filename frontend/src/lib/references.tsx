/**
 * Clickable in-body bibliography citations ("[12]" -> hover/click -> resolve
 * -> add to library). Papers only — see docs on why books/articles don't get
 * this (project convention: state the scope, don't silently skip it).
 *
 * Two pieces:
 *  - useReferences: fetch-once-per-paper hook, mirrors pageMap.ts's
 *    useFetchedPageMap exactly (same shape, same reasoning).
 *  - remarkCitationRefs: a remark plugin that turns a "[12]" run of digits
 *    into a clickable chip, but ONLY when every number in the bracket is a
 *    real, known reference number for this paper — that membership check is
 *    what keeps a stray bracket (an index into a list, a footnote marker
 *    that happens to look like this) from ever being mistaken for a
 *    citation, without any fragile heuristics.
 */

import { useEffect, useMemo, useState } from 'react';
import { visit } from 'unist-util-visit';
import type { Root, Text, Parent } from 'mdast';
import type { Components } from 'react-markdown';
import { getReferences, type ReferenceCitationStyle, type ReferenceEntry } from '../api';
import { BibCitationRef } from '../views/BibCitationRef';

export interface ReferenceIndex {
  /** Style detected while parsing this paper's bibliography. */
  citationStyle: ReferenceCitationStyle;
  /** Every citation number this paper's References section actually has. */
  numbers: Set<number>;
  byNumber: Map<number, ReferenceEntry>;
}

export const EMPTY_REFERENCE_INDEX: ReferenceIndex = {
  citationStyle: 'numeric',
  numbers: new Set(),
  byNumber: new Map(),
};

/** Fetch a paper's parsed bibliography once. `enabled` gates this on
 * doc_kind === 'paper' at the call site — books and articles never fetch. */
export function useReferences(paperId: string | null | undefined, enabled: boolean): ReferenceIndex {
  const [referenceList, setReferenceList] = useState<{
    entries: ReferenceEntry[];
    citationStyle: ReferenceCitationStyle;
  } | null>(null);

  useEffect(() => {
    if (!paperId || !enabled) { setReferenceList(null); return; }
    let alive = true;
    setReferenceList(null);
    getReferences(paperId)
      .then((list) => {
        if (alive) setReferenceList({ entries: list.references, citationStyle: list.citation_style });
      })
      .catch(() => { if (alive) setReferenceList({ entries: [], citationStyle: 'numeric' }); });
    return () => { alive = false; };
  }, [paperId, enabled]);

  return useMemo(() => {
    if (!referenceList) return EMPTY_REFERENCE_INDEX;
    return {
      citationStyle: referenceList.citationStyle,
      numbers: new Set(referenceList.entries.map((e) => e.number)),
      byNumber: new Map(referenceList.entries.map((e) => [e.number, e])),
    };
  }, [referenceList]);
}

const CITATION_RE = /\[(\d+(?:,\s*\d+)*)\]/g;
const SURNAME_PATTERN = String.raw`[\p{L}\p{M}][\p{L}\p{M}'’.-]*`;
const AUTHOR_PATTERN = String.raw`${SURNAME_PATTERN}(?:\s+(?:et\s+al\.?|(?:and|&)\s+${SURNAME_PATTERN}))?`;
const AUTHOR_YEAR_CITATION_RE = new RegExp(
  String.raw`(?<![\p{L}\p{M}])(${AUTHOR_PATTERN})(?:\s*,\s*((?:19|20)\d{2}[a-z]?)|\s+\(\s*((?:19|20)\d{2}[a-z]?)\s*\)|\s+((?:19|20)\d{2}[a-z]?))(?![\p{L}\p{M}\d])`,
  'giu',
);
const YEAR_IN_REFERENCE_RE = /(?<!\d)((?:19|20)\d{2}[a-z]?)(?!\d)/gi;

/**
 * Splits a "[5, 2, 35]"-shaped run out of a text node into a marker `span`
 * (className "citation-ref", data-numbers holding the comma list) wrapping
 * the original bracket text — rendered by the `span` override in
 * lib/markdown.ts, which is what actually swaps in <BibCitationRef>.
 *
 * A `span` carrying a real HTML tag name rather than a made-up one sidesteps
 * two problems a custom element name would hit: rehype-sanitize would strip
 * an unknown tag outright (see SANITIZE_SCHEMA's own comment on why `video`
 * needed adding), and react-markdown's `Components` type is keyed by real
 * JSX intrinsic tag names, so a custom tag wouldn't type-check either.
 *
 * ⚠ Whole-bracket, not per-number: if ANY number in "[5, 2, 35]" is not a
 * known reference, the WHOLE bracket is left as plain text rather than
 * linkifying the numbers that do match — a partially-clickable bracket would
 * be a confusing, half-working control for what should be a rare edge case.
 */
export function remarkCitationRefs(refNumbers: Set<number>) {
  return (tree: Root) => {
    if (refNumbers.size === 0) return; // fast no-op: articles, or a paper with no numbered refs at all
    visit(tree, 'text', (node: Text, index, parent: Parent | undefined) => {
      if (!parent || index == null) return;
      const value = node.value;
      CITATION_RE.lastIndex = 0;
      const matches = [...value.matchAll(CITATION_RE)];
      if (matches.length === 0) return;

      const out: (Text | ReturnType<typeof citationNode>)[] = [];
      let cursor = 0;
      let matchedAny = false;
      for (const m of matches) {
        const numbers = m[1].split(',').map((s) => parseInt(s.trim(), 10));
        if (!numbers.every((n) => refNumbers.has(n))) continue;
        matchedAny = true;
        const start = m.index as number;
        if (start > cursor) out.push({ type: 'text', value: value.slice(cursor, start) });
        out.push(citationNode(numbers, m[0]));
        cursor = start + m[0].length;
      }
      if (!matchedAny) return;
      if (cursor < value.length) out.push({ type: 'text', value: value.slice(cursor) });

      parent.children.splice(index, 1, ...(out as any));
      // Tell visit to resume after the nodes we just spliced in, not at the
      // same index — otherwise it would re-visit the replacement text nodes
      // and, on a paper whose OWN bibliography text is itself being scanned
      // (edge case: none today per parse_references, but cheap to guard),
      // loop.
      return index + out.length;
    });
  };
}

/** Turn unambiguous author/year text into the same numbered marker the
 * existing citation chip consumes. Parentheses around a citation stay as
 * ordinary text; a narrative citation's year parentheses remain in the chip.
 */
export function remarkAuthorYearCitationRefs(entriesByNumber: Map<number, ReferenceEntry>) {
  return (tree: Root) => {
    if (entriesByNumber.size === 0) return;
    visit(tree, 'text', (node: Text, index, parent: Parent | undefined) => {
      if (!parent || index == null) return;
      const value = node.value;
      AUTHOR_YEAR_CITATION_RE.lastIndex = 0;
      const matches = [...value.matchAll(AUTHOR_YEAR_CITATION_RE)];
      if (matches.length === 0) return;

      const out: (Text | ReturnType<typeof citationNode>)[] = [];
      let cursor = 0;
      let matchedAny = false;
      for (const match of matches) {
        const surname = match[1].match(new RegExp(SURNAME_PATTERN, 'u'))?.[0];
        const citationYear = (match[2] || match[3] || match[4]).toLowerCase();
        if (!surname) continue;
        const normalizedSurname = normalizeSurname(surname);
        const matchingEntries = [...entriesByNumber.values()].filter((entry) => (
          entry.first_author
          && normalizeSurname(entry.first_author) === normalizedSurname
          && entry.year === Number.parseInt(citationYear.slice(0, 4), 10)
          && referenceYearKey(entry) === citationYear
        ));
        if (matchingEntries.length !== 1) continue;

        const start = match.index as number;
        if (start > cursor) out.push({ type: 'text', value: value.slice(cursor, start) });
        out.push(citationNode([matchingEntries[0].number], match[0]));
        cursor = start + match[0].length;
        matchedAny = true;
      }
      if (!matchedAny) return;
      if (cursor < value.length) out.push({ type: 'text', value: value.slice(cursor) });

      parent.children.splice(index, 1, ...(out as any));
      return index + out.length;
    });
  };
}

function normalizeSurname(surname: string): string {
  return surname.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

function referenceYearKey(entry: ReferenceEntry): string | null {
  if (entry.year == null) return null;
  YEAR_IN_REFERENCE_RE.lastIndex = 0;
  const years = [...(entry.raw_text || '').matchAll(YEAR_IN_REFERENCE_RE)];
  const afterAuthors = (entry.raw_text || '').match(/\.\s*((?:19|20)\d{2}[a-z]?)\b/i)?.[1];
  const preferred = afterAuthors && Number.parseInt(afterAuthors.slice(0, 4), 10) === entry.year
    ? afterAuthors
    : [...years].reverse().find((match) => Number.parseInt(match[1].slice(0, 4), 10) === entry.year)?.[1];
  return preferred?.toLowerCase() ?? String(entry.year);
}

function citationNode(numbers: number[], raw: string) {
  return {
    type: 'text' as const,
    // Not real markdown text — a placeholder value satisfying mdast's Text
    // shape. mdast-util-to-hast prefers `data.hChildren` over re-deriving
    // content from `value` once hName is set, so this string is never shown;
    // the real citation text is `raw` in hChildren immediately below.
    value: raw,
    data: {
      hName: 'span',
      hProperties: { className: 'citation-ref', 'data-numbers': numbers.join(',') },
      hChildren: [{ type: 'text', value: raw }],
    },
  };
}

/**
 * `span` override for ReactMarkdown's `components` prop: swaps in
 * <BibCitationRef> for the marker spans remarkCitationRefs produces, and
 * passes every ordinary `<span>` (KaTeX's math spans included) straight
 * through untouched. One factory call per rendering paper (see
 * ArticleBlock's `Md`), closing over that paper's id and reference index so
 * BibCitationRef doesn't need either threaded through react-markdown's
 * props.
 */
export function makeCitationSpanComponent(
  paperId: string,
  refIndex: ReferenceIndex,
  onOpenPaper?: (documentId: string) => void,
): Pick<Components, 'span'> {
  return {
    span: ({ className, children, ...rest }) => {
      const numbersAttr = (rest as Record<string, unknown>)['data-numbers'];
      if (className !== 'citation-ref' || typeof numbersAttr !== 'string') {
        return <span className={className} {...rest}>{children}</span>;
      }
      const numbers = numbersAttr.split(',').map((s) => parseInt(s, 10)).filter((n) => !Number.isNaN(n));
      return <BibCitationRef paperId={paperId} numbers={numbers} refIndex={refIndex} onOpenPaper={onOpenPaper} />;
    },
  };
}
