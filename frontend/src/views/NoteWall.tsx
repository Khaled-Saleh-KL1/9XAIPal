import { useMemo, useState } from 'react';
import { StickyNote } from './StickyNote';
import type { Sticky, StickyColor } from '../api';

/**
 * The universal board — notes that belong to no conversation.
 *
 * ⚠ **A wall, not a list.** The chat strip is a list because it sits beside a
 * transcript and has to stay narrow; this is the whole page, and the point of
 * it is seeing everything at once. Scanning forty notes is a spatial task, so
 * they are laid out spatially: tacked to a board, tilted, in a masonry flow.
 *
 * ⚠ **The tilt is derived from the note's id, never random.** A `Math.random()`
 * rotation re-rolls on every render — every keystroke in the filter box would
 * make the whole wall twitch. Same id, same angle, forever.
 */

/** Deterministic small angle from the id. Same note, same tilt, every render. */
function tiltFor(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  // −2.6°…+2.6°, in 0.4° steps. Enough to read as pinned paper, little enough
  // that the text stays comfortable to read.
  return ((Math.abs(h) % 14) - 7) * 0.4;
}

type Filter = 'all' | 'mine' | 'assistant';

export function NoteWall({
  notes,
  onCreate,
  onSave,
  onDelete,
}: {
  notes: Sticky[];
  onCreate: () => void;
  onSave: (id: string, patch: { body?: string; color?: StickyColor; pinned?: boolean }) => void;
  onDelete: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return notes.filter((n) => {
      if (filter === 'mine' && n.origin !== 'user') return false;
      if (filter === 'assistant' && n.origin !== 'assistant') return false;
      if (!q) return true;
      return (
        n.body.toLowerCase().includes(q) ||
        n.papers.some((p) => p.label.toLowerCase().includes(q))
      );
    });
  }, [notes, query, filter]);

  const byAssistant = notes.filter((n) => n.origin === 'assistant').length;

  return (
    <section className="wall">
      <header className="wall-head">
        <div className="wall-head-title">
          <h2>Notes</h2>
          <span className="chat-head-sub">
            {notes.length === 0
              ? 'nothing pinned yet'
              : `${notes.length} note${notes.length === 1 ? '' : 's'}` +
                (byAssistant ? ` · ${byAssistant} from the assistant` : '')}
          </span>
        </div>

        <input
          className="wall-search"
          value={query}
          placeholder="Find a note…"
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Find a note"
        />

        <div className="wall-filters">
          {(['all', 'mine', 'assistant'] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={`wall-filter${filter === f ? ' is-on' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'All' : f === 'mine' ? 'Mine' : 'Assistant'}
            </button>
          ))}
        </div>

        <button type="button" className="wall-add" onClick={onCreate}>
          + New note
        </button>
      </header>

      <div className="wall-surface thin-scroll">
        {shown.length === 0 ? (
          <div className="wall-empty">
            <p className="chat-empty-lead">
              {notes.length === 0 ? 'An empty board.' : 'Nothing matches.'}
            </p>
            {notes.length === 0 && (
              <>
                <p>
                  This board belongs to no conversation. It is where a thought
                  goes when it outlives the question that produced it: a
                  contradiction worth chasing, a definition you keep re-looking
                  up, a paper to find.
                </p>
                <p className="marg-hint">
                  The assistant can pin here too, from any chat. Its notes are
                  badged. It can add and edit; only you can remove.
                </p>
              </>
            )}
          </div>
        ) : (
          <div className="wall-grid">
            {shown.map((n) => (
              <StickyNote
                key={n.id}
                note={n}
                pinned
                tilt={tiltFor(n.id)}
                onSave={(patch) => onSave(n.id, patch)}
                onDelete={() => onDelete(n.id)}
                footer={
                  n.papers.length > 0 ? (
                    <span className="sticky-scope" title={n.papers.map((p) => p.label).join(', ')}>
                      {n.papers.length === 1 ? n.papers[0].label : `${n.papers.length} papers`}
                    </span>
                  ) : null
                }
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
