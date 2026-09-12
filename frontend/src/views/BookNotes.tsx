import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createPersonalNote,
  deletePersonalNote,
  getPersonalState,
  putDecks,
  updatePersonalNote,
  type MarginSide,
} from '../api';
import {
  deckFromWire,
  deckToWire,
  noteFromWire,
  reconcileDecks,
  type NoteDeck,
  type PersonalNote,
} from '../lib/personalNotes';
import { pruneDecks, stackDecks, type DragKind } from '../lib/decks';
import { useAutoGrowTextarea } from '../lib/useAutoGrowTextarea';
import { useConfirm } from '../components/ConfirmDialog';
import { IconPlus, IconTrash, IconUndo } from '../components/Icons';

/**
 * The reader's own notes on a book — as floating icons, not margin cards.
 *
 * The paper reader has room for a margin: its notes sit beside the passage
 * they belong to. The book reader does not — the book takes the left pane,
 * the chat the right, and a column of open cards on top of the text would
 * cost exactly the width the book needs. So here a note is a small icon.
 * Write it, and it minimises to the icon; drag the icon anywhere over the
 * page; click it to open the note again. Drop one icon onto another and
 * they become one icon holding both — a stack, with ‹ › to switch between
 * the notes inside, the same decks the paper reader's margin uses
 * (lib/decks.ts is the one rule for both). "Take out" un-stacks one note.
 *
 * What persists where: the notes and the stacks are server-side — the same
 * personal_notes / note_decks rows the paper reader uses, scoped to the
 * document, so a note written on the laptop is on the tablet. Where the
 * icon sits on the page is a per-device convenience kept in localStorage
 * (a tablet and a monitor have different pages); which *side* it was left on
 * does travel, via margin_side, so a fresh device at least puts it on the
 * same edge.
 *
 * A note is anchored at the block the reader had revealed when writing it
 * (`anchor_sequence_id`), so "Go to passage" reopens the book there.
 */

interface Props {
  paperId: string;
  /** The block currently revealed, i.e. where a new note anchors. */
  currentSeq: number | null;
  /** The first words of that block, kept on the note as its quote. */
  currentQuote: string | null;
  onJump: (sequenceId: number) => void;
}

/** One icon on the page: a lone note, or a stack. */
interface Icon {
  id: string;
  kind: 'personal' | 'deck';
  count: number;
  side: MarginSide;
  /** The note this icon shows (a stack's face-up member). */
  face: PersonalNote | null;
}

interface Pos { x: number; y: number }

const ICON = 34;
const ADD_BTN_HEIGHT = 44;
const DRAG_THRESHOLD = 4;

function posKey(paperId: string) {
  return `pal:booknotes:${paperId}:pos`;
}

function readPositions(paperId: string): Record<string, Pos> {
  try {
    const raw = localStorage.getItem(posKey(paperId));
    return raw ? (JSON.parse(raw) as Record<string, Pos>) : {};
  } catch {
    return {};
  }
}

function writePositions(paperId: string, positions: Record<string, Pos>) {
  try {
    localStorage.setItem(posKey(paperId), JSON.stringify(positions));
  } catch {
    /* per-device convenience only; nothing to do without storage */
  }
}

function firstLine(text: string, max = 60): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean.length > max ? `${clean.slice(0, max).trimEnd()}…` : clean;
}

