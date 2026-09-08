/**
 * Whether the paper reader hands you the paper one block at a time.
 *
 * The book reader has always worked this way — you press for the next small
 * unit and the chapter arrives in pieces — while a paper has always arrived
 * whole, on the reasoning that a 12-page paper is not a 600-page textbook and
 * the interruption should be the reader's to choose. Reader feedback split
 * cleanly on it: some want the segmentation for the same cognitive-load reason
 * the book reader exists, others want the paper as one continuous article and
 * find a keypress per paragraph intolerable. Neither group is wrong, so this
 * is a preference rather than a redesign, and the whole-article default is
 * unchanged.
 *
 * ⚠ **Per device, in localStorage, not per document on the server.** It is a
 * reading style, not a property of the paper: the same reader wants the same
 * pacing on every paper they open, and a different one on the tablet they read
 * in bed than on the desk they work at. This is the same reasoning that keeps
 * reading *position* local (see readingPosition.ts) while notes and bookmarks
 * live in Postgres. It also keeps the change off the schema, whose migrations
 * are best-effort by design.
 *
 * Every access is wrapped: Safari private mode and "block site data" make
 * localStorage throw rather than return null.
 */

const KEY = 'pal:reveal-mode';

/** Per paper, because the cursor is a fact about one document. */
function cursorKey(paperId: string): string {
  return `pal:reveal-cursor:${paperId}`;
}

export function loadRevealMode(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function saveRevealMode(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, '1');
    else localStorage.removeItem(KEY);
  } catch {
    // Preference not persisting is not worth interrupting a reader over; the
    // toggle still works for this session.
  }
}

/**
 * The cursor for one paper, remembered.
 *
 * ⚠ **The cursor is stored, not recomputed.** Deriving it from scroll position
 * every time the mode came on made it unreliable in exactly the way a reader
 * notices: scroll to the end to look at a figure, toggle on, and the whole
 * paper is "already read". Reading position moves with the viewport; the
 * cursor is a record of what has been handed over, so it changes only when the
 * reader presses Next or jumps somewhere, survives a reload and a trip through
 * Whole mode, and never moves backward.
 */
export function loadRevealCursor(paperId: string): number | null {
  try {
    const raw = localStorage.getItem(cursorKey(paperId));
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export function saveRevealCursor(paperId: string, seq: number | null): void {
  try {
    if (seq == null) localStorage.removeItem(cursorKey(paperId));
    else localStorage.setItem(cursorKey(paperId), String(seq));
  } catch {
    // Same as the mode: a cursor that does not persist still works today.
  }
}

/** Evidence that the reader has actually been somewhere in the paper. */
export interface ReadingEvidence {
  /** Where the viewport is right now. Weakest signal: see below. */
  currentSeq: number | null;
  /** The stored reading position for this paper. */
  resumeSeq: number | null;
  /**
   * Blocks the reader has deliberately marked — every note anchor (asked and
   * written) and every bookmark. The strongest signal there is.
   */
  marks: number[];
}

/**
 * Where to open the cursor the first time this paper is read in stepped mode.
 *
 * ⚠ **Never below what the reader has already seen, and scroll position alone
 * cannot decide that.** Two failures to avoid, in opposite directions:
 *
 * - Opening at the first block collapses a paper someone is thirty blocks into
 *   back to a single paragraph. The mode then reads as "lose my place" rather
 *   than "slow down from here".
 * - Trusting the viewport alone is worse, because the viewport is not a record
 *   of reading. Scrolling to the end to look at a figure and coming back up is
 *   ordinary behaviour, and it would leave the cursor at the end — the whole
 *   paper "already read" — for a reader who had read ten blocks.
 *
 * So it is the furthest point of *evidence*, not of scrolling: a note or a
 * bookmark is a deliberate act at a specific passage and is therefore the most
 * reliable proof that the passage was read, and the stored reading position
 * and the current viewport are taken alongside them. The maximum wins, because
 * each one is independently proof of having been there, and being wrong in the
 * cautious direction hides something the reader has already read.
 *
 * ⚠ Only for the FIRST time. After that the cursor is stored (see
 * loadRevealCursor) and moves only when the reader presses Next or jumps, so
 * scrolling around can never shift it again.
 */
export function initialRevealCursor(
  blocks: { sequence_order: number }[],
  evidence: ReadingEvidence,
): number | null {
  if (!blocks.length) return null;

  const known = new Set(blocks.map((b) => b.sequence_order));
  const candidates = [
    evidence.currentSeq,
    evidence.resumeSeq,
    ...evidence.marks,
  ].filter((n): n is number => n != null && known.has(n));

  // ⚠ Against the block list, not by numeric value: sequence ids have gaps, so
  // a larger id is not necessarily a later block, and a stale mark from before
  // a re-chunk may not be a block at all. Position in reading order is the
  // only ordering that means anything here.
  const order = new Map(blocks.map((b, i) => [b.sequence_order, i]));
  let best: number | null = null;
  let bestAt = -1;
  for (const seq of candidates) {
    const at = order.get(seq)!;
    if (at > bestAt) { bestAt = at; best = seq; }
  }
  return best ?? blocks[0].sequence_order;
}

/**
 * The block after `cursor`, or null at the end of the paper.
 *
 * Walks the real block list rather than computing `cursor + 1`: sequence ids
 * are gap-tolerant (the same reason the book reader pages with an "after"
 * endpoint instead of incrementing), so an arithmetic next lands in a hole and
 * the Next button silently stops working part way through a paper.
 */
export function nextRevealCursor(
  blocks: { sequence_order: number }[],
  cursor: number | null,
): number | null {
  if (!blocks.length) return null;
  if (cursor == null) return blocks[0].sequence_order;
  const i = blocks.findIndex((b) => b.sequence_order === cursor);
  if (i < 0) return blocks[0].sequence_order;
  return i + 1 < blocks.length ? blocks[i + 1].sequence_order : null;
}
