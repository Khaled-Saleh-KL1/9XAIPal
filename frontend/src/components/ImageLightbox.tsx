import { useCallback, useEffect, useState } from 'react';

/**
 * Click any content image to open it full-screen over a blurred page.
 *
 * Deliberately a single delegated listener on `document` rather than an
 * `onClick` threaded through every image render site. Figures, equation
 * fallback images, VLM-rendered book figures and anything inside a markdown
 * body are rendered by five different components, and several of them build
 * their <img> from raw markdown — there is no one place to hook. A delegated
 * listener catches all of them, including images that don't exist yet.
 *
 * Because it is opt-OUT, the exclusion list below is the whole contract:
 * anything not excluded is treated as content worth enlarging.
 */

/** Images that are chrome or a control, not content to be examined. */
const EXCLUDED = [
  // The lightbox's own image. Without this the overlay cannot be closed by
  // clicking it: the backdrop's onClick clears the state and this listener
  // immediately sets it again from the very click that closed it, so the
  // overlay just sits there. Caught in a browser, not by types.
  '.lightbox-backdrop',
  '.paper-cover',      // library card thumbnail — clicking it opens the paper
  '.composer-thumb',   // the little "asking about this" chip in a composer
  'a[href]',           // an image standing in for a link
  'button',
  '[role="button"]',
  '[data-no-lightbox]',
].join(', ');

/**
 * Below this, an image is an icon or an avatar rather than something with
 * numbers to read. Measured against the natural size, not the rendered one:
 * a large figure scaled down into a narrow column is exactly the case this
 * feature exists for.
 */
const MIN_NATURAL_WIDTH = 80;

export function ImageLightbox() {
  const [image, setImage] = useState<{ src: string; alt: string } | null>(null);
  const close = useCallback(() => setImage(null), []);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      // Leave modified clicks alone — those are "open in new tab" and friends.
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
        return;
      }
      const target = e.target as HTMLElement | null;
      if (!target || target.tagName !== 'IMG') return;

      const img = target as HTMLImageElement;
      if (img.closest(EXCLUDED)) return;

      // A broken image has naturalWidth 0 and nothing worth showing.
      if (!img.naturalWidth || img.naturalWidth < MIN_NATURAL_WIDTH) return;

      e.preventDefault();
      setImage({ src: img.currentSrc || img.src, alt: img.alt || '' });
    };

    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, []);

  // Escape closes, and the page behind must not scroll while it's open.
  useEffect(() => {
    if (!image) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [image, close]);

  if (!image) return null;

  return (
    <div
      className="lightbox-backdrop"
      onClick={close}
      role="dialog"
      aria-modal="true"
      aria-label={image.alt || 'Enlarged image'}
    >
      {/* No stopPropagation: clicking the image closes it again, so the whole
          thing behaves as the toggle it looks like. */}
      <img className="lightbox-image" src={image.src} alt={image.alt} />
      <button type="button" className="lightbox-close" onClick={close} aria-label="Close image">
        &times;
      </button>
    </div>
  );
}
