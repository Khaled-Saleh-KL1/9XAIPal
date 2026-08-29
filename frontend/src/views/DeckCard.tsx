import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  CardEyebrow,
  CardGrip,
  useCardDrag,
  type CardDrag,
} from './NoteChrome';
import type { DeckMemberKind, NoteDeck } from '../lib/personalNotes';

/**
 * A stack of margin cards occupying one card's worth of gutter.
 *
 * The gutter's scarce resource is vertical space: cards are placed at their
 * anchor and then pushed down past each other, so three tall notes on one
 * section drag the third one far below the paragraph it belongs to. A deck
 * trades simultaneous visibility for locality: you see one card at a time,
 * but every card in the stack stays beside the passage that produced it.
 *
 * Decks are built by dragging one card onto another and are stored locally,
 * because they are a reading-desk arrangement rather than a property of the
 * notes themselves: spreading a deck leaves every note exactly as it was.
 */

export interface DeckFace {
  id: string;
  kind: DeckMemberKind;
  /** One-line description, used by the pager tooltips. */
  title: string;
  /** The paragraph this member is anchored to. */
  seq: number;
}

/**
 * Halves of the turn. `out` ends with the card edge-on, so it is short, and it
 * is dead time before anything changes. `in` carries the new face back to
 * flat and gets the longer, eased half, which is the part that reads as
 * weight. Both must match the durations in `deck-flip-*` in index.css.
 */
const FLIP_OUT_MS = 150;
const FLIP_IN_MS = 230;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  );
}

