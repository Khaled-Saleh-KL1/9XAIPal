/**
 * Personal reading state: bookmarks, the reader's own notes, and decks.
 *
 * The shapes here are the reader's, not the wire's — camelCase, timestamps as
 * millisecond numbers, deck members as a flat ordered list. The API speaks
 * snake_case with ISO strings, so everything crosses through the mappers at
 * the bottom of this file rather than leaking a transport shape into the
 * components.
 *
 * This state used to live in localStorage. It is now server-owned; what
 * remains of localStorage here is the one-way migration that carries an
 * existing reader's marks up to the database on first open.
 */

import type {
  MarginSide,
  WireBookmark,
  WireDeck,
  WirePersonalNote,
} from '../api';

export interface PersonalNote {
  id: string;
  anchorSequenceId: number;
  anchorChunkId: string | null;
  quote: string | null;
  body: string;
  marginSide: MarginSide;
  createdAt: number;
  updatedAt: number;
}

/** A place worth coming back to. */
export interface PersonalBookmark {
  id: string;
  sequenceId: number;
  progress: number;
  updatedAt: number;
  /** First ~60 chars of the bookmarked block, cached for display. */
  snippet?: string;
  /** Block kind at the bookmark — lets us label it "on figure" / "on equation". */
  kind?: 'text' | 'figure' | 'equation' | 'table' | 'block';
  /** Page number where the bookmark lives, when known. */
  page?: number | null;
  /** Optional name the reader gave this mark. */
  label?: string | null;
}

/**
 * A stack of cards sharing one slot in the margin.
 *
 * Vertical space in the gutter is the scarcest resource in the reader: cards
 * stack downward from their anchors, so one tall note pushes every later note
 * away from the passage it belongs to. A deck collapses N cards into the
 * height of one, which is the whole point — you trade "see everything at once"
 * for "everything stays where it belongs".
 *
 * A deck owns nothing. Its members are notes that go on existing
 * independently, so spreading a deck leaves every note exactly as it was.
 */
export type DeckMemberKind = 'ai' | 'personal';

export interface DeckMember {
  id: string;
  kind: DeckMemberKind;
}

export interface NoteDeck {
  id: string;
  members: DeckMember[];
  /** Index of the member currently face-up. */
  top: number;
  marginSide: MarginSide;
  label: string | null;
  /** Study mode: show the prompt side first and hide the answer until asked. */
  study: boolean;
}

export interface PersonalState {
  bookmarks: PersonalBookmark[];
  notes: PersonalNote[];
  decks: NoteDeck[];
}

export const EMPTY_PERSONAL_STATE: PersonalState = {
  bookmarks: [],
  notes: [],
  decks: [],
};

export function makeId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Deck ids are sent to the server as UUIDs, so the fallback has to be one
  // too — a timestamp-and-random string is rejected by the route's UUID type.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ── Wire mapping ────────────────────────────────────────────────────────────

const asMillis = (iso: string | null | undefined): number =>
  iso ? Date.parse(iso) || 0 : 0;

export function bookmarkFromWire(b: WireBookmark): PersonalBookmark {
  return {
    id: b.id,
    sequenceId: b.sequence_id,
    progress: b.progress ?? 0,
    updatedAt: asMillis(b.updated_at),
    snippet: b.snippet ?? undefined,
    kind: b.kind,
    page: b.page,
    label: b.label,
  };
}

export function noteFromWire(n: WirePersonalNote): PersonalNote {
  return {
    id: n.id,
    anchorSequenceId: n.anchor_sequence_id,
    anchorChunkId: n.anchor_chunk_id,
    quote: n.anchor_quote,
    body: n.body,
    marginSide: n.margin_side,
    createdAt: asMillis(n.created_at),
    updatedAt: asMillis(n.updated_at),
  };
}

export function deckFromWire(d: WireDeck): NoteDeck {
  return {
    id: d.id,
    members: d.members.map((m) => ({ id: m.id, kind: m.kind })),
    top: d.top,
    marginSide: d.margin_side,
    label: d.label,
    study: d.study,
  };
}

export function deckToWire(d: NoteDeck): WireDeck {
  return {
    id: d.id,
    members: d.members.map((m) => ({ id: m.id, kind: m.kind })),
    top: d.top,
    margin_side: d.marginSide,
    label: d.label,
    study: d.study,
  };
}

