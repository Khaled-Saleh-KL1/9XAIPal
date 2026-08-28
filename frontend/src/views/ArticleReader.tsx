import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { IconBack, IconDoc } from '../components/Icons';
import { UserMenuInline } from '../components/UserMenu';
import { ArticleBlock } from './ArticleBlock';
import { AskComposer, type ComposerTarget } from './AskComposer';
import { NoteCardView, PendingNoteCard, type NoteGroup, type PendingNote } from './NoteCard';
import {
  PersonalNoteCard,
  PersonalNoteComposer,
  type PersonalComposerTarget,
} from './PersonalNoteCard';
import { DeckCard, type DeckFace } from './DeckCard';
import { MarginaliaPanel, buildMarginaliaRows } from './MarginaliaPanel';
import type { CardDrag, DragKind } from './NoteChrome';
import {
  captureSelection,
  clearAnchors,
  paintAnchors,
} from '../lib/highlight';
import {
  bookmarkFromWire,
  deckFromWire,
  deckToWire,
  makeDeck,
  makeId,
  noteFromWire,
  reconcileDecks,
  type DeckMember,
  type DeckMemberKind,
  type NoteDeck,
  type PersonalBookmark,
  type PersonalNote,
} from '../lib/personalNotes';
import { loadPersonalState } from '../lib/personalState';
import { formatRelativeTime } from '../lib/time';
import { createPacer } from '../lib/pacer';
import {
  askNoteStream,
  createBookmark as createBookmarkApi,
  createPersonalNote as createPersonalNoteApi,
  deleteBookmark as deleteBookmarkApi,
  deleteNote as deleteNoteApi,
  deletePersonalNote as deletePersonalNoteApi,
  getFullDocument,
  listModels,
  listNotes,
  getStudy,
  listStudies,
  moveNote as moveNoteApi,
  putDecks,
  updatePersonalNote as updatePersonalNoteApi,
  type DocBlock,
  type FullDocument,
  type MarginSide,
  type ModelCatalog,
  type PaperNote,
} from '../api';

/**
 * The paper reading experience: a centred article with a note margin either side.
 *
 * Reading is scrolling. Nothing is revealed, gated, or paced — the whole paper
 * arrives in one request and renders at once.
 *
 * Asking is anchoring. Highlight a passage (or pick a figure or equation) and a
 * composer opens in the margin; the answer streams back into a card beside the
 * thing it is about. There is no chat pane, so a question with no selection
 * anchors to whatever block is at the top of the viewport.
 *
 * ⚠ The article column never moves. It is centred by a symmetric grid
 * (gutter | article | gutter) whose side columns are always present, so adding
 * a note cannot shift the text you are reading — the single most disruptive
 * thing a margin can do to a reader.
 */

/** Vertical breathing room between stacked note cards. */
const NOTE_GAP = 16;
/** Below this, neither margin fits: notes fall back to inline cards. */
const GUTTER_MIN_WIDTH = 1180;
/** Below this, only one margin fits, so every card goes right. */
const BOTH_GUTTERS_MIN_WIDTH = 1560;

type Layout = 'inline' | 'right-only' | 'both';

function layoutFor(width: number): Layout {
  if (width >= BOTH_GUTTERS_MIN_WIDTH) return 'both';
  if (width >= GUTTER_MIN_WIDTH) return 'right-only';
  return 'inline';
}

function truncate(text: string, max: number): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean.length > max ? `${clean.slice(0, max).trimEnd()}…` : clean;
}

/** A deck of one is not a deck: its survivor goes back to standing alone. */
function pruneDecks(decks: NoteDeck[]): NoteDeck[] {
  return decks
    .filter((d) => d.members.length >= 2)
    .map((d) => ({ ...d, top: Math.min(Math.max(d.top, 0), d.members.length - 1) }));
}

/**
 * The whole drag-to-stack rule, as one pure transformation.
 *
 * Every combination — card onto card, card onto deck, deck onto card, deck
 * onto deck — collapses to the same sentence: the thing that was already
 * sitting still keeps its place, and the thing that was dragged joins it.
 * Whatever the moving card belonged to before is left without it.
 *
 * Kept out of the component because it is what actually gets written: the
 * result is PUT as the paper's complete arrangement, so it has to be correct
 * on its own rather than as a sequence of state updates.
 *
 * Returns null when the drop no longer makes sense — the target vanished
 * mid-drag — so the caller can leave the arrangement untouched.
 */
