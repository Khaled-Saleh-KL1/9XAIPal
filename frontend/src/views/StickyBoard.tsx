import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { StickyNote } from './StickyNote';
import type { Sticky, StickyColor } from '../api';
import { calm, reducedMotionFade } from '../motion/springs';

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
  const reducedMotion = useReducedMotion();
  const transition = reducedMotion ? reducedMotionFade : calm;

  return (
    <aside className={`board${collapsed ? ' is-collapsed' : ''}`}>
      <AnimatePresence initial={false}>
        {collapsed ? (
          <m.button
            key="collapsed-board"
            type="button"
            className="board-reopen"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, pointerEvents: 'none' }}
            transition={transition}
            onClick={onToggle}
            title="Show this chat's notes"
            aria-label="Show this chat's notes"
          >
            <span className="board-reopen-glyph" aria-hidden="true">‹</span>
            <span className="board-reopen-label">Notes</span>
            {notes.length > 0 && <span className="board-reopen-count">{notes.length}</span>}
          </m.button>
        ) : (
          <m.div
            key="expanded-board"
            className="board-expanded"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, pointerEvents: 'none' }}
            transition={transition}
          >
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
                <AnimatePresence initial={false}>
                  {notes.map((n) => (
                    <StickyNote
                      key={n.id}
                      note={n}
                      onSave={(patch) => onSave(n.id, patch)}
                      onDelete={() => onDelete(n.id)}
                    />
                  ))}
                </AnimatePresence>
              )}
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </aside>
  );
}
