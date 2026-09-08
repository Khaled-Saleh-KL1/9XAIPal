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
 * The sequence id to stop revealing at, given where the reader already is.
 *
 * ⚠ Turning the mode on must never hide what the reader has already read.
 * Starting the cursor at the first block would collapse a paper someone is
 * thirty blocks into back to a single paragraph, and reveal mode would read as
 * "lose my place" rather than "slow down from here". So it opens at the block
 * currently in view, or at a saved position when the paper has just loaded,
 * and only moves forward from there.
 */
export function initialRevealCursor(
  blocks: { sequence_order: number }[],
  currentSeq: number | null,
  resumeSeq: number | null,
): number | null {
  if (!blocks.length) return null;
  const at = currentSeq ?? resumeSeq;
  if (at != null && blocks.some((b) => b.sequence_order === at)) return at;
  return blocks[0].sequence_order;
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