export function BookNotes({ paperId, currentSeq, currentQuote, onJump }: Props) {
  const confirm = useConfirm();
  const layerRef = useRef<HTMLDivElement>(null);
  const [notes, setNotes] = useState<PersonalNote[]>([]);
  const [decks, setDecks] = useState<NoteDeck[]>([]);
  const decksRef = useRef<NoteDeck[]>([]);
  decksRef.current = decks;
  const [positions, setPositions] = useState<Record<string, Pos>>(() => readPositions(paperId));
  /** The icon whose note box is open, if any. */
  const [openId, setOpenId] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [notice, setNotice] = useState<string | null>(null);

  // ── Load ──────────────────────────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    setNotes([]);
    setDecks([]);
    setOpenId(null);
    setComposing(false);
    setPositions(readPositions(paperId));
    getPersonalState(paperId)
      .then((state) => {
        if (!alive) return;
        const ns = state.notes.map(noteFromWire);
        setNotes(ns);
        setDecks(reconcileDecks(state.decks.map(deckFromWire), new Set(), new Set(ns.map((n) => n.id))));
      })
      .catch(() => { if (alive) setNotice('Could not load your notes.'); });
    return () => { alive = false; };
  }, [paperId]);

  // The layer's size, for clamping icons and boxes into the page.
  useEffect(() => {
    const el = layerRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── Derived: the icons ────────────────────────────────────────────────
  const noteById = useMemo(() => new Map(notes.map((n) => [n.id, n])), [notes]);
  const inDeck = useMemo(() => {
    const s = new Set<string>();
    for (const d of decks) for (const m of d.members) s.add(m.id);
    return s;
  }, [decks]);

  const icons: Icon[] = useMemo(() => {
    const out: Icon[] = [];
    for (const n of notes) {
      if (inDeck.has(n.id)) continue;
      out.push({ id: n.id, kind: 'personal', count: 1, side: n.marginSide, face: n });
    }
    for (const d of decks) {
      const faces = d.members.map((m) => noteById.get(m.id)).filter((n): n is PersonalNote => !!n);
      if (faces.length === 0) continue;
      const top = Math.min(Math.max(d.top, 0), faces.length - 1);
      out.push({ id: d.id, kind: 'deck', count: faces.length, side: d.marginSide, face: faces[top] });
    }
    return out;
  }, [notes, decks, inDeck, noteById]);

  /** Where an icon sits: its saved spot, else a slot down its edge. */
  const positionOf = useCallback(
    (icon: Icon, index: number): Pos => {
      const saved = positions[icon.id];
      const maxX = Math.max(0, size.w - ICON - 8);
      const maxY = Math.max(0, size.h - ICON - 8);
      if (saved) return { x: Math.min(Math.max(saved.x, 8), maxX), y: Math.min(Math.max(saved.y, 8), maxY) };
      const x = icon.side === 'left' ? 12 : maxX - 4;
      const y = Math.min(ADD_BTN_HEIGHT + 24 + index * (ICON + 10), maxY);
      return { x, y };
    },
    [positions, size],
  );

  const laidOut = useMemo(
    () => icons.map((icon, i) => ({ icon, pos: positionOf(icon, i) })),
    [icons, positionOf],
  );

  // ── Persistence helpers ───────────────────────────────────────────────
  const commitDecks = useCallback(
    async (next: NoteDeck[]) => {
      const previous = decksRef.current;
      setDecks(next);
      try {
        const saved = await putDecks(paperId, next.map(deckToWire));
        setDecks(saved.map(deckFromWire));
      } catch {
        setDecks(previous);
        setNotice('Could not save the stack.');
      }
    },
    [paperId],
  );

  const savePosition = useCallback(
    (id: string, pos: Pos) => {
      setPositions((prev) => {
        const next = { ...prev, [id]: pos };
        writePositions(paperId, next);
        return next;
      });
    },
    [paperId],
  );

  const setSide = useCallback(
    (icon: Icon, side: MarginSide) => {
      if (icon.side === side) return;
      if (icon.kind === 'personal') {
        setNotes((prev) => prev.map((n) => (n.id === icon.id ? { ...n, marginSide: side } : n)));
        updatePersonalNote(paperId, icon.id, { margin_side: side }).catch(() => {});
      } else {
        void commitDecks(decksRef.current.map((d) => (d.id === icon.id ? { ...d, marginSide: side } : d)));
      }
    },
    [paperId, commitDecks],
  );

  // ── Dragging an icon ──────────────────────────────────────────────────
  const dragRef = useRef<{
    id: string; kind: DragKind; startX: number; startY: number; originX: number; originY: number; moved: boolean;
  } | null>(null);
  const [dragPos, setDragPos] = useState<{ id: string; pos: Pos } | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const iconAt = useCallback(
    (clientX: number, clientY: number, excludeId: string): Icon | null => {
      const layer = layerRef.current;
      if (!layer) return null;
      const r = layer.getBoundingClientRect();
      const x = clientX - r.left;
      const y = clientY - r.top;
      for (const { icon, pos } of laidOut) {
        if (icon.id === excludeId) continue;
        if (x >= pos.x - 6 && x <= pos.x + ICON + 6 && y >= pos.y - 6 && y <= pos.y + ICON + 6) return icon;
      }
      return null;
    },
    [laidOut],
  );

  const onIconPointerDown = (icon: Icon, pos: Pos) => (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = {
      id: icon.id, kind: icon.kind, startX: e.clientX, startY: e.clientY, originX: pos.x, originY: pos.y, moved: false,
    };
  };

  const onIconPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    d.moved = true;
    const maxX = Math.max(0, size.w - ICON - 8);
    const maxY = Math.max(0, size.h - ICON - 8);
    setDragPos({
      id: d.id,
      pos: { x: Math.min(Math.max(d.originX + dx, 8), maxX), y: Math.min(Math.max(d.originY + dy, 8), maxY) },
    });
    setDropTarget(iconAt(e.clientX, e.clientY, d.id)?.id ?? null);
  };

  const onIconPointerUp = (icon: Icon) => (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    setDropTarget(null);
    if (!d) return;
    if (!d.moved) {
      // A click: open the note, or minimise it again.
      setComposing(false);
      setOpenId((cur) => (cur === icon.id ? null : icon.id));
      setDragPos(null);
      return;
    }
    const target = iconAt(e.clientX, e.clientY, d.id);
    if (target) {
      // Dropped onto another icon: they become one stack. The one that was
      // sitting still keeps its place; the dragged one joins it.
      const next = stackDecks(
        decksRef.current,
        { id: d.id, kind: d.kind },
        { id: target.id, kind: target.kind },
        target.side,
      );
      if (next) {
        // The stack stands where the icon it was dropped on stood: a
        // freshly made deck has a new id and would otherwise fall back to
        // the default slot after the next load.
        const holder = next.find((dk) => dk.members.some((m) => m.id === target.id) || dk.id === target.id);
        const targetPos = laidOut.find((x) => x.icon.id === target.id)?.pos;
        if (holder && targetPos && !positions[holder.id]) savePosition(holder.id, targetPos);
        void commitDecks(next);
        setOpenId(null);
      }
      setDragPos(null);
      return;
    }
    const pos = dragPos?.id === d.id ? dragPos.pos : { x: d.originX, y: d.originY };
    savePosition(icon.id, pos);
    setSide(icon, pos.x + ICON / 2 < size.w / 2 ? 'left' : 'right');
    setDragPos(null);
  };

  // ── Note actions ──────────────────────────────────────────────────────
  const saveBody = useCallback(
    async (note: PersonalNote, body: string) => {
      const clean = body.trim();
      if (clean === note.body) return;
      setNotes((prev) => prev.map((n) => (n.id === note.id ? { ...n, body: clean } : n)));
      try {
        const saved = await updatePersonalNote(paperId, note.id, { body: clean });
        setNotes((prev) => prev.map((n) => (n.id === note.id ? noteFromWire(saved) : n)));
      } catch {
        setNotes((prev) => prev.map((n) => (n.id === note.id ? note : n)));
        setNotice('Could not save the note.');
      }
    },
    [paperId],
  );

  const createNote = useCallback(
    async (body: string) => {
      const clean = body.trim();
      if (!clean) { setComposing(false); return; }
      try {
        const saved = await createPersonalNote(paperId, {
          anchor_sequence_id: currentSeq ?? 0,
          body: clean,
          anchor_quote: currentQuote,
          margin_side: 'left',
        });
        setNotes((prev) => [...prev, noteFromWire(saved)]);
        setComposing(false);
      } catch {
        setNotice('Could not save the note.');
      }
    },
    [paperId, currentSeq, currentQuote],
  );

  const removeNote = useCallback(
    async (note: PersonalNote) => {
      const ok = await confirm({
        title: 'Delete this note?',
        body: firstLine(note.body, 120),
        confirmLabel: 'Delete',
        tone: 'danger',
      });
      if (!ok) return;
      const previousNotes = notes;
      const previousDecks = decksRef.current;
      const remaining = notes.filter((n) => n.id !== note.id);
      setNotes(remaining);
      setDecks(reconcileDecks(previousDecks, new Set(), new Set(remaining.map((n) => n.id))));
      setOpenId(null);
      try {
        await deletePersonalNote(paperId, note.id);
      } catch {
        setNotes(previousNotes);
        setDecks(previousDecks);
        setNotice('Could not delete the note.');
      }
    },
    [confirm, notes, paperId],
  );

  const flipDeck = useCallback(
    (deck: NoteDeck, delta: number) => {
      const n = deck.members.length;
      const top = (deck.top + delta + n) % n;
      void commitDecks(decksRef.current.map((d) => (d.id === deck.id ? { ...d, top } : d)));
    },
    [commitDecks],
  );

  const takeOut = useCallback(
    (deck: NoteDeck, noteId: string) => {
      void commitDecks(
        pruneDecks(
          decksRef.current.map((d) =>
            d.id === deck.id ? { ...d, members: d.members.filter((m) => m.id !== noteId) } : d,
          ),
        ),
      );
      setOpenId(noteId);
    },
    [commitDecks],
  );

  // Escape minimises whatever is open.
  useEffect(() => {
    if (!openId && !composing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setOpenId(null); setComposing(false); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openId, composing]);

  const open = openId ? laidOut.find((x) => x.icon.id === openId) ?? null : null;
  const openDeck = open?.icon.kind === 'deck' ? decks.find((d) => d.id === open.icon.id) ?? null : null;

  return (
    <div ref={layerRef} className="bnotes-layer" aria-label="Your notes on this book">
      <button
        type="button"
        className={`bnotes-add${composing ? ' is-on' : ''}`}
        onClick={() => { setOpenId(null); setComposing((v) => !v); }}
        title="Write a note at the passage you are reading (it minimises to an icon you can move)"
      >
        <IconPlus className="w-3.5 h-3.5" />
        Note
        {icons.length > 0 && <span className="bnotes-add-count">{notes.length}</span>}
      </button>

      {laidOut.map(({ icon, pos }) => {
        const shown = dragPos?.id === icon.id ? dragPos.pos : pos;
        return (
          <button
            key={icon.id}
            type="button"
            className={`bnotes-icon${icon.kind === 'deck' ? ' is-deck' : ''}${openId === icon.id ? ' is-open' : ''}${dropTarget === icon.id ? ' is-target' : ''}${dragPos?.id === icon.id ? ' is-dragging' : ''}`}
            style={{ left: shown.x, top: shown.y }}
            title={icon.face ? `${icon.count > 1 ? `${icon.count} notes · ` : ''}${firstLine(icon.face.body)}` : 'Note'}
            aria-label={icon.count > 1 ? `Stack of ${icon.count} notes` : `Note: ${icon.face ? firstLine(icon.face.body, 40) : ''}`}
            onPointerDown={onIconPointerDown(icon, pos)}
            onPointerMove={onIconPointerMove}
            onPointerUp={onIconPointerUp(icon)}
            onPointerCancel={() => { dragRef.current = null; setDragPos(null); setDropTarget(null); }}
          >
            <span className="bnotes-glyph" aria-hidden="true" />
            {icon.count > 1 && <span className="bnotes-badge">{icon.count}</span>}
          </button>
        );
      })}

      {composing && (
        <NoteBox
          key="new"
          title="New note"
          subtitle={currentQuote ? `at “${firstLine(currentQuote, 70)}”` : 'at the start of the book'}
          initial=""
          placeholder="Write it down — it minimises to an icon you can put anywhere."
          onSave={(body) => void createNote(body)}
          onClose={() => setComposing(false)}
          style={{ left: 12, top: ADD_BTN_HEIGHT + 16 }}
          layerSize={size}
        />
      )}

      {open && open.icon.face && (
        <NoteBox
          key={`${open.icon.id}:${open.icon.face.id}`}
          title={openDeck ? `Note ${(openDeck.top % openDeck.members.length) + 1} of ${openDeck.members.length}` : 'Note'}
          subtitle={open.icon.face.quote ? `at “${firstLine(open.icon.face.quote, 70)}”` : null}
          initial={open.icon.face.body}
          placeholder="Empty note"
          onSave={(body) => void saveBody(open.icon.face!, body)}
          onClose={() => setOpenId(null)}
          onJump={() => onJump(open.icon.face!.anchorSequenceId)}
          onDelete={() => void removeNote(open.icon.face!)}
          onPrev={openDeck ? () => flipDeck(openDeck, -1) : undefined}
          onNext={openDeck ? () => flipDeck(openDeck, 1) : undefined}
          onTakeOut={openDeck ? () => takeOut(openDeck, open.icon.face!.id) : undefined}
          style={anchorBox(open.pos, size)}
          layerSize={size}
        />
      )}

      {notice && (
        <div className="bnotes-notice" role="status">
          {notice}
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
        </div>
      )}
    </div>
  );
}

const BOX_W = 300;

/** Put the box beside its icon, on whichever side has room, inside the page. */
function anchorBox(pos: Pos, size: { w: number; h: number }): React.CSSProperties {
  const right = pos.x + ICON + 10;
  const left = right + BOX_W <= size.w - 8 ? right : Math.max(8, pos.x - BOX_W - 10);
  const top = Math.max(8, Math.min(pos.y, Math.max(8, size.h - 260)));
  return { left, top };
}

function NoteBox({
  title, subtitle, initial, placeholder, onSave, onClose, onJump, onDelete, onPrev, onNext, onTakeOut, style, layerSize,
}: {
  title: string;
  subtitle: string | null;
  initial: string;
  placeholder: string;
  onSave: (body: string) => void;
  onClose: () => void;
  onJump?: () => void;
  onDelete?: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onTakeOut?: () => void;
  style: React.CSSProperties;
  layerSize: { w: number; h: number };
}) {
  const [body, setBody] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutoGrowTextarea(ref, body, Math.max(120, Math.min(360, layerSize.h - 180)));
  const isNew = onDelete === undefined;

  useEffect(() => { ref.current?.focus({ preventScroll: true }); }, []);

  const commit = () => { onSave(body); };

  return (
    <div className="bnotes-box" style={{ ...style, width: Math.min(BOX_W, Math.max(200, layerSize.w - 24)) }} role="dialog" aria-label={title}>
      <header className="bnotes-box-head">
        {onPrev && <button type="button" onClick={onPrev} title="Previous note in this stack" aria-label="Previous note">‹</button>}
        <span className="bnotes-box-title">{title}</span>
        {onNext && <button type="button" onClick={onNext} title="Next note in this stack" aria-label="Next note">›</button>}
        <button type="button" className="bnotes-box-min" onClick={() => { if (!isNew) commit(); onClose(); }} title="Minimise to the icon" aria-label="Minimise">
          –
        </button>
      </header>
      {subtitle && (
        <button type="button" className="bnotes-box-quote" onClick={onJump} disabled={!onJump} title={onJump ? 'Go to this passage' : undefined}>
          {subtitle}
        </button>
      )}
      <textarea
        ref={ref}
        className="bnotes-box-text"
        value={body}
        placeholder={placeholder}
        onChange={(e) => setBody(e.target.value)}
        onBlur={() => { if (!isNew) commit(); }}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); commit(); onClose(); }
        }}
      />
      <footer className="bnotes-box-foot">
        {isNew ? (
          <>
            <span className="bnotes-box-hint">⌘/Ctrl + Enter saves</span>
            <button type="button" className="bnotes-box-cancel" onClick={onClose}>Cancel</button>
            <button type="button" className="bnotes-box-save" onClick={() => { commit(); }} disabled={!body.trim()}>Save</button>
          </>
        ) : (
          <>
            {onJump && <button type="button" onClick={onJump} title="Open the book where this note was written">Go to passage ↗</button>}
            {onTakeOut && (
              <button type="button" onClick={onTakeOut} title="Take this note out of the stack, as its own icon">
                <IconUndo className="w-3 h-3" /> Take out
              </button>
            )}
            <button type="button" className="is-danger" onClick={onDelete} title="Delete this note">
              <IconTrash className="w-3 h-3" /> Delete
            </button>
          </>
        )}
      </footer>
    </div>
  );
}
