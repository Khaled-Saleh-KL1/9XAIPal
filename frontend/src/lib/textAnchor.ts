/**
 * Text anchors: the position two views of the same document agree on when
 * there are no page numbers to agree on.
 *
 * A doc_kind='article' has no pages at all — its raw view is one long HTML
 * snapshot and its structured view is a markdown block list — and a PDF
 * extracted through the markdown fallback (no content_list.json) has no
 * per-chunk page either (see extraction/pipeline_sync.py, which only gets
 * page numbers from content_list). For all of those, "open the other view
 * where I am" has to be answered with the text itself.
 *
 * Both sides of the match come from the SAME extraction — the reader's
 * block text was pulled out of that exact HTML by trafilatura — so this is
 * matching a string against a near-copy of itself, not fuzzy search. What
 * it has to survive is the rendering difference: collapsed whitespace,
 * decoded entities, markdown syntax on one side and none on the other.
 * Hence normalize hard (letters, digits and single spaces only), then
 * compare.
 */

/** Chars of normalized text carried as the anchor. Long enough to be unique
 *  in a document, short enough to survive one side truncating a paragraph. */
const ANCHOR_CHARS = 120;

/** Below this many matched characters it is coincidence, not the passage —
 *  two paragraphs opening with "in this section we" must not count. */
const MIN_MATCH_CHARS = 24;

/** Letters, digits and single spaces. Everything else is rendering noise:
 *  markdown syntax, entity artifacts, punctuation the two sides disagree on. */
export function normalizeAnchor(text: string): string {
  return text
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .toLowerCase();
}

/** The anchor payload for a passage: normalized, capped. */
export function makeAnchor(text: string): string {
  return normalizeAnchor(text).slice(0, ANCHOR_CHARS);
}

function commonPrefixLength(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

/**
 * How well `candidate` matches `anchor`, in characters. Containment scores
 * as the full overlap (the anchor is a *prefix* of a passage, so the raw
 * side's element text routinely contains it, or vice versa when that
 * element holds only part of the paragraph); otherwise a shared opening is
 * all there is to go on.
 */
export function matchScore(anchor: string, candidate: string): number {
  const a = normalizeAnchor(anchor);
  const c = normalizeAnchor(candidate);
  if (!a || !c) return 0;
  if (c.includes(a) || a.includes(c)) return Math.min(a.length, c.length);
  return commonPrefixLength(a, c);
}

/**
 * Index of the candidate that best matches `anchor`, or -1 when nothing
 * clears MIN_MATCH_CHARS. Ties go to the earliest candidate, so a repeated
 * boilerplate line resolves to its first occurrence rather than a random one.
 */
export function bestMatchIndex(anchor: string, candidates: string[]): number {
  const a = normalizeAnchor(anchor);
  if (a.length < MIN_MATCH_CHARS) return -1;

  let bestIndex = -1;
  let bestScore = 0;
  for (let i = 0; i < candidates.length; i++) {
    const score = matchScore(a, candidates[i]);
    if (score > bestScore) {
      bestScore = score;
      bestIndex = i;
    }
  }
  return bestScore >= MIN_MATCH_CHARS ? bestIndex : -1;
}
