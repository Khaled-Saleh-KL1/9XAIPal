/**
 * A picture inside a model's answer.
 *
 * Two sources reach this, and they behave differently enough to matter:
 *
 *  - a figure, table or equation lifted from a document the reader already
 *    has, served through an authenticated paper-asset endpoint — always loads;
 *  - a picture the model found on the web, hotlinked from wherever it lives.
 *    Hotlink protection is extremely common, so a broken red X would be the
 *    normal case rather than the exception; a failure falls back to a labelled
 *    link to the original instead.
 *
 * Lifted out of ChatPane, where it began. Every AI surface renders through the
 * shared markdown pipeline (see lib/markdown.ts), so a model that can show a
 * picture in the book chat should be able to show one in a margin note, the
 * desk and the research answer too — with the same hotlink handling rather
 * than three divergent copies of it.
 *
 * `referrerPolicy="no-referrer"` on every one of these: an image the model
 * chose is fetched by the reader's browser from a third party, and there is no
 * reason to tell that third party which page the reader is on.
 */
import { useState, type ImgHTMLAttributes } from 'react';
import { getApiMediaUrl } from '../api';

/*
 * ⚠ No click handler here, on purpose. Enlarging is the job of the one
 * delegated ImageLightbox mounted in main.tsx, which catches a click on ANY
 * content image — a figure in the article, a book page, a picture in an
 * answer — and opens the same overlay the structured reader uses. This
 * component used to dispatch its own `pal:lightbox` event and, when nothing
 * answered it (a margin note, the desk), open the image in a NEW TAB — while
 * the delegated listener opened the overlay as well. In the book chat that
 * meant two overlays; in a margin note it meant a new tab (or a blocked
 * pop-up) on top of the overlay: "the picture is not clickable to make it
 * bigger, like in the structured reading" (2026-09-13). One path now.
 */

export const AnswerImage: React.FC<ImgHTMLAttributes<HTMLImageElement>> = ({
  src,
  alt,
  ...rest
}) => {
  const [failed, setFailed] = useState(false);

  if (!src) return null;
  const resolvedSrc = getApiMediaUrl(src);

  if (failed) {
    return (
      <span
        className="block my-3 rounded-md border px-3 py-2 text-[12px] font-mono"
        style={{
          borderColor: 'var(--border)',
          background: 'var(--bg-2)',
          color: 'var(--muted)',
        }}
      >
        <span className="block">Image blocked by source (hotlink protection)</span>
        <a
          href={resolvedSrc}
          target="_blank"
          rel="noreferrer noopener"
          className="underline"
          style={{ color: 'var(--accent)' }}
        >
          Open original image in new tab →
        </a>
        {alt && <span className="block mt-1 opacity-70">{alt}</span>}
      </span>
    );
  }

  return (
    <span className="block my-3">
      <img
        src={resolvedSrc}
        alt={alt || ''}
        loading="lazy"
        referrerPolicy="no-referrer"
        title="Click to enlarge"
        onError={() => setFailed(true)}
        style={{
          maxWidth: '100%',
          maxHeight: 360,
          borderRadius: 6,
          border: '1px solid var(--border)',
          background: 'var(--bg-2)',
          display: 'block',
          margin: '0 auto',
          cursor: 'zoom-in',
        }}
        {...rest}
      />
      {alt && (
        <span
          className="block text-center mt-1 text-[11px] font-mono"
          style={{ color: 'var(--muted)' }}
        >
          {alt}
        </span>
      )}
    </span>
  );
};