export function DeckCard({
  deck,
  faces,
  renderFace,
  active,
  onFocus,
  onJump,
  onTopChange,
  onSpread,
  onTakeOut,
  onToggleStudy,
  onRename,
  onFlip,
  drag,
}: {
  deck: NoteDeck;
  /** Every member, in stacking order. */
  faces: DeckFace[];
  /**
   * Render the face-up member. `study` is non-null while the deck is in study
   * mode and the answer is still hidden.
   */
  renderFace: (face: DeckFace, study: { revealed: boolean; onReveal: () => void } | null) => ReactNode;
  active: boolean;
  onFocus: () => void;
  onJump: (seq: number) => void;
  onTopChange: (index: number) => void;
  onSpread: () => void;
  /** Pull the face-up card back out into its own slot. */
  onTakeOut: (memberId: string) => void;
  onToggleStudy: () => void;
  onRename: (label: string | null) => void;
  onFlip: (() => void) | null;
  drag: CardDrag | null;
}) {
  const { dragging, isDropTarget, zoneProps, gripProps } = useCardDrag(drag);
  const [renaming, setRenaming] = useState(false);
  const [draftLabel, setDraftLabel] = useState(deck.label ?? '');
  // Revealed is tracked per position, so flipping to the next card in study
  // mode always lands on its prompt rather than inheriting the last reveal.
  const [revealedAt, setRevealedAt] = useState<number | null>(null);
  const labelRef = useRef<HTMLInputElement>(null);

  // The turn: which half is running, which way, and the height held steady
  // across the swap.
  const [flip, setFlip] = useState<{ dir: 1 | -1; phase: 'out' | 'in' } | null>(null);
  const [lockHeight, setLockHeight] = useState<number | null>(null);
  const flipping = useRef(false);
  const faceRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const timers = useRef<number[]>([]);

  // A deck can be spread, or its card taken out, mid-turn. Without this the
  // pending timeout calls setState on an unmounted component.
  useEffect(
    () => () => {
      timers.current.forEach(window.clearTimeout);
      timers.current = [];
    },
    [],
  );

  const count = faces.length;
  const top = Math.min(Math.max(deck.top, 0), Math.max(count - 1, 0));
  const face = faces[top];

  useEffect(() => {
    if (renaming) labelRef.current?.focus({ preventScroll: true });
  }, [renaming]);

  /**
   * Turn the card over, and change what it says while it is edge-on.
   *
   * A physical card does not dissolve into the next one: it rotates until
   * you are looking at its edge, and comes back showing something else. So
   * the swap happens at the midpoint of a two-phase Y rotation, where the
   * card is ~90° to the viewer and effectively invisible. One element does
   * both halves: mounting a second face would double every card's state (a
   * follow-up composer, a collapse toggle) and leave the hidden one in the
   * tab order.
   *
   * The height is pinned for the turn and released on the way out, so a
   * shorter card following a taller one does not snap the whole margin
   * upward while the flip is still running.
   */
  const flipTo = (dir: 1 | -1, apply: () => void) => {
    if (flipping.current) {
      apply();
      return;
    }
    if (prefersReducedMotion()) {
      apply();
      return;
    }
    flipping.current = true;
    setLockHeight(faceRef.current?.offsetHeight ?? null);
    setFlip({ dir, phase: 'out' });

    timers.current.push(
      window.setTimeout(() => {
        apply();
        setFlip({ dir, phase: 'in' });
        // The new content is mounted but not yet measured, so wait a frame,
        // then let the pinned height ease to whatever it actually needs.
        requestAnimationFrame(() => {
          setLockHeight(innerRef.current?.offsetHeight ?? null);
          timers.current.push(
            window.setTimeout(() => {
              setFlip(null);
              setLockHeight(null);
              flipping.current = false;
            }, FLIP_IN_MS),
          );
        });
      }, FLIP_OUT_MS),
    );
  };

  const go = (next: number) => {
    if (count < 2) return;
    const target = ((next % count) + count) % count;
    if (target === top) return;
    // Shortest way round decides which way the card turns, so the last card
    // wrapping to the first still reads as "forward".
    const forward = (target - top + count) % count <= count / 2;
    flipTo(forward ? 1 : -1, () => {
      onTopChange(target);
      setRevealedAt(null);
    });
  };

  if (!face) return null;

  const study = deck.study
    ? {
        revealed: revealedAt === top,
        onReveal: () => flipTo(1, () => setRevealedAt(top)),
      }
    : null;

  const commitRename = () => {
    const trimmed = draftLabel.trim();
    onRename(trimmed || null);
    setRenaming(false);
  };

  return (
    <div
      className={[
        'deck',
        dragging ? 'is-dragging' : '',
        isDropTarget ? 'is-drop-target' : '',
        deck.study ? 'is-study' : '',
        flip ? 'is-turning' : '',
      ].filter(Boolean).join(' ')}
      onMouseEnter={onFocus}
      {...zoneProps}
    >
      {/* The paper peeking out from under the face card. Clicking it advances,
          which is the gesture people already have for a physical stack. */}
      {count > 2 && (
        <button
          type="button"
          className="deck-edge deck-edge-2"
          tabIndex={-1}
          aria-hidden="true"
          onClick={() => go(top + 1)}
        />
      )}
      <button
        type="button"
        className="deck-edge deck-edge-1"
        tabIndex={-1}
        aria-hidden="true"
        onClick={() => go(top + 1)}
      />

      <article
        className={`note-card is-deck tone-${face.kind}${active ? ' is-active' : ''}`}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') { e.preventDefault(); go(top + 1); }
          if (e.key === 'ArrowLeft') { e.preventDefault(); go(top - 1); }
          if (e.key === ' ' && study && !study.revealed) {
            e.preventDefault();
            study.onReveal();
          }
        }}
      >
        <CardEyebrow
          tone="deck"
          seq={face.seq}
          onJump={onJump}
          grip={<CardGrip {...gripProps} />}
          word={deck.label ?? 'Deck'}
          right={
            <span className="deck-pager">
              <button
                type="button"
                className="deck-step"
                onClick={() => go(top - 1)}
                title="Previous card (←)"
              >
                ‹
              </button>
              {count <= 6 ? (
                <span className="deck-dots">
                  {faces.map((f, i) => (
                    <button
                      key={f.id}
                      type="button"
                      className={`deck-dot${i === top ? ' is-on' : ''} tone-${f.kind}`}
                      onClick={() => go(i)}
                      title={f.title}
                      aria-label={f.title}
                    />
                  ))}
                </span>
              ) : (
                <span className="deck-count">{top + 1}/{count}</span>
              )}
              <button
                type="button"
                className="deck-step"
                onClick={() => go(top + 1)}
                title="Next card (→)"
              >
                ›
              </button>
            </span>
          }
        />

        {renaming ? (
          <input
            ref={labelRef}
            className="deck-rename"
            value={draftLabel}
            placeholder="Name this deck…"
            onChange={(e) => setDraftLabel(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
              if (e.key === 'Escape') { setDraftLabel(deck.label ?? ''); setRenaming(false); }
            }}
          />
        ) : null}

        {/* The turn. The wrapper owns the perspective and the pinned height;
            the inner element is what actually rotates. Keying it on the
            phase restarts the animation for each half. */}
        <div
          className="deck-stage"
          style={lockHeight != null ? { height: lockHeight } : undefined}
        >
          <div
            ref={faceRef}
            key={flip ? `${flip.phase}-${top}-${study?.revealed ? 'b' : 'f'}` : `${top}-${study?.revealed ? 'b' : 'f'}`}
            className={[
              'deck-face',
              flip ? `is-${flip.phase}` : '',
              flip ? (flip.dir === 1 ? 'dir-next' : 'dir-prev') : '',
            ].filter(Boolean).join(' ')}
          >
            <div ref={innerRef}>{renderFace(face, study)}</div>
          </div>
        </div>

        <div className="deck-foot">
          <button type="button" onClick={() => onTakeOut(face.id)} title="Move this card out of the deck">
            Take out
          </button>
          <button type="button" onClick={onSpread} title="Break the deck up into separate cards">
            Spread
          </button>
          <button
            type="button"
            className={deck.study ? 'is-on' : ''}
            onClick={onToggleStudy}
            title="Hide each answer until you ask for it"
          >
            Study
          </button>
          <button type="button" onClick={() => { setDraftLabel(deck.label ?? ''); setRenaming(true); }}>
            Rename
          </button>
          {onFlip && (
            <button
              type="button"
              className="note-flip"
              onClick={onFlip}
              title={`Move to the ${deck.marginSide === 'right' ? 'left' : 'right'} margin`}
            >
              {deck.marginSide === 'right' ? '←' : '→'}
            </button>
          )}
        </div>
      </article>
    </div>
  );
}
