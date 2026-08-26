import { useState } from 'react';
import { IconDoc } from '../components/Icons';
import { getCoverUrl } from '../api';

/**
 * A paper's first page, or a placeholder shaped like one.
 *
 * ⚠ The fallback is not optional. The cover endpoint answers 204 when a page
 * cannot be rasterised — no source PDF, a corrupt first page — and a browser
 * reports that to <img> as a load error. Without `failed` state the grid would
 * hold broken-image glyphs in exactly the cases where the paper is otherwise
 * fine.
 *
 * ⚠ It also renders a placeholder while a paper is still processing. The PDF
 * is on disk from the moment of upload so a cover *would* render, but a card
 * that is visibly mid-extraction should not look finished.
 */
export function PaperCover({
  paperId,
  title,
  ready,
  className = '',
}: {
  paperId: string;
  title: string;
  /** False while the paper is still being processed. */
  ready: boolean;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (!ready || failed) {
    return (
      <div className={`paper-cover is-blank ${className}`} aria-hidden="true">
        <IconDoc className="w-5 h-5" style={{ color: 'var(--faint)' }} />
      </div>
    );
  }

  return (
    <div className={`paper-cover ${className}`}>
      <img
        src={getCoverUrl(paperId)}
        alt={`First page of ${title}`}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    </div>
  );
}
