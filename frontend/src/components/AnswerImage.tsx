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

export type LightboxDetail = { src: string; alt?: string };

/**
 * Ask whoever is listening to open this image full-screen.
 *
 * A CustomEvent rather than a prop or a context so the markdown component map
 * can stay at module scope with no React state of its own. The event is
 * `cancelable`: a listener that actually shows an overlay calls
 * `preventDefault()`, and `dispatchEvent` then returns false. That is what
 * lets a surface WITHOUT a lightbox (a margin note, the desk) still do
 * something sensible — open the original in a new tab — instead of the click
 * silently doing nothing.
 */
export function openLightbox(detail: LightboxDetail): void {
  const handled = !window.dispatchEvent(
    new CustomEvent<LightboxDetail>('pal:lightbox', { detail, cancelable: true }),
  );
  if (!handled) window.open(detail.src, '_blank', 'noopener,noreferrer');
}

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
        onClick={() => openLightbox({ src: resolvedSrc, alt: alt || undefined })}
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
