import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from 'react';
import type { DeckMemberKind } from '../lib/personalNotes';

/**
 * The shared furniture every margin card wears.
 *
 * Before this, an AI note and a personal note were told apart by a slightly
 * greener border, which is invisible in peripheral vision, exactly where a
 * margin card lives. Every card now opens with the same strip: a grip to drag
 * it by, a colored dot and one word saying who made it, the paragraph it is
 * anchored to, and a slot for its actions. Same geometry, different color, so
 * the eye can sort a gutter full of cards without reading any of them.
 */

export type CardTone = 'ai' | 'personal' | 'composer' | 'pending' | 'deck';

const TONE_WORD: Record<CardTone, string> = {
  ai: 'Asked',
  personal: 'Note',
  composer: 'Asking',
  pending: 'Asking',
  deck: 'Deck',
};

/** A deck can be dragged and dropped on just like the cards inside it. */
export type DragKind = DeckMemberKind | 'deck';

// ── Drag to stack ───────────────────────────────────────────────────────────

/**
 * Everything a card needs to be dragged onto another card, or be dropped on.
 *
 * Dragging is wired with pointer events rather than the HTML5 drag-and-drop
 * API. HTML5 DnD would hand us a drag image and autoscroll for free, but it
 * does not exist on touch, and this app is meant to be opened from a tablet
 * on the same network, where "drag one note onto another" is exactly the
 * gesture a finger expects to work. Pointer events cover mouse, pen and touch
 * with one path, and let the card itself be the thing that moves.
 */
export interface CardDrag {
  /** This card's identity as a deck member, or the deck's own id. */
  id: string;
  kind: DragKind;
  /** Which card is being dragged right now, anywhere in the reader. */
  activeId: string | null;
  /** Which card the pointer is currently over, while a drag is in flight. */
  hoverId: string | null;
  onStart: (id: string, kind: DragKind) => void;
  onHover: (id: string | null) => void;
  onEnd: () => void;
  /** Stack the dragged card onto this target. */
  onDrop: (targetId: string, targetKind: DragKind) => void;
}

/** How far the pointer must travel before a press becomes a drag. */
const DRAG_THRESHOLD = 5;

export function useCardDrag(drag: CardDrag | null) {
  const rootRef = useRef<HTMLElement | null>(null);
  const gesture = useRef<{
    x: number;
    y: number;
    active: boolean;
    target: { id: string; kind: DragKind } | null;
  } | null>(null);

  const dragging = !!drag && drag.activeId === drag.id;
  const isDropTarget =
    !!drag && drag.activeId !== null && drag.activeId !== drag.id && drag.hoverId === drag.id;

  const release = useCallback(
    (commit: boolean) => {
      const el = rootRef.current;
      if (el) {
        el.style.transform = '';
        el.style.pointerEvents = '';
      }
      const target = gesture.current?.target ?? null;
      gesture.current = null;
      if (commit && target && drag && target.id !== drag.id) {
        drag.onDrop(target.id, target.kind);
      }
      drag?.onEnd();
    },
    [drag],
  );

  const onPointerDown = useCallback((e: PointerEvent<HTMLElement>) => {
    if (!drag || (e.pointerType === 'mouse' && e.button !== 0)) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    gesture.current = { x: e.clientX, y: e.clientY, active: false, target: null };
  }, [drag]);

  const onPointerMove = useCallback(
    (e: PointerEvent<HTMLElement>) => {
      const g = gesture.current;
      if (!g || !drag) return;
      const dx = e.clientX - g.x;
      const dy = e.clientY - g.y;

      if (!g.active) {
        if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
        g.active = true;
        drag.onStart(drag.id, drag.kind);
        // Take the card out of hit-testing so elementFromPoint reports what
        // is *underneath* it rather than the card in your hand.
        if (rootRef.current) rootRef.current.style.pointerEvents = 'none';
      }

      if (rootRef.current) {
        const tilt = Math.max(-2.5, Math.min(2.5, dx / 30));
        rootRef.current.style.transform =
          `translate(${dx}px, ${dy}px) rotate(${tilt}deg) scale(1.015)`;
      }

      const hit = document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest('[data-drag-id]') as HTMLElement | null;
      const id = hit?.dataset.dragId ?? null;
      g.target = id && id !== drag.id ? { id, kind: hit!.dataset.dragKind as DragKind } : null;
      drag.onHover(g.target?.id ?? null);
    },
    [drag],
  );

  const onPointerUp = useCallback(() => {
    if (gesture.current?.active) release(true);
    else gesture.current = null;
  }, [release]);

  const onPointerCancel = useCallback(() => {
    if (gesture.current?.active) release(false);
    else gesture.current = null;
  }, [release]);

  // A callback ref rather than the object: the same hook is spread onto a
  // <div> for decks and an <article> for cards, and only a callback is
  // assignable to both.
  const setRoot = useCallback((el: HTMLElement | null) => {
    rootRef.current = el;
  }, []);

  return {
    dragging,
    isDropTarget,
    /** Spread onto the card root. */
    zoneProps: drag
      ? {
          ref: setRoot,
          'data-drag-id': drag.id,
          'data-drag-kind': drag.kind,
        }
      : {},
    gripProps: drag ? { onPointerDown, onPointerMove, onPointerUp, onPointerCancel } : {},
  };
}