function stackDecks(
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

/**
 * Flatten a flat note list into threads: each root note plus its follow-ups.
 *
 * ⚠ The rootOf walk is guarded against cycles. parent_note_id is a foreign key
 * the server sets, so a cycle should be impossible — but "should be" is doing
 * a lot of work for a loop that renders the reader's margin, and an infinite
 * one hangs the tab rather than dropping a card.
 */
function groupNotes(notes: PaperNote[]): NoteGroup[] {
  const byId = new Map(notes.map((n) => [n.id, n]));
  const rootOf = (n: PaperNote): PaperNote => {
    let cur = n;
    const guard = new Set<string>();
    while (cur.parent_note_id && byId.has(cur.parent_note_id) && !guard.has(cur.id)) {
      guard.add(cur.id);
      cur = byId.get(cur.parent_note_id)!;
    }
    return cur;
  };
  const map = new Map<string, NoteGroup>();
  for (const n of notes) {
    if (n.parent_note_id) continue;
    map.set(n.id, { root: n, replies: [] });
  }
  for (const n of notes) {
    if (!n.parent_note_id) continue;
    const group = map.get(rootOf(n).id);
    if (group) group.replies.push(n);
  }
  return [...map.values()].sort(
    (a, b) =>
      a.root.anchor_sequence_id - b.root.anchor_sequence_id ||
      (a.root.created_at || '').localeCompare(b.root.created_at || ''),
  );
}

let clientIdSeq = 0;

interface Props {
  paperId: string;
  fallbackTitle: string;
  /** A block the desk asked us to open at. Consumed once, then cleared. */
  jumpToSequence?: number | null;
  onJumped?: () => void;
  /** Leave for the desk — the cross-paper surface this reader's panel became. */
  onOpenDesk?: (scope?: string) => void;
  onBack: () => void;
}

export function ArticleReader({
  paperId,
  fallbackTitle,
  jumpToSequence = null,
  onJumped,
  onOpenDesk,
  onBack,
}: Props) {
  const [doc, setDoc] = useState<FullDocument | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notes, setNotes] = useState<PaperNote[]>([]);
  /**
   * Whether the server's notes have arrived. Decks reference note ids, so
   * reconciling them against an empty list before the fetch lands would throw
   * away every deck the reader built.
   */
  const [notesLoaded, setNotesLoaded] = useState(false);
  const [pending, setPending] = useState<PendingNote[]>([]);
  const [composer, setComposer] = useState<ComposerTarget | null>(null);
  const [personalComposer, setPersonalComposer] = useState<PersonalComposerTarget | null>(null);
  const [activeNoteId, setActiveNoteId] = useState<string | null>(null);
  const [tintedBlocks, setTintedBlocks] = useState<Set<number>>(new Set());
  const [progress, setProgress] = useState(0);
  const [panel, setPanel] = useState<'contents' | 'bookmarks' | 'notes' | null>(null);
  /**
   * Which desk scope this paper's corner button opens.
   *
   * A study containing this paper if there is exactly one — that is almost
   * always the context the reader wants back. Two or more is ambiguous, and
   * guessing between them is worse than landing on the library scope with the
   * studies rail right there.
   */
  const [deskScope, setDeskScope] = useState<string | null>(null);
  const deskScopeRef = useRef<string | null>(null);
  deskScopeRef.current = deskScope;
  const [layout, setLayout] = useState<Layout>(() => layoutFor(window.innerWidth));
  const wideEnough = layout !== 'inline';

  /**
   * User-owned state, served by the backend.
   *
   * Cleared during render when the paper changes rather than in the load
   * effect: an effect runs after the commit, so for one paint the new paper
   * would be rendered with the previous paper's marks in its margin.
   */
  const [personalNotes, setPersonalNotes] = useState<PersonalNote[]>([]);
  const [bookmarks, setBookmarks] = useState<PersonalBookmark[]>([]);
  const [decks, setDecks] = useState<NoteDeck[]>([]);
  const [personalLoaded, setPersonalLoaded] = useState(false);
  /**
   * A transient line above the article: what the localStorage migration moved,
   * or why a write did not land. Personal state is the reader's own work, so a
   * failure to save it has to be visible rather than swallowed.
   */
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'error' } | null>(null);
  const [loadedPaper, setLoadedPaper] = useState(paperId);
  if (loadedPaper !== paperId) {
    setLoadedPaper(paperId);
    setPersonalNotes([]);
    setBookmarks([]);
    setDecks([]);
    setPersonalLoaded(false);
    setNotice(null);
    setNotesLoaded(false);
  }

  // Deck writes replace the whole arrangement, so the in-flight value is read
  // from a ref rather than from state: two gestures in quick succession must
  // build on each other, not both on the render that was current when the
  // first one started.
  const decksRef = useRef<NoteDeck[]>([]);
  const deckWriteSeq = useRef(0);

  /** The card being dragged and the card under the pointer, if any. */
  const [dragging, setDragging] = useState<{ id: string; kind: DragKind } | null>(null);
  const [dragHover, setDragHover] = useState<string | null>(null);
  const draggingRef = useRef<{ id: string; kind: DragKind } | null>(null);

  // Model choice. Remembered across sessions so the reader does not re-pick it
  // on every question, but re-validated against the catalog on load — a model
  // can be removed from Ollama between sessions.
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [model, setModel] = useState<string>(
    () => { try { return localStorage.getItem('pal:model') || ''; } catch { return ''; } },
  );
  const chooseModel = useCallback((name: string) => {
    setModel(name);
    try { localStorage.setItem('pal:model', name); } catch { /* storage blocked */ }
  }, []);

  // Floating "Ask" pill shown at the end of a fresh selection.
  const [pill, setPill] = useState<{ top: number; left: number } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLDivElement>(null);
  // Note: the gutters need no refs. Cards are positioned against the ARTICLE's
  // top edge, and both gutters are grid items in the same row as the article,
  // so their origins already coincide.
  const blockRefs = useRef<Map<number, HTMLElement>>(new Map());
  const cardRefs = useRef<Map<string, HTMLElement>>(new Map());

  const registerBlockRef = useCallback((seq: number, el: HTMLElement | null) => {
    if (el) blockRefs.current.set(seq, el);
    else blockRefs.current.delete(seq);
  }, []);

  const blockFor = useCallback(
    (seq: number) => blockRefs.current.get(seq) ?? null,
    [],
  );

  // ── Load the paper and its notes ────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    setDoc(null);
    setLoadError(null);
    blockRefs.current.clear();

    getFullDocument(paperId)
      .then((d) => { if (alive) setDoc(d); })
      .catch((e: Error) => { if (alive) setLoadError(e.message); });

    listNotes(paperId)
      .then((n) => { if (alive) setNotes(n); })
      .catch(() => { /* notes are additive — a failure must not block reading */ })
      .finally(() => { if (alive) setNotesLoaded(true); });

    return () => { alive = false; };
  }, [paperId]);

  // A paper still being extracted has no blocks yet. Poll until it does,
  // rather than making the reader a dead end.
  useEffect(() => {
    if (!doc || doc.blocks.length > 0 || doc.status === 'failed') return;
    const id = setInterval(() => {
      getFullDocument(paperId).then(setDoc).catch(() => {});
    }, 2000);
    return () => clearInterval(id);
  }, [doc, paperId]);

  // Bookmarks, personal notes and decks arrive together — decks reference the
  // other two, so a deck list fetched separately can describe a note that is
  // no longer there.
  useEffect(() => {
    let alive = true;
    loadPersonalState(paperId)
      .then((state) => {
        if (!alive) return;
        setBookmarks(state.bookmarks);
        setPersonalNotes(state.notes);
        setDecks(state.decks);
        decksRef.current = state.decks;
        if (state.migrated) {
          const { notes, bookmarks, decks } = state.migrated;
          const parts = [
            notes && `${notes} note${notes === 1 ? '' : 's'}`,
            bookmarks && `${bookmarks} bookmark${bookmarks === 1 ? '' : 's'}`,
            decks && `${decks} deck${decks === 1 ? '' : 's'}`,
          ].filter(Boolean);
          setNotice({
            tone: 'info',
            text: `Moved ${parts.join(', ')} from this browser to the server — they now follow you to any device.`,
          });
        }
      })
      .catch(() =>
        setNotice({
          tone: 'error',
          text: 'Could not load your bookmarks and notes. The paper still reads, but the margin is empty.',
        }),
      )
      .finally(() => { if (alive) setPersonalLoaded(true); });
    return () => { alive = false; };
  }, [paperId]);

  // The notice is an acknowledgement, not a status bar — it goes on its own.
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), notice.tone === 'error' ? 9000 : 7000);
    return () => clearTimeout(t);
  }, [notice]);

  // Keep the ref in step with anything that sets decks from outside a write
  // (the load above, and the reconcile pass below).
  useEffect(() => { decksRef.current = decks; }, [decks]);

  /**
   * Push a new deck arrangement and adopt whatever the server made of it.
   *
   * Optimistic, because dragging a card onto another should land under your
   * hand rather than after a round trip. The sequence guard drops a response
   * that has already been superseded: a full-collection PUT that resolves out
   * of order would otherwise reinstate an arrangement the reader has moved on
   * from.
   */
  const commitDecks = useCallback(
    async (next: NoteDeck[]) => {
      const previous = decksRef.current;
      const seq = ++deckWriteSeq.current;
      setDecks(next);
      decksRef.current = next;
      try {
        const saved = (await putDecks(paperId, next.map(deckToWire))).map(deckFromWire);
        if (deckWriteSeq.current !== seq) return;
        setDecks(saved);
        decksRef.current = saved;
      } catch {
        if (deckWriteSeq.current !== seq) return;
        setDecks(previous);
        decksRef.current = previous;
      }
    },
    [paperId],
  );

  useEffect(() => {
    let alive = true;
    listModels()
      .then((c) => {
        if (!alive) return;
        setCatalog(c);
        setModel((current) =>
          current && c.models.some((m) => m.name === current) ? current : c.default,
        );
      })
      .catch(() => { /* picker just stays hidden; the default model still answers */ });
    return () => { alive = false; };
  }, []);

  useEffect(() => () => clearAnchors(), []);

  useEffect(() => {
    const onResize = () => setLayout(layoutFor(window.innerWidth));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Which study to hand the corner button, and where to land if the desk sent
  // us to a specific block.
  useEffect(() => {
    let alive = true;
    listStudies()
      .then(async (all) => {
        const holding = [];
        for (const st of all) {
          const detail = await getStudy(st.id).catch(() => null);
          if (detail?.papers.some((x) => x.id === paperId)) holding.push(st.id);
        }
        if (alive) setDeskScope(holding.length === 1 ? holding[0] : null);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [paperId]);

  /**
   * Scroll to the block the desk cited.
   *
   * ⚠ Waits for `doc` — the block elements do not exist until the article has
   * rendered, and a jump fired on mount silently does nothing. `onJumped`
   * clears it in App so a later re-render cannot yank the reader back.
   */
  useEffect(() => {
    if (jumpToSequence == null || !doc) return;
    const t = setTimeout(() => {
      jumpToRef.current(jumpToSequence);
      onJumped?.();
    }, 120);
    return () => clearTimeout(t);
  }, [jumpToSequence, doc, onJumped]);

  // ── Notes grouped into threads: a root plus its follow-ups ──────────────
  //
  // ⚠ Split by scope FIRST. Anchored notes belong in the gutter beside their
  // passage; whole-paper notes belong in the assistant panel. They share a
  // table and an endpoint but never a surface, and a document-scope note
  // carries the first block's sequence id only to satisfy a NOT NULL column —
  // laid out in the margin it would pile onto the paper's title.
  const marginNotes = useMemo(
    () => notes.filter((n) => n.scope !== 'document'),
    [notes],
  );
  const paperNotes_ = useMemo(
    () => notes.filter((n) => n.scope === 'document'),
    [notes],
  );

  const groups = useMemo<NoteGroup[]>(() => groupNotes(marginNotes), [marginNotes]);
  const holisticGroups = useMemo<NoteGroup[]>(() => groupNotes(paperNotes_), [paperNotes_]);

  // The gutter lays out anchored pending cards; the panel owns its own.
  const marginPending = useMemo(
    () => pending.filter((p) => p.scope !== 'document'),
    [pending],
  );

  // ── Decks ───────────────────────────────────────────────────────────────
  //
  // A deck is a local arrangement over notes it does not own, so it is
  // re-validated whenever the underlying notes change rather than kept in
  // lockstep with them. Held until the server's notes land, or the first pass
  // would discard every deck for referencing ids it cannot see yet.
  useEffect(() => {
    if (!notesLoaded || !personalLoaded) return;
    const aiIds = new Set(groups.map((g) => g.root.id));
    const personalIds = new Set(personalNotes.map((n) => n.id));
    setDecks((prev) => {
      const next = reconcileDecks(prev, aiIds, personalIds);
      const same =
        next.length === prev.length &&
        next.every((d, i) => d.members.length === prev[i].members.length && d.id === prev[i].id);
      return same ? prev : next;
    });
  }, [groups, personalNotes, notesLoaded, personalLoaded]);

  const deckMemberIds = useMemo(
    () => new Set(decks.flatMap((d) => d.members.map((m) => m.id))),
    [decks],
  );

  const faceFor = useCallback(
    (member: DeckMember): DeckFace | null => {
      if (member.kind === 'ai') {
        const g = groups.find((x) => x.root.id === member.id);
        if (!g) return null;
        return {
          id: member.id,
          kind: 'ai',
          title: truncate(g.root.question, 70),
          seq: g.root.anchor_sequence_id,
        };
      }
      const n = personalNotes.find((x) => x.id === member.id);
      if (!n) return null;
      return {
        id: member.id,
        kind: 'personal',
        title: truncate(n.body, 70),
        seq: n.anchorSequenceId,
      };
    },
    [groups, personalNotes],
  );

  const facesOf = useCallback(
    (deck: NoteDeck): DeckFace[] =>
      deck.members.map(faceFor).filter((f): f is DeckFace => f !== null),
    [faceFor],
  );

  /** A deck parks at its earliest member, so it never drifts as you flip. */
  const deckSeq = useCallback(
    (deck: NoteDeck): number => {
      const seqs = facesOf(deck).map((f) => f.seq);
      return seqs.length ? Math.min(...seqs) : 0;
    },
    [facesOf],
  );

  const sideForCard = useCallback(
    (id: string, kind: DragKind): MarginSide => {
      if (kind === 'ai') return notes.find((n) => n.id === id)?.margin_side || 'right';
      if (kind === 'personal') return personalNotes.find((n) => n.id === id)?.marginSide || 'right';
      return decks.find((d) => d.id === id)?.marginSide || 'right';
    },
    [notes, personalNotes, decks],
  );

  /**
   * Drop one card (or one deck) onto another and stack them.
   *
   * Every combination collapses to the same rule: the thing that was already
   * sitting still keeps its place in the margin, and the thing that was
   * dragged joins it. Anything the moving card belonged to before is left
   * without it, and a deck that falls below two cards stops being a deck.
   */
  const stackOnto = useCallback(
    (targetId: string, targetKind: DragKind) => {
      const src = draggingRef.current;
      if (!src || src.id === targetId) return;
      const next = stackDecks(
        decksRef.current,
        src,
        { id: targetId, kind: targetKind },
        sideForCard(targetId, targetKind),
      );
      if (next) void commitDecks(next);
    },
    [sideForCard, commitDecks],
  );

  const patchDeck = useCallback(
    (deckId: string, patch: Partial<NoteDeck>) => {
      void commitDecks(
        decksRef.current.map((d) => (d.id === deckId ? { ...d, ...patch } : d)),
      );
    },
    [commitDecks],
  );

  const spreadDeck = useCallback(
    (deckId: string) => {
      void commitDecks(decksRef.current.filter((d) => d.id !== deckId));
    },
    [commitDecks],
  );

  const takeOutOfDeck = useCallback(
    (deckId: string, memberId: string) => {
      void commitDecks(
        pruneDecks(
          decksRef.current.map((d) =>
            d.id === deckId
              ? { ...d, members: d.members.filter((m) => m.id !== memberId) }
              : d,
          ),
        ),
      );
    },
    [commitDecks],
  );

  const dragFor = useCallback(
    (id: string, kind: DragKind): CardDrag => ({
      id,
      kind,
      activeId: dragging?.id ?? null,
      hoverId: dragHover,
      onStart: (dragId, dragKind) => {
        draggingRef.current = { id: dragId, kind: dragKind };
        setDragging({ id: dragId, kind: dragKind });
      },
      onHover: setDragHover,
      onEnd: () => {
        draggingRef.current = null;
        setDragging(null);
        setDragHover(null);
      },
      onDrop: stackOnto,
    }),
    [dragging, dragHover, stackOnto],
  );

  // ── Paint the quote highlights inside the article ───────────────────────
  useEffect(() => {
    if (!doc) return;
    const anchors = [
      ...groups.map((g) => ({
        noteId: g.root.id,
        sequenceId: g.root.anchor_sequence_id,
        quote: g.root.anchor_quote,
        tone: 'ai' as const,
      })),
      ...personalNotes.map((pn) => ({
        noteId: pn.id,
        sequenceId: pn.anchorSequenceId,
        quote: pn.quote,
        tone: 'personal' as const,
      })),
    ];
    // The article has to be laid out before ranges can be found in it.
    const raf = requestAnimationFrame(() => {
      const unmatched = paintAnchors(blockFor, anchors, activeNoteId);
      setTintedBlocks(new Set(unmatched));
    });
    return () => cancelAnimationFrame(raf);
  }, [doc, groups, personalNotes, activeNoteId, blockFor]);

  /** Which margin a card belongs in, honouring what the layout can show. */
  const sideOf = useCallback(
    (side: MarginSide | undefined): MarginSide =>
      layout === 'both' && side === 'left' ? 'left' : 'right',
    [layout],
  );

  // ── Margin layout: park each card beside its anchor, stacking downward ──
  const layoutNotes = useCallback(() => {
    const article = articleRef.current;
    if (!article) return;

    // Narrow window: cards stack under the article in normal flow, so any
    // leftover transform would push them off their own container.
    if (!wideEnough) {
      for (const el of cardRefs.current.values()) el.style.transform = '';
      return;
    }

    const articleTop = article.getBoundingClientRect().top;

    // Each margin is laid out independently — a card on the left must not be
    // pushed down by one on the right.
    const bySide: Record<MarginSide, Array<{ key: string; seq: number }>> = {
      left: [],
      right: [],
    };
    for (const g of groups) {
      if (deckMemberIds.has(g.root.id)) continue;
      bySide[sideOf(g.root.margin_side)].push({
        key: g.root.id,
        seq: g.root.anchor_sequence_id,
      });
    }
    for (const p of marginPending) {
      bySide[sideOf(p.marginSide)].push({ key: p.clientId, seq: p.anchorSequenceId });
    }
    if (composer) {
      bySide[sideOf(composer.marginSide)].push({
        key: 'composer',
        seq: composer.sequenceId,
      });
    }
    if (personalComposer) {
      bySide[sideOf(personalComposer.marginSide)].push({
        key: 'personal-composer',
        seq: personalComposer.sequenceId,
      });
    }
    for (const pn of personalNotes) {
      if (deckMemberIds.has(pn.id)) continue;
      bySide[sideOf(pn.marginSide)].push({
        key: pn.id,
        seq: pn.anchorSequenceId,
      });
    }
    for (const deck of decks) {
      bySide[sideOf(deck.marginSide)].push({ key: `deck:${deck.id}`, seq: deckSeq(deck) });
    }

    for (const side of ['left', 'right'] as MarginSide[]) {
      const entries = bySide[side].sort((a, b) => a.seq - b.seq);
      let cursor = -Infinity;
      for (const entry of entries) {
        const el = cardRefs.current.get(entry.key);
        const block = blockRefs.current.get(entry.seq);
        if (!el || !block) continue;
        const desired = block.getBoundingClientRect().top - articleTop;
        const top = Math.max(desired, cursor);
        el.style.transform = `translateY(${top}px)`;
        cursor = top + el.offsetHeight + NOTE_GAP;
      }
    }
  }, [
    groups,
    marginPending,
    composer,
    personalComposer,
    personalNotes,
    decks,
    deckMemberIds,
    deckSeq,
    wideEnough,
    sideOf,
  ]);

  /**
   * Coalesced re-layout.
   *
   * ⚠ layoutNotes measures with getBoundingClientRect, which forces a
   * synchronous reflow. A streaming answer triggers it from three directions at
   * once — the React update, the ResizeObserver watching the growing card, and
   * the observer watching the article — so calling it directly meant several
   * forced reflows per repaint. Funnelling every request through one animation
   * frame collapses those into a single measure-and-place pass.
   */
  const layoutFrame = useRef(0);
  const requestLayout = useCallback(() => {
    if (layoutFrame.current) return;
    layoutFrame.current = requestAnimationFrame(() => {
      layoutFrame.current = 0;
      layoutNotes();
    });
  }, [layoutNotes]);

  useLayoutEffect(() => {
    // The first placement of a newly mounted card must land before paint,
    // otherwise it flashes at the top of the margin on its way to its anchor.
    layoutNotes();
    return () => {
      if (layoutFrame.current) cancelAnimationFrame(layoutFrame.current);
      layoutFrame.current = 0;
    };
  });

  // Cards grow as answers stream in, cards collapse and expand under the
  // reader's hand, and the article reflows as KaTeX and images settle.
  // Re-measure on all of it rather than guessing at heights.
  useEffect(() => {
    if (!wideEnough) return;
    const ro = new ResizeObserver(requestLayout);
    if (articleRef.current) ro.observe(articleRef.current);
    for (const el of cardRefs.current.values()) ro.observe(el);
    window.addEventListener('resize', requestLayout);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', requestLayout);
    };
  }, [
    requestLayout,
    wideEnough,
    groups.length,
    marginPending.length,
    composer,
    personalNotes.length,
    personalComposer,
    decks,
  ]);

  // ── Where am I? ─────────────────────────────────────────────────────────
  /**
   * The block nearest the top of the viewport.
   *
   * ⚠ Binary search, not a scan. This runs on every scroll frame to keep the
   * Resume chip and the panel's "you are here" marker honest, and the previous
   * linear version read a bounding rect for every block in the paper — several
   * hundred forced reflows per frame on a long document. Blocks are laid out
   * top to bottom in sequence order, so their tops are monotonic and a search
   * costs about ten reads instead.
   */
  const topmostBlock = useCallback(
    (offset = 8): { seq: number; block: DocBlock } | null => {
      const scroller = scrollRef.current;
      const blocks = doc?.blocks;
      if (!scroller || !blocks?.length) return null;
      const targetY = scroller.getBoundingClientRect().top + offset;

      const topAt = (i: number): number | null => {
        // Probe outward if a block has no element yet, so one gap cannot
        // derail the search.
        for (let d = 0; d <= 4; d++) {
          for (const j of [i + d, i - d]) {
            if (j < 0 || j >= blocks.length) continue;
            const el = blockRefs.current.get(blocks[j].sequence_order);
            if (el) return el.getBoundingClientRect().top;
          }
        }
        return null;
      };

      let lo = 0;
      let hi = blocks.length - 1;
      let best = 0;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const top = topAt(mid);
        if (top === null) break;
        if (top <= targetY) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
      }

      // The closest block is either the last one above the line or the first
      // one below it.
      const nextTop = best + 1 < blocks.length ? topAt(best + 1) : null;
      const bestTop = topAt(best);
      const i =
        nextTop !== null && bestTop !== null &&
        Math.abs(nextTop - targetY) < Math.abs(bestTop - targetY)
          ? best + 1
          : best;
      return { seq: blocks[i].sequence_order, block: blocks[i] };
    },
    [doc],
  );

  const [currentSeq, setCurrentSeq] = useState<number | null>(null);
  const scrollFrame = useRef(0);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const max = el.scrollHeight - el.clientHeight;
    setProgress(max > 0 ? Math.min(1, el.scrollTop / max) : 0);
    setPill(null);
    if (scrollFrame.current) return;
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = 0;
      setCurrentSeq(topmostBlock()?.seq ?? null);
    });
  }, [topmostBlock]);

  useEffect(() => () => {
    if (scrollFrame.current) cancelAnimationFrame(scrollFrame.current);
  }, []);

  // After the doc paints, block tops are finally measurable.
  useLayoutEffect(() => {
    setCurrentSeq(topmostBlock()?.seq ?? null);
  }, [doc, topmostBlock]);

  // ── Bookmarks ───────────────────────────────────────────────────────────
  // Build the human-readable preview shown in the panel and the Resume chip. A
  // figure has no prose and an equation has math, so the snippet is a label
  // rather than text in those cases.
  const snippetForBlock = (block: DocBlock, seq: number): string => {
    const ref = `¶${seq}`;
    const t = block.structural_type;
    if (t === 'figure') return `${ref} · figure`;
    if (t === 'math') return `${ref} · equation`;
    if (t === 'table') return `${ref} · table${block.plain_text ? ` · ${truncate(block.plain_text.replace(/\s+/g, ' ').trim(), 44)}` : ''}`;
    const text = (block.plain_text || block.content_markdown || '').replace(/\s+/g, ' ').trim();
    if (!text) return ref;
    return `${ref} · “${truncate(text, 60)}”`;
  };

  const kindForBlock = (block: DocBlock): PersonalBookmark['kind'] => {
    const t = block.structural_type;
    if (t === 'figure') return 'figure';
    if (t === 'math') return 'equation';
    if (t === 'table') return 'table';
    if (t === 'heading' || t === 'paragraph' || t === 'text') return 'text';
    return 'block';
  };

  /**
   * Add a bookmark at `seq`, or lift the one already there.
   *
   * Bookmarking the same block twice is always a mistake, so the second press
   * is read as "actually, not here" rather than silently doing nothing.
   */
  /**
   * Optimistic on both paths. Bookmarking is a reflex — it happens on a
   * keypress mid-scroll — so the ribbon has to appear under the cursor rather
   * than a round trip later. The temporary id is swapped for the real one
   * when the row comes back, and a failure simply takes the mark away again.
   */
  const removeBookmark = useCallback(
    (id: string) => {
      const previous = bookmarks;
      setBookmarks((prev) => prev.filter((b) => b.id !== id));
      // A mark that was never saved has nothing to delete server-side.
      if (id.startsWith('local-')) return;
      void deleteBookmarkApi(paperId, id).catch(() => setBookmarks(previous));
    },
    [bookmarks, paperId],
  );

  const toggleBookmarkAt = useCallback(
    (seq: number) => {
      const existing = bookmarks.find((b) => b.sequenceId === seq);
      if (existing) {
        removeBookmark(existing.id);
        return;
      }

      const block = doc?.blocks.find((b) => b.sequence_order === seq) ?? null;
      const draft: PersonalBookmark = {
        id: `local-${makeId()}`,
        sequenceId: seq,
        progress,
        updatedAt: Date.now(),
        snippet: block ? snippetForBlock(block, seq) : `¶${seq}`,
        kind: block ? kindForBlock(block) : 'block',
        page: block?.page_start ?? null,
        label: null,
      };
      setBookmarks((prev) => [...prev, draft]);

      void createBookmarkApi(paperId, {
        sequence_id: seq,
        snippet: draft.snippet ?? null,
        kind: draft.kind ?? 'block',
        page: draft.page ?? null,
        progress: draft.progress,
      })
        .then((wire) => {
          const saved = bookmarkFromWire(wire);
          setBookmarks((prev) => prev.map((b) => (b.id === draft.id ? saved : b)));
        })
        .catch(() => {
          setBookmarks((prev) => prev.filter((b) => b.id !== draft.id));
        });
    },
    [bookmarks, doc, paperId, progress, removeBookmark],
  );

  const bookmarkHere = useCallback(() => {
    const at = topmostBlock();
    if (at) toggleBookmarkAt(at.seq);
  }, [topmostBlock, toggleBookmarkAt]);

  const bookmarkedSeqs = useMemo(
    () => new Map(bookmarks.map((b) => [b.sequenceId, b.label || b.snippet || `¶${b.sequenceId}`])),
    [bookmarks],
  );
  /** "Resume" means the newest mark when there are several. */
  const resume = useMemo(
    () =>
      bookmarks.length
        ? bookmarks.reduce((a, b) => (b.updatedAt > a.updatedAt ? b : a))
        : null,
    [bookmarks],
  );
  const atResume = resume != null && currentSeq === resume.sequenceId;

  // jumpTo is declared later in this component, so bookmark navigation reads
  // it through a ref that is updated once jumpTo exists.
  const jumpToRef = useRef<(seq: number) => void>(() => {});

  // ── Selection → the "Ask" pill ──────────────────────────────────────────
  useEffect(() => {
    const article = articleRef.current;
    const scroller = scrollRef.current;
    if (!article || !scroller) return;

    const onUp = () => {
      // Let the browser finish committing the selection first.
      setTimeout(() => {
        const cap = captureSelection(article);
        if (!cap) {
          setPill(null);
          return;
        }
        const bounds = scroller.getBoundingClientRect();
        // The group is centred on `left` (see .ask-pill-group's translateX).
        // On a narrow phone a selection near either edge of the column would
        // otherwise centre it partly off-screen — clamp so the full pill
        // group (~260px, three pills) always stays reachable.
        const halfGroupWidth = 130;
        const rawLeft = cap.rect.left - bounds.left + cap.rect.width / 2;
        const left = Math.min(
          Math.max(rawLeft, halfGroupWidth + 8),
          Math.max(halfGroupWidth + 8, bounds.width - halfGroupWidth - 8),
        );
        setPill({
          top: cap.rect.bottom - bounds.top + scroller.scrollTop + 8,
          left,
        });
      }, 10);
    };
    article.addEventListener('mouseup', onUp);
    // A touch selection never fires mouseup — without this, the whole
    // Ask/Note/Bookmark pill (the only way to ask the AI about a passage on
    // a paper) simply never appears on a phone or tablet.
    article.addEventListener('touchend', onUp);
    return () => {
      article.removeEventListener('mouseup', onUp);
      article.removeEventListener('touchend', onUp);
    };
  }, [doc]);

  /**
   * Mirror the server's placement rule so the composer opens where the note
   * will end up. Picking the side only at save time would make the card jump
   * across the page the moment you pressed Ask.
   */
  const suggestSide = useCallback(
    (seq: number): MarginSide => {
      if (layout !== 'both') return 'right';
      const near = groups.filter(
        (g) => Math.abs(g.root.anchor_sequence_id - seq) <= 6,
      );
      const right = near.filter((g) => (g.root.margin_side || 'right') === 'right').length;
      return right > near.length - right ? 'left' : 'right';
    },
    [groups, layout],
  );

  /**
   * Blocks by sequence id.
   *
   * A selection knows only which element it landed in; deciding what that
   * element *is* — and therefore whether the ask should be about a passage or
   * about a whole table — needs the block behind it.
   */
  const blockBySeq = useMemo(() => {
    const map = new Map<number, DocBlock>();
    for (const b of doc?.blocks ?? []) map.set(b.sequence_order, b);
    return map;
  }, [doc]);

  const openComposerForBlock = useCallback(
    (block: DocBlock, kind: 'figure' | 'equation' | 'table') => {
      setComposer({
        sequenceId: block.sequence_order,
        chunkId: block.id,
        kind,
        // For an equation and a table the "quote" is the machine transcription
        // — LaTeX, or the recovered table body — which the agent is told to
        // treat as fallible next to the attached crop. A figure has only its
        // caption to offer.
        quote: kind === 'figure'
          ? block.plain_text || null
          : block.content_markdown || block.plain_text || null,
        imageUrl: block.image_url,
        marginSide: suggestSide(block.sequence_order),
      });
    },
    [suggestSide],
  );

  const openComposerFromSelection = useCallback(() => {
    const article = articleRef.current;
    if (!article) return;
    const cap = captureSelection(article);
    setPill(null);
    if (!cap) return;
    /**
     * ⚠ A selection inside a table asks about the whole table.
     *
     * Dragging across a table yields text like "8.4 12.1 91.2 7B" — the cells
     * the pointer crossed, stripped of the header that says which metric each
     * one is and the row label that says which model. As a quote it is
     * unanswerable, and worse, it is unanswerable in a way that looks
     * answerable. A table is one unit, like a figure: the crop goes to the
     * model, and the reader asks about the thing they were looking at.
     */
    const block = blockBySeq.get(cap.sequenceId);
    if (block?.structural_type === 'table') {
      openComposerForBlock(block, 'table');
      window.getSelection()?.removeAllRanges();
      return;
    }
    setComposer({
      sequenceId: cap.sequenceId,
      chunkId: cap.chunkId,
      kind: 'text',
      quote: cap.quote,
      imageUrl: null,
      marginSide: suggestSide(cap.sequenceId),
    });
    window.getSelection()?.removeAllRanges();
  }, [suggestSide, blockBySeq, openComposerForBlock]);

  const openPersonalComposerFromSelection = useCallback(() => {
    const article = articleRef.current;
    if (!article) return;
    const cap = captureSelection(article);
    setPill(null);
    if (!cap) return;
    // Same rule as asking: a note written against three loose cell values is
    // a note that means nothing when you come back to it.
    const block = blockBySeq.get(cap.sequenceId);
    const isTable = block?.structural_type === 'table';
    setPersonalComposer({
      sequenceId: cap.sequenceId,
      chunkId: cap.chunkId,
      kind: isTable ? 'table' : 'text',
      quote: isTable ? block!.plain_text || null : cap.quote,
      imageUrl: isTable ? block!.image_url : null,
      marginSide: suggestSide(cap.sequenceId),
    });
    window.getSelection()?.removeAllRanges();
  }, [suggestSide, blockBySeq]);

  const bookmarkFromSelection = useCallback(() => {
    const article = articleRef.current;
    if (!article) return;
    const cap = captureSelection(article);
    setPill(null);
    if (!cap) return;
    toggleBookmarkAt(cap.sequenceId);
    window.getSelection()?.removeAllRanges();
  }, [toggleBookmarkAt]);

  /** Anchor to whatever block is nearest the top of the viewport. */
  const openComposerAtViewport = useCallback(() => {
    const at = topmostBlock(80);
    if (!at) return;
    setComposer({
      sequenceId: at.seq,
      chunkId: at.block.id,
      kind: 'block',
      quote: null,
      imageUrl: null,
      marginSide: suggestSide(at.seq),
    });
  }, [topmostBlock, suggestSide]);

  const openPersonalComposerAtViewport = useCallback(() => {
    const at = topmostBlock(80);
    if (!at) return;
    setPersonalComposer({
      sequenceId: at.seq,
      chunkId: at.block.id,
      kind: 'block',
      quote: null,
      imageUrl: null,
      marginSide: suggestSide(at.seq),
    });
  }, [topmostBlock, suggestSide]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === 'TEXTAREA' ||
          target.tagName === 'INPUT' ||
          target.isContentEditable);
      if (typing) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === 'a') {
        e.preventDefault();
        const cap = articleRef.current ? captureSelection(articleRef.current) : null;
        if (cap) openComposerFromSelection();
        else openComposerAtViewport();
      }
      if (e.key === 'n') {
        e.preventDefault();
        const cap = articleRef.current ? captureSelection(articleRef.current) : null;
        if (cap) openPersonalComposerFromSelection();
        else openPersonalComposerAtViewport();
      }
      if (e.key === 'b') {
        // Bookmark reuses the same "topmost block" rule as the bar's button.
        // Selection wins if there is one — a bookmark over a passage is a more
        // specific intent than "wherever I'm looking."
        e.preventDefault();
        const cap = articleRef.current ? captureSelection(articleRef.current) : null;
        if (cap) bookmarkFromSelection();
        else bookmarkHere();
      }
      if (e.key === 'i') {
        e.preventDefault();
        setPanel((p) => (p ? null : 'contents'));
      }
      if (e.key === 'p') {
        e.preventDefault();
        onOpenDesk?.(deskScopeRef.current ?? 'library');
      }
      if (e.key === 'Escape') {
        setComposer(null);
        setPersonalComposer(null);
        setPill(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [
    openComposerFromSelection,
    openComposerAtViewport,
    openPersonalComposerFromSelection,
    openPersonalComposerAtViewport,
    bookmarkFromSelection,
    bookmarkHere,
  ]);

  // ── Asking ──────────────────────────────────────────────────────────────
  const runNote = useCallback(
    async (draft: PendingNote, anchor: {
      kind: 'text' | 'figure' | 'equation' | 'table' | 'block' | 'document';
      sequence_id: number;
      chunk_id: string | null;
      quote: string | null;
      image_url: string | null;
    }) => {
      setPending((prev) => [...prev.filter((p) => p.clientId !== draft.clientId), draft]);
      const patch = (fn: (p: PendingNote) => PendingNote) =>
        setPending((prev) => prev.map((p) => (p.clientId === draft.clientId ? fn(p) : p)));

      // Tokens arrive in uneven bursts (see lib/pacer.ts). Feed them to the
      // pacer rather than to React, so the card paints at a readable rate
      // instead of mirroring the model's stutter.
      const pacer = createPacer((revealed) =>
        patch((p) => ({ ...p, answer: revealed, status: null })),
      );

      try {
        await askNoteStream(
          paperId,
          draft.question,
          anchor,
          draft.parentNoteId,
          {
            onCreated: (noteId) => patch((p) => ({ ...p, noteId })),
            onStatus: (message) => patch((p) => ({ ...p, status: message })),
            // ⚠ Upsert by id, never append. Every call arrives twice —
            // `running` when the agent announces it, `done` when it returns —
            // and appending would show each fetch as two rows, the first one
            // spinning forever.
            // ⚠ Clear the status line as well. Once a fetch is on screen the
            // trail IS the activity indicator, and the phase message that
            // preceded it would otherwise sit under finished rows still
            // claiming to be what is happening now.
            onStep: (step) =>
              patch((p) => ({
                ...p,
                status: null,
                steps: p.steps.some((s) => s.id === step.id)
                  ? p.steps.map((s) => (s.id === step.id ? step : s))
                  : [...p.steps, step],
              })),
            onToken: (text) => pacer.push(text),
          },
          undefined,
          draft.marginSide,
          // Only meaningful for a new note. The server ignores this on
          // follow-ups and uses the parent's model instead.
          draft.model,
        );
        // Let the pacer finish painting before the card is swapped for the
        // saved one — otherwise the last few words would be skipped over.
        await pacer.finish();
        // Refetch rather than splicing the response in: the server is the
        // authority on the note's final shape (citations, retrieval mode, and
        // the ordering the gutter lays out by).
        const fresh = await listNotes(paperId);
        setNotes(fresh);
        setPending((prev) => prev.filter((p) => p.clientId !== draft.clientId));
      } catch (e) {
        pacer.cancel();
        patch((p) => ({ ...p, error: (e as Error).message || 'Could not answer that.' }));
      }
    },
    [paperId],
  );

  /**
   * Written to the server before it appears in the margin.
   *
   * The one non-optimistic mutation here: a new note has no id until the
   * server gives it one, and a card rendered under a temporary id cannot be
   * dragged into a deck — deck membership is a foreign key. Waiting is
   * invisible against a local backend, and the composer stays open until the
   * save lands, so a failure loses nothing the reader typed.
   */
  const savingNote = useRef(false);
  const submitPersonalNote = useCallback(
    async (body: string) => {
      if (!personalComposer || savingNote.current) return;
      const target = personalComposer;
      savingNote.current = true;
      try {
        const saved = noteFromWire(
          await createPersonalNoteApi(paperId, {
            anchor_sequence_id: target.sequenceId,
            body,
            anchor_quote: target.quote,
            margin_side: target.marginSide,
          }),
        );
        setPersonalNotes((prev) => [...prev, saved]);
        setPersonalComposer(null);
      } catch {
        // The composer stays open with the text still in it.
        setNotice({
          tone: 'error',
          text: 'That note could not be saved. Your text is still in the composer.',
        });
      } finally {
        savingNote.current = false;
      }
    },
    [personalComposer, paperId],
  );

  const updatePersonalNote = useCallback(
    (id: string, body: string) => {
      const previous = personalNotes;
      setPersonalNotes((prev) =>
        prev.map((n) => (n.id === id ? { ...n, body, updatedAt: Date.now() } : n)),
      );
      void updatePersonalNoteApi(paperId, id, { body }).catch(() =>
        setPersonalNotes(previous),
      );
    },
    [personalNotes, paperId],
  );

  const removePersonalNote = useCallback(
    (id: string) => {
      const previous = personalNotes;
      setPersonalNotes((prev) => prev.filter((n) => n.id !== id));
      void deletePersonalNoteApi(paperId, id).catch(() => setPersonalNotes(previous));
    },
    [personalNotes, paperId],
  );

  const flipPersonalNote = useCallback(
    (id: string) => {
      const note = personalNotes.find((n) => n.id === id);
      if (!note) return;
      const next: MarginSide = note.marginSide === 'right' ? 'left' : 'right';
      setPersonalNotes((prev) =>
        prev.map((n) => (n.id === id ? { ...n, marginSide: next } : n)),
      );
      void updatePersonalNoteApi(paperId, id, { margin_side: next }).catch(() => {
        setPersonalNotes((prev) =>
          prev.map((n) => (n.id === id ? { ...n, marginSide: note.marginSide } : n)),
        );
      });
    },
    [personalNotes, paperId],
  );

  const submitComposer = useCallback(
    (question: string) => {
      if (!composer) return;
      const draft: PendingNote = {
        clientId: `pending-${++clientIdSeq}`,
        noteId: null,
        anchorSequenceId: composer.sequenceId,
        anchorKind: composer.kind,
        quote: composer.quote,
        imageUrl: composer.imageUrl,
        question,
        answer: '',
        status: null,
        steps: [],
        error: null,
        parentNoteId: null,
        scope: 'anchor',
        marginSide: composer.marginSide,
        model: model || null,
      };
      const anchor = {
        kind: composer.kind,
        sequence_id: composer.sequenceId,
        chunk_id: composer.chunkId,
        quote: composer.quote,
        image_url: composer.imageUrl,
      };
      setComposer(null);
      void runNote(draft, anchor);
    },
    [composer, model, runNote],
  );

  const submitFollowUp = useCallback(
    (parentNoteId: string, question: string) => {
      const parent = notes.find((n) => n.id === parentNoteId);
      if (!parent) return;
      const draft: PendingNote = {
        clientId: `pending-${++clientIdSeq}`,
        noteId: null,
        anchorSequenceId: parent.anchor_sequence_id,
        anchorKind: parent.anchor_kind,
        quote: parent.anchor_quote,
        imageUrl: parent.anchor_image_path
          ? `/static/images/${parent.anchor_image_path}`
          : null,
        question,
        answer: '',
        status: null,
        steps: [],
        error: null,
        parentNoteId,
        // A follow-up belongs to the same surface as its parent — a follow-up
        // to a whole-paper question stays in the panel.
        scope: parent.scope,
        // A follow-up joins its parent's card, so it must share its margin.
        marginSide: parent.margin_side || 'right',
        // Shown while it streams. The server independently enforces this from
        // the stored note, so a stale client cannot switch models mid-thread.
        model: parent.requested_model || parent.model,
      };
      void runNote(draft, {
        kind: parent.anchor_kind,
        sequence_id: parent.anchor_sequence_id,
        chunk_id: parent.anchor_chunk_id,
        quote: parent.anchor_quote,
        image_url: parent.anchor_image_path
          ? `/static/images/${parent.anchor_image_path}`
          : null,
      });
    },
    [notes, runNote],
  );

  const removeNote = useCallback(
    async (noteId: string) => {
      setNotes((prev) => prev.filter((n) => n.id !== noteId && n.parent_note_id !== noteId));
      try {
        await deleteNoteApi(paperId, noteId);
      } catch {
        setNotes(await listNotes(paperId).catch(() => notes));
      }
    },
    [paperId, notes],
  );

  /** Move a saved note to the other margin, optimistically. */
  const flipNote = useCallback(
    async (noteId: string) => {
      const note = notes.find((n) => n.id === noteId);
      if (!note) return;
      const next: MarginSide = note.margin_side === 'right' ? 'left' : 'right';
      setNotes((prev) =>
        prev.map((n) => (n.id === noteId ? { ...n, margin_side: next } : n)),
      );
      try {
        await moveNoteApi(paperId, noteId, next);
      } catch {
        setNotes((prev) =>
          prev.map((n) => (n.id === noteId ? { ...n, margin_side: note.margin_side } : n)),
        );
      }
    },
    [notes, paperId],
  );

  const jumpTo = useCallback((seq: number) => {
    const el = blockRefs.current.get(seq);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('is-flash');
    setTimeout(() => el.classList.remove('is-flash'), 1200);
  }, []);

  useEffect(() => {
    jumpToRef.current = jumpTo;
  }, [jumpTo]);

  const jumpToResume = useCallback(() => {
    if (resume) jumpToRef.current(resume.sequenceId);
  }, [resume]);

  // Clicking a [[42]] citation link inside a note scrolls the article.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const anchor = (e.target as HTMLElement)?.closest?.('a[href^="#blk-"]');
      if (!anchor) return;
      e.preventDefault();
      jumpTo(Number(anchor.getAttribute('href')!.replace('#blk-', '')));
    };
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, [jumpTo]);

  const registerCardRef = useCallback((key: string, el: HTMLElement | null) => {
    if (el) cardRefs.current.set(key, el);
    else cardRefs.current.delete(key);
  }, []);

  const clearBookmarkAt = useCallback((seq: number) => {
    setBookmarks((prev) => prev.filter((b) => b.sequenceId !== seq));
  }, []);

  // ── Render ──────────────────────────────────────────────────────────────
  const title = doc?.title || fallbackTitle;

  // Cards are rendered into whichever margin they belong to. Below the
  // two-gutter breakpoint sideOf() collapses everything to the right, so a
  // note saved on the left is still reachable on a narrow window.
  const canFlip = layout === 'both';

  // ⚠ `groups` (margin) AND `holisticGroups` (whole-paper). The desk is where
  // new whole-paper questions are asked now, but notes from before that move
  // still belong to this paper, and dropping them from the index would make
  // them unreachable rather than merely relocated.
  const marginaliaRows = useMemo(
    () => buildMarginaliaRows([...groups, ...holisticGroups], personalNotes, decks),
    [groups, holisticGroups, personalNotes, decks],
  );

  /** Where each bookmark sits along the paper, 0..1, for the progress rail. */
  const bookmarkTicks = useMemo(() => {
    const blocks = doc?.blocks;
    if (!blocks?.length) return [];
    const index = new Map(blocks.map((b, i) => [b.sequence_order, i]));
    const span = Math.max(blocks.length - 1, 1);
    return bookmarks
      .map((b) => {
        const i = index.get(b.sequenceId);
        return i == null ? null : { bookmark: b, at: i / span };
      })
      .filter((t): t is { bookmark: PersonalBookmark; at: number } => t !== null);
  }, [bookmarks, doc]);

  const renderAiCard = (group: NoteGroup, opts: { inDeck?: boolean; study?: { revealed: boolean; onReveal: () => void } | null } = {}) => (
    <NoteCardView
      group={group}
      active={activeNoteId === group.root.id}
      onFocus={() => setActiveNoteId(group.root.id)}
      onJump={jumpTo}
      onDelete={removeNote}
      onFollowUp={submitFollowUp}
      onFlip={canFlip && !opts.inDeck ? () => void flipNote(group.root.id) : null}
      drag={opts.inDeck ? null : dragFor(group.root.id, 'ai')}
      inDeck={opts.inDeck}
      study={opts.study ?? null}
    />
  );

  const renderPersonalCard = (
    note: PersonalNote,
    opts: { inDeck?: boolean; study?: { revealed: boolean; onReveal: () => void } | null } = {},
  ) => (
    <PersonalNoteCard
      note={note}
      active={activeNoteId === note.id}
      onFocus={() => setActiveNoteId(note.id)}
      onJump={jumpTo}
      onDelete={removePersonalNote}
      onEdit={updatePersonalNote}
      onFlip={canFlip && !opts.inDeck ? () => flipPersonalNote(note.id) : null}
      drag={opts.inDeck ? null : dragFor(note.id, 'personal')}
      inDeck={opts.inDeck}
      study={opts.study ?? null}
    />
  );

  const cardsFor = (side: MarginSide) => (
    <>
      {groups
        .filter((g) => !deckMemberIds.has(g.root.id) && sideOf(g.root.margin_side) === side)
        .map((group) => (
          <div
            key={group.root.id}
            className="note-slot"
            ref={(el) => registerCardRef(group.root.id, el)}
          >
            {renderAiCard(group)}
          </div>
        ))}

      {marginPending
        .filter((p) => sideOf(p.marginSide) === side)
        .map((p) => (
          <div
            key={p.clientId}
            className="note-slot"
            ref={(el) => registerCardRef(p.clientId, el)}
          >
            <PendingNoteCard
              note={p}
              onJump={jumpTo}
              onRetry={() => {
                const retry = { ...p, error: null, answer: '', status: null, steps: [] };
                void runNote(retry, {
                  kind: p.anchorKind as 'text' | 'figure' | 'equation' | 'table' | 'block',
                  sequence_id: p.anchorSequenceId,
                  chunk_id: null,
                  quote: p.quote,
                  image_url: p.imageUrl,
                });
              }}
              onDismiss={() =>
                setPending((prev) => prev.filter((x) => x.clientId !== p.clientId))
              }
            />
          </div>
        ))}

      {composer && sideOf(composer.marginSide) === side && (
        <div className="note-slot" ref={(el) => registerCardRef('composer', el)}>
          <AskComposer
            target={composer}
            onSubmit={submitComposer}
            onCancel={() => setComposer(null)}
            catalog={catalog}
            model={model}
            onModelChange={chooseModel}
            onFlip={
              canFlip
                ? () =>
                    setComposer((c) =>
                      c
                        ? { ...c, marginSide: c.marginSide === 'right' ? 'left' : 'right' }
                        : c,
                    )
                : null
            }
          />
        </div>
      )}

      {personalNotes
        .filter((pn) => !deckMemberIds.has(pn.id) && sideOf(pn.marginSide) === side)
        .map((pn) => (
          <div
            key={pn.id}
            className="note-slot"
            ref={(el) => registerCardRef(pn.id, el)}
          >
            {renderPersonalCard(pn)}
          </div>
        ))}

      {decks
        .filter((d) => sideOf(d.marginSide) === side)
        .map((deck) => (
          <div
            key={deck.id}
            className="note-slot"
            ref={(el) => registerCardRef(`deck:${deck.id}`, el)}
          >
            <DeckCard
              deck={deck}
              faces={facesOf(deck)}
              active={activeNoteId === deck.id}
              onFocus={() => setActiveNoteId(deck.id)}
              onJump={jumpTo}
              onTopChange={(top) => patchDeck(deck.id, { top })}
              onSpread={() => spreadDeck(deck.id)}
              onTakeOut={(memberId) => takeOutOfDeck(deck.id, memberId)}
              onToggleStudy={() => patchDeck(deck.id, { study: !deck.study })}
              onRename={(label) => patchDeck(deck.id, { label })}
              onFlip={
                canFlip
                  ? () =>
                      patchDeck(deck.id, {
                        marginSide: deck.marginSide === 'right' ? 'left' : 'right',
                      })
                  : null
              }
              drag={dragFor(deck.id, 'deck')}
              renderFace={(face, study) => {
                if (face.kind === 'ai') {
                  const group = groups.find((g) => g.root.id === face.id);
                  return group ? renderAiCard(group, { inDeck: true, study }) : null;
                }
                const note = personalNotes.find((n) => n.id === face.id);
                return note ? renderPersonalCard(note, { inDeck: true, study }) : null;
              }}
            />
          </div>
        ))}

      {personalComposer && sideOf(personalComposer.marginSide) === side && (
        <div className="note-slot" ref={(el) => registerCardRef('personal-composer', el)}>
          <PersonalNoteComposer
            target={personalComposer}
            onSubmit={submitPersonalNote}
            onCancel={() => setPersonalComposer(null)}
            onFlip={
              canFlip
                ? () =>
                    setPersonalComposer((c) =>
                      c
                        ? { ...c, marginSide: c.marginSide === 'right' ? 'left' : 'right' }
                        : c,
                    )
                : null
            }
          />
        </div>
      )}
    </>
  );

  const bookmarkedHere = currentSeq != null && bookmarkedSeqs.has(currentSeq);

  return (
    <div className={`reader-root${dragging ? ' is-dragging-card' : ''}`}>
      <header className="reader-bar">
        <button onClick={onBack} className="reader-back">
          <IconBack className="w-3.5 h-3.5" />
          <span>Library</span>
        </button>
        <span className="reader-sep" />
        <IconDoc className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--muted)' }} />
        <span className="reader-title">{title}</span>

        <div className="reader-bar-right">
          <button
            className={`reader-chip is-mark${bookmarkedHere ? ' is-on' : ''}`}
            onClick={bookmarkHere}
            title={
              bookmarkedHere
                ? 'Remove the bookmark here (B)'
                : 'Bookmark where you are — select text first to mark that passage (B)'
            }
          >
            <svg className="chip-glyph" viewBox="0 0 12 16" width="10" height="13" aria-hidden="true">
              <path d="M1 1h10v14l-5-4-5 4z" />
            </svg>
            {bookmarkedHere ? 'Marked' : 'Bookmark'}
          </button>

          {resume && (
            <button
              className={`reader-chip is-resume${atResume ? ' is-here' : ''}`}
              onClick={atResume ? undefined : jumpToResume}
              disabled={atResume}
              title={
                atResume
                  ? `You're at your latest bookmark · saved ${formatRelativeTime(resume.updatedAt)}`
                  : `Resume at ${resume.snippet ?? `¶${resume.sequenceId}`} · saved ${formatRelativeTime(resume.updatedAt)}`
              }
            >
              {atResume ? (
                <>
                  <span className="chip-dot" aria-hidden="true" />
                  You're here
                </>
              ) : (
                <>
                  <span className="chip-arrow" aria-hidden="true">↧</span>
                  <span className="chip-text">
                    {truncate(resume.label || resume.snippet || `¶${resume.sequenceId}`, 34)}
                  </span>
                  <span className="chip-when">{formatRelativeTime(resume.updatedAt)}</span>
                </>
              )}
            </button>
          )}

          <button
            className={`reader-chip${panel ? ' is-on' : ''}`}
            onClick={() => setPanel((p) => (p ? null : 'contents'))}
            title="Contents, bookmarks and notes (I)"
          >
            Contents
            {(bookmarks.length > 0 || marginaliaRows.length > 0) && (
              <span className="chip-badge">{bookmarks.length + marginaliaRows.length}</span>
            )}
          </button>

          <span className="reader-meta">{Math.round(progress * 100)}%</span>
          <span className="reader-sep" />
          <UserMenuInline />
        </div>

        {/* The rail doubles as a map: how far in you are, and where every mark
            sits in the paper. Reaching a bookmark you forgot about is the
            common failure of a single "resume" pointer. */}
        <div className="reader-rail">
          <div className="reader-progress" style={{ width: `${progress * 100}%` }} />
          {bookmarkTicks.map(({ bookmark, at }) => (
            <button
              key={bookmark.id}
              className="rail-tick"
              style={{ left: `${at * 100}%` }}
              onClick={() => jumpTo(bookmark.sequenceId)}
              title={`${bookmark.label || bookmark.snippet || `¶${bookmark.sequenceId}`} · saved ${formatRelativeTime(bookmark.updatedAt)}`}
              aria-label={`Jump to bookmark at paragraph ${bookmark.sequenceId}`}
            />
          ))}
        </div>
      </header>

      <MarginaliaPanel
        open={panel !== null}
        tab={panel ?? 'contents'}
        onTabChange={setPanel}
        onClose={() => setPanel(null)}
        outline={doc?.outline ?? []}
        bookmarks={bookmarks}
        rows={marginaliaRows}
        onJump={jumpTo}
        onRemoveBookmark={removeBookmark}
        onAddBookmark={bookmarkHere}
        currentSeq={currentSeq}
      />

      {notice && (
        <div className={`reader-toast${notice.tone === 'error' ? ' is-error' : ''}`}>
          <span>{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <div className="reader-scroll thin-scroll" ref={scrollRef} onScroll={onScroll}>
        {loadError && <div className="reader-notice is-error">{loadError}</div>}

        {doc && doc.blocks.length === 0 && !loadError && (
          <div className="reader-notice">
            {doc.status === 'failed'
              ? 'Extraction failed for this paper.'
              : 'Extracting this paper… the text appears here as soon as it is ready.'}
          </div>
        )}

        <div className={`reader-layout layout-${layout}`}>
          {/* Rendered in both wide tiers. In 'right-only' it holds no cards
              but still occupies its grid column, which is what keeps the
              article centred instead of pushed left by the right margin. */}
          {layout !== 'inline' && (
            <aside className="reader-gutter gutter-left">
              {layout === 'both' ? cardsFor('left') : null}
            </aside>
          )}

          <article className="reader-article" ref={articleRef}>
            {doc && <h1 className="article-title">{doc.title}</h1>}
            {doc && (
              <div className="article-dek">
                {doc.page_count ? `${doc.page_count} pages` : ''}
                {doc.page_count && doc.blocks.length ? ' · ' : ''}
                {doc.blocks.length ? `${doc.blocks.length} blocks` : ''}
              </div>
            )}
            {doc?.blocks.map((block) => (
              <ArticleBlock
                key={block.id}
                block={block}
                blockTinted={tintedBlocks.has(block.sequence_order)}
                bookmarkTitle={bookmarkedSeqs.get(block.sequence_order) ?? null}
                active={
                  activeNoteId != null &&
                  (groups.some(
                    (g) =>
                      g.root.id === activeNoteId &&
                      g.root.anchor_sequence_id === block.sequence_order,
                  ) ||
                    personalNotes.some(
                      (n) =>
                        n.id === activeNoteId &&
                        n.anchorSequenceId === block.sequence_order,
                    ))
                }
                onAsk={openComposerForBlock}
                onClearBookmark={clearBookmarkAt}
                registerRef={registerBlockRef}
              />
            ))}
            {doc && doc.blocks.length > 0 && <div className="article-end">◆</div>}
          </article>

          <aside className="reader-gutter gutter-right">
            {cardsFor('right')}
          </aside>
        </div>

        {pill && (
          <div
            className="ask-pill-group"
            style={{ top: pill.top, left: pill.left }}
            onMouseDown={(e) => e.preventDefault()}
          >
            <button className="ask-pill" onClick={openComposerFromSelection}>
              Ask
            </button>
            <button className="ask-pill is-note" onClick={openPersonalComposerFromSelection}>
              Note
            </button>
            <button className="ask-pill is-bookmark" onClick={bookmarkFromSelection}>
              Bookmark
            </button>
          </div>
        )}
      </div>

      {/* One button, one meaning — and it now leaves.
          ⚠ It replaced an "Ask" and a "Note" button that both anchored to
          whatever block happened to be at the top of the viewport, and then a
          panel that overlaid the article. Neither was right: a question about
          the paper as a whole, or about several papers, is not a thing you do
          *on top of* a document you are reading. It is its own place, so the
          corner is now a door to the desk rather than a drawer. Passage-level
          work stays here, on the selection pill and the A / N keys. */}
      {!composer && !personalComposer && onOpenDesk && (
        <div className="reader-fabs">
          <button
            className="ask-fab panel-fab"
            onClick={() => onOpenDesk(deskScope ?? 'library')}
            title="Open the desk — ask across this paper and others (P)"
          >
            <span className="panel-fab-glyph" aria-hidden="true">◈</span>
            Desk
            {holisticGroups.length > 0 && (
              <span className="panel-fab-count">{holisticGroups.length}</span>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
