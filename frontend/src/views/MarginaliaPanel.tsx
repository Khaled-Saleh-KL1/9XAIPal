import { useEffect, useMemo, useRef, useState } from 'react';
import { formatRelativeTime } from '../lib/time';
import type { NoteGroup } from './NoteCard';
import type { NoteDeck, PersonalBookmark, PersonalNote } from '../lib/personalNotes';
import type { OutlineEntry } from '../api';

/**
 * One panel that answers "what is in this paper, and what have I done to it?"
 *
 * The reader used to have a Contents overlay and nothing else: bookmarks and
 * notes existed only where they happened to sit in the margin, so finding the
 * thing you wrote an hour ago meant scrolling for it. Structure, marks, and
 * annotations are the same question asked three ways, so they share one
 * surface with one search box over all of them.
 */

type Tab = 'contents' | 'bookmarks' | 'notes';

export interface MarginaliaRow {
  key: string;
  seq: number;
  tone: 'ai' | 'personal';
  /** The passage the note is about, when there is one. */
  quote: string | null;
  /** The question asked, or the note written. */
  text: string;
  at: number | null;
  deckLabel: string | null;
}

/**
 * Flatten markdown to something readable in a one-line preview.
 *
 * The list shows note bodies as plain text, and a personal note is written in
 * markdown — without this the reader's own emphasis comes back at them as
 * literal asterisks.
 */
function plainPreview(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/\s+/g, ' ')
    .trim();
}

/** What a note is anchored to, in words rather than raw LaTeX. */
function anchorLabel(kind: string, quote: string | null): string | null {
  if (kind === 'figure') return 'On a figure';
  if (kind === 'equation') return 'On an equation';
  return quote;
}

/** Flatten AI note threads and personal notes into one sorted list. */
export function buildMarginaliaRows(
  groups: NoteGroup[],
  personalNotes: PersonalNote[],
  decks: NoteDeck[],
): MarginaliaRow[] {
  const deckOf = new Map<string, string>();
  decks.forEach((d, i) =>
    d.members.forEach((m) => deckOf.set(m.id, d.label ?? `Deck ${i + 1}`)),
  );

  const rows: MarginaliaRow[] = [
    ...groups.map((g) => ({
      key: g.root.id,
      seq: g.root.anchor_sequence_id,
      tone: 'ai' as const,
      quote: anchorLabel(g.root.anchor_kind, g.root.anchor_quote),
      text: g.root.question,
      at: g.root.created_at ? Date.parse(g.root.created_at) || null : null,
      deckLabel: deckOf.get(g.root.id) ?? null,
    })),
    ...personalNotes.map((n) => ({
      key: n.id,
      seq: n.anchorSequenceId,
      tone: 'personal' as const,
      quote: n.quote,
      text: plainPreview(n.body),
      at: n.updatedAt,
      deckLabel: deckOf.get(n.id) ?? null,
    })),
  ];
  return rows.sort((a, b) => a.seq - b.seq);
}

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return fields.some((f) => f && f.toLowerCase().includes(q));
}

