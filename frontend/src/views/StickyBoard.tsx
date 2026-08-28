import { StickyNote } from './StickyNote';
import type { Sticky, StickyColor } from '../api';

/**
 * The chat's own notes, in a strip beside the transcript.
 *
 * ⚠ **Scoped to the conversation, not to papers.** Notes here belong to *this*
 * chat: switching study switches the board. A thought that outlives the
 * question belongs on the universal board instead, and either the reader or the
 * assistant can put it there.
 *
 * ⚠ **Hideable, and the state persists.** The strip is a companion; a reader
 * working through a long answer wants the width back. Collapsing it is not the
 * same as having no notes, so the collapsed rail still shows the count.
 */
export function StickyBoard({
  notes,
  scopeName,
  collapsed,
  onToggle,
  onCreate,
  onSave,
  onDelete,
}: {
  notes: Sticky[];
  scopeName: string;
  collapsed: boolean;
  onToggle: () => void;
  onCreate: () => void;
  onSave: (id: string, patch: { body?: string; color?: StickyColor; pinned?: boolean }) => void;
  onDelete: (id: string) => void;
}) {
  if (collapsed) {
    return (
      <aside className="board is-collapsed">
        <button
          type="button"
          className="board-reopen"
          onClick={onToggle}
          title="Show this chat's notes"
          aria-label="Show this chat's notes"
        >
          <span className="board-reopen-glyph" aria-hidden="true">‹</span>
          <span className="board-reopen-label">Notes</span>
          {notes.length > 0 && <span className="board-reopen-count">{notes.length}</span>}
        </button>
      </aside>
    );
  }

  return (
    <aside className="board">
      <header className="board-head">
        <h2>Notes · this chat</h2>
        <button type="button" className="board-add" onClick={onCreate} title="New note">
          +
        </button>
        <button
          type="button"
          className="board-hide"
          onClick={onToggle}
          title="Hide the notes"
          aria-label="Hide the notes"
        >
          ›
        </button>
      </header>

      <div className="board-list thin-scroll">
        {notes.length === 0 ? (
          <div className="board-empty">
            <p>No notes on {scopeName} yet.</p>
            <p className="marg-hint">
              These stay with this conversation. The assistant can pin here too,
              its notes are badged, and only you can remove one.
            </p>
          </div>
        ) : (
          notes.map((n) => (
            <StickyNote
              key={n.id}
              note={n}
              onSave={(patch) => onSave(n.id, patch)}
              onDelete={() => onDelete(n.id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
