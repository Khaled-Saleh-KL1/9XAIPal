import { useEffect, useState } from 'react';
import type { Paper } from '../types';
import { ArticleReader } from './ArticleReader';
import { BookReadingView } from './BookReadingView';
import { getPaper, type PaperMeta } from '../api';

/**
 * Picks the reader for a document.
 *
 * Papers get the article reader: the whole text at once, with margin notes.
 * Books keep the original chapter-by-chapter reveal reader untouched: a book
 * cannot be rendered in one pass or held in a context window, so neither half
 * of the article experience applies to it.
 *
 * doc_kind is only known after a round-trip, so this holds the frame for one
 * fetch rather than flashing the wrong reader and swapping it out.
 */
export function ReadingView({
  paper,
  paperId,
  jumpToSequence = null,
  onJumped,
  onOpenDesk,
  onBack,
  onOpenRaw,
}: {
  paper: Paper;
  paperId: string;
  /** A block to scroll to on open, handed over by the desk (or by the raw
   * viewer's "Read structured" button, already resolved to a sequence). */
  jumpToSequence?: number | null;
  onJumped?: () => void;
  onOpenDesk?: (scope?: string) => void;
  onBack: () => void;
  /** The reader's own "Raw file" button: opens the source PDF/HTML snapshot
   * at the given page (null when there's no page to preserve, e.g. an
   * article has no pages at all). Needs `meta` (not just `paperId`), so it
   * only fires once metadata has loaded. */
  onOpenRaw?: (meta: PaperMeta, page: number | null) => void;
}) {
  const [meta, setMeta] = useState<PaperMeta | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setMeta(null);
    setFailed(false);
    getPaper(paperId)
      .then((m) => { if (alive) setMeta(m); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [paperId]);

  if (!meta && !failed) {
    return (
      <div className="reader-root">
        <div className="reader-notice">Opening…</div>
      </div>
    );
  }

  if (meta?.doc_kind === 'book') {
    return (
      <BookReadingView
        paper={paper}
        paperId={paperId}
        onBack={onBack}
        jumpToSequence={jumpToSequence}
        onJumped={onJumped}
        onOpenRaw={meta ? (page) => onOpenRaw?.(meta, page) : undefined}
      />
    );
  }

  // A failed metadata fetch falls through to the article reader, which shows
  // the real error from its own load rather than a second generic one here.
  return (
    <ArticleReader
      paperId={paperId}
      fallbackTitle={paper.title}
      jumpToSequence={jumpToSequence}
      onJumped={onJumped}
      onOpenDesk={onOpenDesk}
      onBack={onBack}
      onOpenRaw={meta ? (page) => onOpenRaw?.(meta, page) : undefined}
    />
  );
}
