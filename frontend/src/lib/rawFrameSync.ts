/**
 * Reading and driving the scroll position of the raw-snapshot iframe.
 *
 * ⚠ This depends on the iframe being SAME-ORIGIN, which it is by
 * deployment design, in both setups the app supports:
 *   - production: nginx serves the SPA and proxies /api on the same host
 *     (see backend/nginx/9xaipal.conf — "Same-origin is a bonus").
 *   - dev: VITE_API_BASE_URL is unset and Vite proxies /api (see api.ts).
 * The iframe's own sandbox keeps `allow-same-origin`, and the snapshot's
 * CSP is only `script-src 'none'; object-src 'none'` — no `sandbox`
 * directive — so the document keeps that origin rather than an opaque one
 * and the parent can read it.
 *
 * Every function here is defensive about that anyway: a cross-origin
 * contentDocument access throws, and the whole feature is a convenience —
 * a caller that gets `null` back just opens the view at the top, which is
 * exactly the old behaviour.
 */

import { bestMatchIndex, makeAnchor } from './textAnchor';

/** Elements worth considering as a scroll target: block-level text holders.
 *  Not `div`/`section` — an outer wrapper "contains" the anchor text too,
 *  and scrolling to the wrapper is scrolling to the top of the page. */
const BLOCK_SELECTOR = 'p, li, h1, h2, h3, h4, h5, h6, blockquote, pre, td, figcaption';

/** Breathing room above the passage we scroll to, so it doesn't sit flush
 *  against the top edge. */
const SCROLL_TOP_GAP = 24;

/**
 * How far into the viewport an element must reach to count as "where the
 * reader is" when reading the position back.
 *
 * ⚠ Tied to SCROLL_TOP_GAP on purpose, and measured rather than guessed:
 * scrolling a passage to the top leaves the PREVIOUS one peeking into that
 * gap, and if a bare "is it visible at all" test picks that one up, every
 * raw→structured hop lands a block or two earlier than the last. Verified
 * in a real browser against a real snapshot: reading from the top with a
 * centred scroll round-tripped 2/28 passages exactly, and this pairing
 * round-trips 28/28 — at every gap size tried, which is what makes it a
 * relationship rather than a lucky constant.
 */
const READ_TOP_MARGIN = SCROLL_TOP_GAP + 8;

function frameDocument(frame: HTMLIFrameElement | null): Document | null {
  if (!frame) return null;
  try {
    return frame.contentDocument;
  } catch {
    return null; // cross-origin (shouldn't happen — see the note above)
  }
}

/**
 * Scroll the raw snapshot to the first of `anchors` it can find.
 *
 * Several anchors, not one, because a passage in the reader does not always
 * exist in the snapshot at all: a video's caption, for instance, is text
 * this app synthesised from the player's `alt-text` attribute (see
 * article_extraction.py's video splice), so it appears in the structured
 * reading and nowhere in the HTML. Falling through to the next passage
 * lands the reader in the right place instead of giving up and staying at
 * the top. Verified against a real snapshot: 5 of 33 passages were
 * video captions with no counterpart in the HTML.
 *
 * Returns true when one of them was found and scrolled to.
 */
export function scrollRawFrameToAnchor(frame: HTMLIFrameElement | null, anchors: string[]): boolean {
  const doc = frameDocument(frame);
  if (!doc || !anchors.length) return false;

  const elements = Array.from(doc.querySelectorAll<HTMLElement>(BLOCK_SELECTOR));
  if (!elements.length) return false;
  const texts = elements.map((el) => el.textContent || '');

  let index = -1;
  for (const anchor of anchors) {
    if (!anchor) continue;
    index = bestMatchIndex(anchor, texts);
    if (index >= 0) break;
  }
  if (index < 0) return false;

  const target = elements[index];
  // 'start', not 'center' — see READ_TOP_MARGIN: the position has to be
  // readable back off the same view, and "where the reader is" is the top
  // of the viewport on both sides of this sync.
  target.scrollIntoView({ block: 'start' });
  const scroller = doc.scrollingElement || doc.documentElement;
  if (scroller) scroller.scrollTop -= SCROLL_TOP_GAP;
  // A brief tint so the reader can see WHERE they landed — the snapshot is
  // an unfamiliar rendering of text they were just reading elsewhere, and a
  // silent scroll into the middle of it is disorienting. Inline style
  // rather than a class: the snapshot carries none of this app's CSS.
  const previous = target.style.backgroundColor;
  target.style.backgroundColor = 'rgba(255, 213, 79, 0.45)';
  setTimeout(() => {
    target.style.transition = 'background-color 1.2s ease';
    target.style.backgroundColor = previous;
  }, 1400);
  return true;
}

/**
 * The passage currently at the top of the raw snapshot's viewport, as an
 * anchor — what the reader is looking at right now. Returns '' when the
 * frame can't be read or holds nothing block-shaped.
 */
export function readRawFrameAnchor(frame: HTMLIFrameElement | null): string {
  const doc = frameDocument(frame);
  if (!doc) return '';

  const elements = Array.from(doc.querySelectorAll<HTMLElement>(BLOCK_SELECTOR));
  // The first block reaching properly into the viewport: the one the reader
  // is reading, rather than the tail of the one they scrolled past — see
  // READ_TOP_MARGIN for why that distinction is the whole ballgame.
  for (const el of elements) {
    const rect = el.getBoundingClientRect();
    const text = (el.textContent || '').trim();
    if (!text) continue;
    if (rect.bottom > READ_TOP_MARGIN) return makeAnchor(text);
  }
  // Scrolled past everything (or nothing matched): fall back to the last
  // block with text, which is where the reader actually is.
  for (let i = elements.length - 1; i >= 0; i--) {
    const text = (elements[i].textContent || '').trim();
    if (text) return makeAnchor(text);
  }
  return '';
}
