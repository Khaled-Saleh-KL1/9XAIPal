/**
 * Painting note anchors onto the article without touching the DOM.
 *
 * A note's anchor is a quote: the exact text the reader highlighted. To show
 * it again after a reload we have to find that string inside the rendered
 * block and draw on it.
 *
 * We use the CSS Custom Highlight API rather than wrapping matches in <mark>
 * elements. Wrapping means mutating DOM that React owns: the next re-render of
 * that block silently discards the marks, and any node-splitting inside a
 * KaTeX subtree corrupts the equation. Highlight ranges live entirely outside
 * the DOM tree, so React can re-render freely and the highlight survives.
 *
 * Browsers without the API (or blocks where the quote no longer matches, e.g.
 * after a re-chunk) fall back to a block-level tint applied via a class, which
 * is coarse but never wrong.
 */

const HL_ANCHOR = 'note-anchor';
const HL_PERSONAL = 'note-anchor-personal';
const HL_ACTIVE = 'note-anchor-active';

type HighlightRegistry = {
  set(name: string, highlight: unknown): void;
  delete(name: string): void;
};

function registry(): HighlightRegistry | null {
  const css = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS;
  const HighlightCtor = (globalThis as { Highlight?: unknown }).Highlight;
  if (!css?.highlights || typeof HighlightCtor !== 'function') return null;
  return css.highlights;
}

export function highlightsSupported(): boolean {
  return registry() !== null;
}

/** Collapse whitespace runs, keeping a map back to original indices. */
function normalizeWithMap(raw: string): { text: string; map: number[] } {
  const out: string[] = [];
  const map: number[] = [];
  let lastWasSpace = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    const isSpace = /\s/.test(ch);
    if (isSpace) {
      if (lastWasSpace) continue;
      out.push(' ');
      map.push(i);
      lastWasSpace = true;
    } else {
      out.push(ch);
      map.push(i);
      lastWasSpace = false;
    }
  }
  return { text: out.join(''), map };
}

/**
 * Locate `quote` inside `block` and return a Range covering it.
 *
 * Matching is whitespace-insensitive because the quote was captured from
 * rendered text (where a line break is a space) while the DOM may hold the
 * source spacing. Returns null when the quote is not present, since after a
 * re-chunk the anchored text may genuinely be gone.
 */
export function findQuoteRange(block: HTMLElement, quote: string): Range | null {
  const wanted = normalizeWithMap(quote).text.trim();
  if (!wanted) return null;

  // Flatten every text node into one string, remembering where each character
  // came from so a match can be mapped back to (node, offset).
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  const starts: number[] = [];
  let raw = '';
  let node = walker.nextNode() as Text | null;
  while (node) {
    nodes.push(node);
    starts.push(raw.length);
    raw += node.data;
    node = walker.nextNode() as Text | null;
  }
  if (!raw) return null;

  const { text: haystack, map } = normalizeWithMap(raw);
  const at = haystack.indexOf(wanted);
  if (at === -1) return null;

  const rawStart = map[at];
  const rawEnd = map[Math.min(at + wanted.length - 1, map.length - 1)] + 1;

  const locate = (rawIndex: number): { node: Text; offset: number } | null => {
    for (let i = nodes.length - 1; i >= 0; i--) {
      if (starts[i] <= rawIndex) {
        return { node: nodes[i], offset: Math.min(rawIndex - starts[i], nodes[i].data.length) };
      }
    }
    return null;
  };

  const from = locate(rawStart);
  const to = locate(rawEnd);
  if (!from || !to) return null;

  try {
    const range = document.createRange();
    range.setStart(from.node, from.offset);
    range.setEnd(to.node, to.offset);
    return range;
  } catch {
    return null;
  }
}

export interface AnchorSpec {
  noteId: string;
  sequenceId: number;
  quote: string | null;
  /**
   * Who made this mark. Painted in different colors so a glance at the page
   * separates "I wrote this" from "I asked about this" without opening a
   * single card.
   */
  tone: 'ai' | 'personal';
}

/**
 * Repaint every note anchor. Returns the sequence ids whose quote could not be
 * found, so the caller can fall back to tinting those whole blocks.
 */
export function paintAnchors(
  blockFor: (sequenceId: number) => HTMLElement | null | undefined,
  anchors: AnchorSpec[],
  activeNoteId: string | null,
): number[] {
  const highlights = registry();
  const unmatched: number[] = [];
  if (!highlights) {
    return anchors.filter((a) => a.quote).map((a) => a.sequenceId);
  }

  const HighlightCtor = (globalThis as unknown as {
    Highlight: new (...ranges: Range[]) => unknown;
  }).Highlight;

  const ai: Range[] = [];
  const personal: Range[] = [];
  const active: Range[] = [];

  for (const anchor of anchors) {
    if (!anchor.quote) continue;
    const block = blockFor(anchor.sequenceId);
    if (!block) continue;
    const range = findQuoteRange(block, anchor.quote);
    if (!range) {
      unmatched.push(anchor.sequenceId);
      continue;
    }
    if (anchor.noteId === activeNoteId) active.push(range);
    else if (anchor.tone === 'personal') personal.push(range);
    else ai.push(range);
  }

  const paint = (name: string, ranges: Range[]) => {
    if (ranges.length) highlights.set(name, new HighlightCtor(...ranges));
    else highlights.delete(name);
  };

  paint(HL_ANCHOR, ai);
  paint(HL_PERSONAL, personal);
  paint(HL_ACTIVE, active);

  return unmatched;
}

/** Drop all painted anchors: call on unmount so highlights don't leak. */
export function clearAnchors(): void {
  const highlights = registry();
  if (!highlights) return;
  highlights.delete(HL_ANCHOR);
  highlights.delete(HL_PERSONAL);
  highlights.delete(HL_ACTIVE);
}

export interface CapturedSelection {
  sequenceId: number;
  chunkId: string | null;
  quote: string;
  /** Viewport rect of the selection, for placing the "Ask" affordance. */
  rect: DOMRect;
}

/**
 * Read the current selection, if it lies inside an article block.
 *
 * A selection spanning several blocks anchors to the block it STARTS in: that
 * is where the reader's attention began, and it keeps the anchor a single
 * stable sequence id rather than a range that a re-chunk could tear apart.
 */
export function captureSelection(root: HTMLElement): CapturedSelection | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;

  const quote = sel.toString().replace(/\s+/g, ' ').trim();
  if (quote.length < 2) return null;

  const range = sel.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return null;

  let node: Node | null = range.startContainer;
  let block: HTMLElement | null = null;
  while (node && node !== root) {
    if (node instanceof HTMLElement && node.dataset.seq) {
      block = node;
      break;
    }
    node = node.parentNode;
  }
  if (!block) return null;

  return {
    sequenceId: Number(block.dataset.seq),
    chunkId: block.dataset.chunkId || null,
    quote,
    rect: range.getBoundingClientRect(),
  };
}