// ── Pure deck helpers ───────────────────────────────────────────────────────

export function makeDeck(members: DeckMember[], marginSide: MarginSide): NoteDeck {
  return {
    id: makeId(),
    members,
    top: 0,
    marginSide,
    label: null,
    study: false,
  };
}

/**
 * Drop members whose underlying note is gone, then drop decks that no longer
 * hold two cards.
 *
 * The server enforces the same rule on write — membership rows cascade away
 * with their notes and thin decks are pruned. This is the local mirror of it,
 * applied between an optimistic delete and the response that confirms it, so
 * the margin never renders a stack with a hole in it.
 */
export function reconcileDecks(
  decks: NoteDeck[],
  validAiIds: Set<string>,
  validPersonalIds: Set<string>,
): NoteDeck[] {
  const claimed = new Set<string>();
  const out: NoteDeck[] = [];

  for (const deck of decks) {
    const members = deck.members.filter((m) => {
      // A note can only belong to one deck; first deck wins if data is stale.
      if (claimed.has(m.id)) return false;
      const exists = m.kind === 'ai' ? validAiIds.has(m.id) : validPersonalIds.has(m.id);
      if (exists) claimed.add(m.id);
      return exists;
    });
    if (members.length < 2) continue;
    out.push({
      ...deck,
      members,
      top: Math.min(Math.max(deck.top, 0), members.length - 1),
    });
  }
  return out;
}

/** Which deck, if any, holds this card. */
export function deckHolding(decks: NoteDeck[], cardId: string): NoteDeck | null {
  return decks.find((d) => d.members.some((m) => m.id === cardId)) ?? null;
}

// ── Legacy localStorage migration ───────────────────────────────────────────
//
// Read once per paper, uploaded, then erased. Everything below exists only to
// carry an existing reader's marks across the move to the database, and can be
// deleted once no installation is still holding the old keys.

const legacyKeys = (paperId: string) => ({
  notes: `pal:personal:${paperId}:notes`,
  bookmarks: `pal:personal:${paperId}:bookmarks`,
  bookmarkSingular: `pal:personal:${paperId}:bookmark`,
  decks: `pal:personal:${paperId}:decks`,
});

export interface LegacyState {
  notes: PersonalNote[];
  bookmarks: PersonalBookmark[];
  decks: NoteDeck[];
}

function readJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

/**
 * Whatever this browser still holds for a paper, or null if nothing.
 *
 * Tolerant by design: these rows were written by three different versions of
 * the app (single bookmark, bookmark array, decks) and a reader who upgraded
 * mid-paper should not lose the older ones.
 */
export function readLegacyState(paperId: string): LegacyState | null {
  const keys = legacyKeys(paperId);

  const notes = (readJson<Partial<PersonalNote>[]>(keys.notes) ?? []).filter(
    (n) => typeof n?.anchorSequenceId === 'number' && typeof n?.body === 'string',
  ) as PersonalNote[];

  let bookmarks = (readJson<Partial<PersonalBookmark>[]>(keys.bookmarks) ?? []).filter(
    (b) => typeof b?.sequenceId === 'number',
  ) as PersonalBookmark[];

  if (!bookmarks.length) {
    // The original format: exactly one bookmark, stored as a bare object.
    const single = readJson<Partial<PersonalBookmark>>(keys.bookmarkSingular);
    if (single && typeof single.sequenceId === 'number') {
      bookmarks = [{ ...single, id: single.id ?? makeId() } as PersonalBookmark];
    }
  }

  const decks = (readJson<Partial<NoteDeck>[]>(keys.decks) ?? []).filter(
    (d) => Array.isArray(d?.members) && d!.members!.length >= 2,
  ) as NoteDeck[];

  if (!notes.length && !bookmarks.length && !decks.length) return null;
  return { notes, bookmarks, decks };
}

export function clearLegacyState(paperId: string): void {
  const keys = legacyKeys(paperId);
  for (const key of Object.values(keys)) {
    try {
      localStorage.removeItem(key);
    } catch {
      /* storage blocked — the migration is idempotent, so a retry is harmless */
    }
  }
}
