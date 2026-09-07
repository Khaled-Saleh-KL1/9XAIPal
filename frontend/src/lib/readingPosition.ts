/**
 * Where the reader got to, per document.
 *
 * Both readers save this and both restore it, so it lives here rather than in
 * either one: BookReadingView owned the only copy, and the article reader had
 * no notion of a reading position at all — a paper reopened from the library
 * always started at the top, however far in you were.
 *
 * ⚠ The key and shape are BookReadingView's originals, deliberately unchanged.
 * Every book already on a reader's machine has a position stored under
 * `pal:progress:<paperId>` in exactly this form, and quietly moving to a
 * tidier schema would silently reset all of them.
 *
 * A chapter-less document (a paper, or a book being read linearly) stores its
 * position under LINEAR, which is the same `-1` slot the book reader has always
 * used for `chapter?.index ?? -1`. So the two readers share one slot for the
 * same document and neither has to know about the other.
 *
 * localStorage, not the server: this is per-device by nature — where you are
 * on the laptop is not where you are on the phone — and it is written on
 * every block boundary you cross, which is not traffic worth sending. Notes
 * and bookmarks, which DO follow you between devices, live in Postgres.
 * Every access is wrapped: Safari private mode and "block site data" both
 * make localStorage throw rather than return null.
 */

/** The slot for a document with no chapters. */
export const LINEAR = -1;

export type ReadingProgress = {
  lastChapter: number | null;
  seqByChapter: Record<string, number>;
};

const EMPTY: ReadingProgress = { lastChapter: null, seqByChapter: {} };

function keyFor(paperId: string): string {
  return `pal:progress:${paperId}`;
}

export function loadReadingProgress(paperId: string): ReadingProgress {
  try {
    const raw = localStorage.getItem(keyFor(paperId));
    if (raw) {
      const p = JSON.parse(raw);
      return {
        lastChapter: p.lastChapter ?? null,
        seqByChapter: p.seqByChapter || {},
      };
    }
  } catch {
    /* blocked, or bad JSON from an older version */
  }
  return { ...EMPTY, seqByChapter: {} };
}

/** Remember that the reader is at `seq`, in `chapterIndex` (null = linear). */
export function saveReadingPosition(
  paperId: string,
  chapterIndex: number | null,
  seq: number,
): void {
  try {
    const p = loadReadingProgress(paperId);
    p.seqByChapter[String(chapterIndex ?? LINEAR)] = seq;
    p.lastChapter = chapterIndex;
    localStorage.setItem(keyFor(paperId), JSON.stringify(p));
  } catch {
    /* no-op: losing a scroll position is not worth surfacing */
  }
}

/** The stored position, or null when this document has never been opened. */
export function lastReadSequence(
  paperId: string,
  chapterIndex: number | null = null,
): number | null {
  const seq = loadReadingProgress(paperId).seqByChapter[String(chapterIndex ?? LINEAR)];
  return typeof seq === 'number' ? seq : null;
}

/**
 * Whether this document's reader should restore a position on open.
 *
 * Papers and books only. An article is a web page someone imported to read
 * once, usually in a single sitting and often short enough that there is no
 * "where I was" to return to — dropping such a reader into the middle of it
 * on every visit reads as a bug rather than a convenience.
 *
 * ⚠ Anything that is not explicitly an article counts, including a null
 * doc_kind: the column defaults to 'paper' and every document predating the
 * book/paper chooser is already labelled that way, so testing for 'article'
 * is what keeps the whole existing library working.
 */
export function shouldRestorePosition(docKind: string | null | undefined): boolean {
  return (docKind ?? 'paper') !== 'article';
}
