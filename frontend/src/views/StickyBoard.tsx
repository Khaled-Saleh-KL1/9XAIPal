import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import type { Sticky, StickyColor, StudyPaper } from '../api';

/**
 * The desk's sticky notes.
 *
 * ⚠ **A note with no papers is the important case, not the empty one.** The
 * thing a reader most wants pinned — a question to come back to, a definition,
 * a suspicion about the whole field — usually belongs to no single paper. Those
 * show on every desk, and the UI says "any paper" rather than leaving the scope
 * row blank as if something were missing.
 */

const COLORS: StickyColor[] = ['yellow', 'blue', 'green', 'pink', 'plain'];

/** Edit in place: a sticky is a scrap of text, and a modal is heavier than it. */
function StickyCard({
  sticky,
  papers,
  onSave,
  onDelete,
}: {
  sticky: Sticky;
  /** Papers currently in scope, offered as the ones this note can name. */
  papers: StudyPaper[];
  onSave: (patch: { body?: string; color?: StickyColor; pinned?: boolean; document_ids?: string[] }) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(sticky.body === '');
  const [draft, setDraft] = useState(sticky.body);
  const [scoping, setScoping] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) ref.current?.focus({ preventScroll: true });
  }, [editing]);

  const commit = () => {
    setEditing(false);
    if (draft !== sticky.body) onSave({ body: draft });
  };

  const scopedIds = new Set(sticky.papers.map((p) => p.document_id));
  const toggleScope = (id: string) => {
    const next = new Set(scopedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSave({ document_ids: [...next] });
  };

  return (
    <article className={`sticky tone-${sticky.color}${sticky.pinned ? ' is-pinned' : ''}`}>
      <div className="sticky-top">
        <button
          type="button"
          className={`sticky-pin${sticky.pinned ? ' is-on' : ''}`}
          onClick={() => onSave({ pinned: !sticky.pinned })}
          title={sticky.pinned ? 'Unpin' : 'Pin to the top'}
          aria-label={sticky.pinned ? 'Unpin this note' : 'Pin this note'}
        >
          <svg viewBox="0 0 12 16" width="9" height="12" aria-hidden="true">
            <path d="M1 1h10v14l-5-4-5 4z" />
          </svg>
        </button>
        <div className="sticky-colors">
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              className={`sticky-swatch tone-${c}${c === sticky.color ? ' is-on' : ''}`}
              onClick={() => onSave({ color: c })}
              title={c}
              aria-label={`Colour: ${c}`}
            />
          ))}
        </div>
        <button
          type="button"
          className="sticky-drop"
          onClick={onDelete}
          title="Delete this note"
          aria-label="Delete this note"
        >
          ×
        </button>
      </div>

      {editing ? (
        <textarea
          ref={ref}
          className="sticky-input"
          value={draft}
          placeholder="Write it down…"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            // ⚠ Enter inserts a newline here, unlike every composer in the app.
            // A sticky is a scrap you jot in several lines; sending on Enter
            // would truncate half of them.
            if (e.key === 'Escape') {
              setDraft(sticky.body);
              setEditing(false);
            }
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit();
          }}
        />
      ) : (
        <div
          className="sticky-body md-body"
          onClick={() => { setDraft(sticky.body); setEditing(true); }}
        >
          {sticky.body ? (
            <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
              {sticky.body}
            </ReactMarkdown>
          ) : (
            <span className="sticky-placeholder">Empty — click to write.</span>
          )}
        </div>
      )}

      <div className="sticky-foot">
        <button
          type="button"
          className="sticky-scope"
          onClick={() => setScoping((v) => !v)}
          aria-expanded={scoping}
        >
          {sticky.papers.length === 0
            ? 'any paper'
            : sticky.papers.length === 1
            ? sticky.papers[0].label
            : `${sticky.papers.length} papers`}
        </button>
      </div>

      {scoping && (
        <div className="sticky-scope-list">
          {papers.length === 0 && <p className="marg-hint">No papers in this scope.</p>}
          {papers.map((p) => (
            <label key={p.id} className="sticky-scope-row">
              <input
                type="checkbox"
                checked={scopedIds.has(p.id)}
                onChange={() => toggleScope(p.id)}
              />
              <span>{p.title}</span>
            </label>
          ))}
          {sticky.papers.length > 0 && (
            <button
              type="button"
              className="sticky-scope-clear"
              onClick={() => onSave({ document_ids: [] })}
            >
              Make it about any paper
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export function StickyBoard({
  stickies,
  papers,
  onCreate,
  onSave,
  onDelete,
}: {
  stickies: Sticky[];
  papers: StudyPaper[];
  onCreate: () => void;
  onSave: (id: string, patch: { body?: string; color?: StickyColor; pinned?: boolean; document_ids?: string[] }) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="board">
      <header className="board-head">
        <h2>Notes</h2>
        <button type="button" className="board-add" onClick={onCreate} title="New note">
          +
        </button>
      </header>
      <div className="board-list thin-scroll">
        {stickies.length === 0 ? (
          <div className="board-empty">
            <p>Nothing pinned yet.</p>
            <p className="marg-hint">
              Notes here are yours, not the assistant's. Scope one to a paper, to
              several, or to none — an unscoped note follows you to every study.
            </p>
          </div>
        ) : (
          stickies.map((s) => (
            <StickyCard
              key={s.id}
              sticky={s}
              papers={papers}
              onSave={(patch) => onSave(s.id, patch)}
              onDelete={() => onDelete(s.id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
