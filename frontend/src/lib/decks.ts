/**
 * The drag-to-stack rule for note decks, shared by the paper reader's margin
 * (ArticleReader) and the book reader's floating note icons (BookNotes). One
 * pure transformation, because its result is what gets PUT as the document's
 * complete arrangement — it has to be right on its own.
 */
import type { MarginSide } from '../api';
import { makeDeck, type DeckMember, type DeckMemberKind, type NoteDeck } from './personalNotes';

export type { DragKind } from '../views/NoteChrome';
import type { DragKind } from '../views/NoteChrome';

/** A deck of one is not a deck: its survivor goes back to standing alone. */
export function pruneDecks(decks: NoteDeck[]): NoteDeck[] {
  return decks
    .filter((d) => d.members.length >= 2)
    .map((d) => ({ ...d, top: Math.min(Math.max(d.top, 0), d.members.length - 1) }));
}

/**
 * The whole drag-to-stack rule, as one pure transformation.
 *
 * Every combination, card onto card, card onto deck, deck onto card, deck
 * onto deck, collapses to the same sentence: the thing that was already
 * sitting still keeps its place, and the thing that was dragged joins it.
 * Whatever the moving card belonged to before is left without it.
 *
 * Kept out of the component because it is what actually gets written: the
 * result is PUT as the paper's complete arrangement, so it has to be correct
 * on its own rather than as a sequence of state updates.
 *
 * Returns null when the drop no longer makes sense, the target vanished
 * mid-drag, so the caller can leave the arrangement untouched.
 */
export function stackDecks(
  decks: NoteDeck[],
  src: { id: string; kind: DragKind },
  target: { id: string; kind: DragKind },
  targetSide: MarginSide,
): NoteDeck[] | null {
  let next = decks.map((d) => ({ ...d, members: [...d.members] }));

  let moving: DeckMember[];
  if (src.kind === 'deck') {
    const i = next.findIndex((d) => d.id === src.id);
    if (i === -1) return null;
    moving = next[i].members;
    next.splice(i, 1);
  } else {
    moving = [{ id: src.id, kind: src.kind as DeckMemberKind }];
    next = next.map((d) => ({ ...d, members: d.members.filter((m) => m.id !== src.id) }));
  }

  // Dropped on a deck, or on a card that is already inside one.
  const holder =
    next.find((d) => d.id === target.id) ??
    next.find((d) => d.members.some((m) => m.id === target.id));

  if (holder) {
    const have = new Set(holder.members.map((m) => m.id));
    holder.members = [...holder.members, ...moving.filter((m) => !have.has(m.id))];
  } else if (target.kind !== 'deck') {
    next.push(
      makeDeck(
        [{ id: target.id, kind: target.kind as DeckMemberKind }, ...moving],
        targetSide,
      ),
    );
  } else {
    return null;
  }

  return pruneDecks(next);
}