export function MarginaliaPanel({
  open,
  tab,
  onTabChange,
  onClose,
  outline,
  bookmarks,
  rows,
  onJump,
  onRemoveBookmark,
  onAddBookmark,
  currentSeq,
}: {
  open: boolean;
  tab: Tab;
  onTabChange: (tab: Tab) => void;
  onClose: () => void;
  outline: OutlineEntry[];
  bookmarks: PersonalBookmark[];
  rows: MarginaliaRow[];
  onJump: (seq: number) => void;
  onRemoveBookmark: (id: string) => void;
  onAddBookmark: () => void;
  /** The block currently at the top of the viewport, highlighted in the list. */
  currentSeq: number | null;
}) {
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) searchRef.current?.focus({ preventScroll: true });
    else setQuery('');
  }, [open, tab]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const shownOutline = useMemo(
    () => outline.filter((e) => matches(query, e.text)),
    [outline, query],
  );
  const shownBookmarks = useMemo(
    () =>
      [...bookmarks]
        .sort((a, b) => a.sequenceId - b.sequenceId)
        .filter((b) => matches(query, b.snippet, b.label)),
    [bookmarks, query],
  );
  const shownRows = useMemo(
    () => rows.filter((r) => matches(query, r.text, r.quote, r.deckLabel)),
    [rows, query],
  );

  if (!open) return null;

  const go = (seq: number) => {
    onJump(seq);
    onClose();
  };

  const counts: Record<Tab, number> = {
    contents: outline.length,
    bookmarks: bookmarks.length,
    notes: rows.length,
  };

  return (
    <div className="marg-scrim" onClick={onClose}>
      <aside
        className="marg-panel thin-scroll"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Contents, bookmarks and notes"
      >
        <div className="marg-head">
          <div className="marg-tabs">
            {(['contents', 'bookmarks', 'notes'] as Tab[]).map((t) => (
              <button
                key={t}
                type="button"
                className={`marg-tab${tab === t ? ' is-on' : ''}`}
                onClick={() => onTabChange(t)}
              >
                {t === 'contents' ? 'Contents' : t === 'bookmarks' ? 'Bookmarks' : 'Notes'}
                {counts[t] > 0 && <span className="marg-badge">{counts[t]}</span>}
              </button>
            ))}
          </div>
          <button type="button" className="marg-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <input
          ref={searchRef}
          className="marg-search"
          value={query}
          placeholder={
            tab === 'contents' ? 'Find a section…' : tab === 'bookmarks' ? 'Find a mark…' : 'Find a note…'
          }
          onChange={(e) => setQuery(e.target.value)}
        />

        <div className="marg-list">
          {tab === 'contents' && (
            shownOutline.length === 0 ? (
              <p className="marg-empty">No headings match.</p>
            ) : (
              shownOutline.map((entry) => (
                <button
                  key={entry.sequence_order}
                  type="button"
                  className={`marg-row outline-l${Math.min(entry.level, 3)}${
                    entry.sequence_order === currentSeq ? ' is-current' : ''
                  }`}
                  onClick={() => go(entry.sequence_order)}
                >
                  <span className="marg-row-text">{entry.text}</span>
                </button>
              ))
            )
          )}

          {tab === 'bookmarks' && (
            shownBookmarks.length === 0 ? (
              <div className="marg-empty">
                <p>{bookmarks.length ? 'No marks match.' : 'No bookmarks in this paper yet.'}</p>
                {!bookmarks.length && (
                  <button type="button" className="marg-cta" onClick={onAddBookmark}>
                    Bookmark where you are
                  </button>
                )}
                {!bookmarks.length && (
                  <p className="marg-hint">
                    Or press <span className="kbd">B</span> while reading — with text selected it
                    marks that passage.
                  </p>
                )}
              </div>
            ) : (
              shownBookmarks.map((b) => (
                <div
                  key={b.id}
                  className={`marg-row is-bookmark${b.sequenceId === currentSeq ? ' is-current' : ''}`}
                >
                  <button type="button" className="marg-row-main" onClick={() => go(b.sequenceId)}>
                    <svg className="marg-row-flag" viewBox="0 0 12 16" width="9" height="12" aria-hidden="true">
                      <path d="M1 1h10v14l-5-4-5 4z" />
                    </svg>
                    <span className="marg-row-body">
                      <span className="marg-row-text">
                        {b.label || b.snippet || `¶${b.sequenceId}`}
                      </span>
                      <span className="marg-row-meta">
                        {b.page != null && <>p.{b.page} · </>}
                        {b.updatedAt ? formatRelativeTime(b.updatedAt) : `¶${b.sequenceId}`}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="marg-row-drop"
                    onClick={() => onRemoveBookmark(b.id)}
                    title="Remove this bookmark"
                    aria-label="Remove this bookmark"
                  >
                    ×
                  </button>
                </div>
              ))
            )
          )}

          {tab === 'notes' && (
            shownRows.length === 0 ? (
              <div className="marg-empty">
                <p>{rows.length ? 'No notes match.' : 'No notes in this paper yet.'}</p>
                {!rows.length && (
                  <p className="marg-hint">
                    Select a passage and press <span className="kbd">A</span> to ask about it, or{' '}
                    <span className="kbd">N</span> to write your own note.
                  </p>
                )}
              </div>
            ) : (
              shownRows.map((r) => (
                <button
                  key={r.key}
                  type="button"
                  className={`marg-row is-note tone-${r.tone}${
                    r.seq === currentSeq ? ' is-current' : ''
                  }`}
                  onClick={() => go(r.seq)}
                >
                  <span className="card-dot" aria-hidden="true" />
                  <span className="marg-row-body">
                    <span className="marg-row-text">{r.text}</span>
                    {r.quote && <span className="marg-row-quote">“{r.quote}”</span>}
                    <span className="marg-row-meta">
                      ¶{r.seq}
                      {r.deckLabel && <> · in {r.deckLabel}</>}
                      {r.at && <> · {formatRelativeTime(r.at)}</>}
                    </span>
                  </span>
                </button>
              ))
            )
          )}
        </div>
      </aside>
    </div>
  );
}