export function CardGrip(props: Record<string, unknown>) {
  return (
    <span
      className="card-grip"
      role="button"
      tabIndex={-1}
      title="Drag onto another card to stack them into a deck"
      {...props}
    >
      <svg viewBox="0 0 10 16" width="10" height="16" aria-hidden="true">
        {[3, 7].map((x) =>
          [3, 8, 13].map((y) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.15" />),
        )}
      </svg>
    </span>
  );
}

export function CardEyebrow({
  tone,
  seq,
  onJump,
  grip,
  right,
  word,
}: {
  tone: CardTone;
  /** The paragraph this card is anchored to. */
  seq: number | null;
  onJump?: (seq: number) => void;
  grip?: ReactNode;
  right?: ReactNode;
  /** Override the default word for this tone (decks say "Deck", etc.). */
  word?: string;
}) {
  return (
    <header className={`card-eyebrow tone-${tone}`}>
      {grip ?? <span className="card-grip is-inert" aria-hidden="true" />}
      <span className="card-dot" aria-hidden="true" />
      <span className="card-tone-word">{word ?? TONE_WORD[tone]}</span>
      {seq != null && (
        <button
          type="button"
          className="card-anchor-ref"
          onClick={() => onJump?.(seq)}
          disabled={!onJump}
          title={onJump ? `Scroll to paragraph ${seq}` : `Paragraph ${seq}`}
        >
          ¶{seq}
        </button>
      )}
      {right && <span className="card-eyebrow-right">{right}</span>}
    </header>
  );
}

/**
 * Clamp tall content behind a fade until asked to open.
 *
 * A four-turn thread with a long answer makes a card most of a screen tall,
 * and because cards stack downward from their anchor, that one card shoves
 * every later note hundreds of pixels past the passage it annotates. Clamping
 * keeps the margin readable; the toggle is only rendered when there is
 * genuinely something hidden.
 */
export function Collapsible({
  max = 300,
  children,
  moreLabel = 'Show more',
  lessLabel = 'Show less',
}: {
  max?: number;
  children: ReactNode;
  moreLabel?: string;
  lessLabel?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const inner = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    // Measure the inner element: the clamp lives on its parent, so this stays
    // the true content height whether open or closed.
    const measure = () => setOverflows(el.scrollHeight > max + 32);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [max]);

  const clamped = overflows && !expanded;

  return (
    <div className="card-collapsible">
      <div
        className={`card-clamp${clamped ? ' is-clamped' : ''}`}
        style={clamped ? { maxHeight: max } : undefined}
      >
        <div ref={inner}>{children}</div>
      </div>
      {overflows && (
        <button
          type="button"
          className="card-more"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? lessLabel : moreLabel}
        </button>
      )}
    </div>
  );
}
