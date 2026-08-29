import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import type { Sticky, StickyColor } from '../api';

/**
 * One sticky note, shared by the chat strip and the universal board.
 *
 * ⚠ **An assistant note is marked, and the mark cannot be edited away.** The
 * reader has to be able to tell "I decided this" from "a model claimed this"
 * at a glance and after a week, so the badge is drawn from `origin`, which the
 * API refuses to patch. Editing the body of an assistant note leaves it the
 * assistant's; the badge records where the claim came from, not who typed last.
 *
 * ⚠ **Delete is the reader's alone.** There is no assistant path to it: no
 * tool, and `study_agent` does not import the repository's delete. The × here
 * is the only way a note goes away, which is why it is on every note including
 * the ones the assistant wrote.
 */

export const STICKY_COLORS: StickyColor[] = [
  'yellow', 'blue', 'green', 'pink', 'orange', 'plain',
];

/** A small robot-ish glyph. Reads as "not a person" without needing a legend. */
function AuthorBadge({ note }: { note: Sticky }) {
  if (note.origin !== 'assistant') return null;
  return (
    <span
      className="sticky-badge"
      title={
        note.author_model
          ? `Written by ${note.author_model}. You can edit or delete it; it cannot.`
          : 'Written by the assistant. You can edit or delete it; it cannot.'
      }
    >
      <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true">
        <rect x="3" y="5.5" width="10" height="8" rx="2.2" />
        <circle cx="6" cy="9.2" r="1.05" className="eye" />
        <circle cx="10" cy="9.2" r="1.05" className="eye" />
        <path d="M8 2.4v3M5.4 15.2h5.2" />
      </svg>
      assistant
    </span>
  );
}

export function StickyNote({
  note,
  onSave,
  onDelete,
  /** Rendered on the corkboard: a pin, a tilt, and a bigger hit area. */
  pinned = false,
  tilt = 0,
  footer,
}: {
  note: Sticky;
  onSave: (patch: { body?: string; color?: StickyColor; pinned?: boolean }) => void;
  onDelete: () => void;
  pinned?: boolean;
  tilt?: number;
  footer?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(note.body === '' && note.origin === 'user');
  const [draft, setDraft] = useState(note.body);
  const [armed, setArmed] = useState(false);
  /**
   * Whether the clamped body actually has more below it.
   *
   * ⚠ Measured, not inferred. This was a CSS heuristic: fade when the body has
   * three or more block children, which is wrong for the common case: a long
   * note is usually ONE long paragraph, so it clamped with a hard cut through
   * the middle of a line and no fade to say why. CSS cannot ask "did this
   * overflow", so the question is asked of the DOM.
   */
  const [clipped, setClipped] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (editing) ref.current?.focus({ preventScroll: true });
  }, [editing]);

  // The body can change under us: the assistant edits notes too. So a card
  // that is not being edited follows the server.
  useEffect(() => {
    if (!editing) setDraft(note.body);
  }, [note.body, editing]);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);

  const measure = useCallback(() => {
    const el = bodyRef.current;
    // 2px of slack: sub-pixel line heights make an exactly-fitting body report
    // a scrollHeight a fraction over its clientHeight.
    setClipped(!!el && el.scrollHeight - el.clientHeight > 2);
  }, []);

  // Before paint, so a clipped note never shows one frame without its fade.
  useLayoutEffect(measure, [measure, note.body, editing, pinned]);

  // Fonts land after first paint and reflow the text; a note that fits at
  // measure time can overflow once the serif face swaps in.
  useEffect(() => {
    if (!pinned) return;
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pinned, measure]);

  const commit = () => {
    setEditing(false);
    if (draft !== note.body) onSave({ body: draft });
  };

  return (
    <article
      className={[
        'sticky',
        `tone-${note.color}`,
        note.pinned ? 'is-pinned' : '',
        note.origin === 'assistant' ? 'is-ai' : '',
        pinned ? 'is-tacked' : '',
      ].filter(Boolean).join(' ')}
      style={pinned ? { transform: `rotate(${tilt}deg)` } : undefined}
    >
      {pinned && <span className="sticky-tack" aria-hidden="true" />}

      <div className="sticky-top">
        <button
          type="button"
          className={`sticky-pin${note.pinned ? ' is-on' : ''}`}
          onClick={() => onSave({ pinned: !note.pinned })}
          title={note.pinned ? 'Unpin' : 'Pin to the top'}
          aria-label={note.pinned ? 'Unpin this note' : 'Pin this note'}
        >
          <svg viewBox="0 0 12 16" width="9" height="12" aria-hidden="true">
            <path d="M1 1h10v14l-5-4-5 4z" />
          </svg>
        </button>
        <AuthorBadge note={note} />
        <div className="sticky-colors">
          {STICKY_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              className={`sticky-swatch tone-${c}${c === note.color ? ' is-on' : ''}`}
              onClick={() => onSave({ color: c })}
              title={c}
              aria-label={`Colour: ${c}`}
            />
          ))}
        </div>
        {/* Two clicks to delete. A note can be a week-old thought, and the
            assistant cannot recreate one it was never asked to write again. */}
        <button
          type="button"
          className={`sticky-drop${armed ? ' is-armed' : ''}`}
          onClick={() => (armed ? onDelete() : setArmed(true))}
          title={armed ? 'Click again to delete' : 'Delete this note'}
          aria-label={armed ? 'Confirm delete' : 'Delete this note'}
        >
          {armed ? '!' : '×'}
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
              setDraft(note.body);
              setEditing(false);
            }
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) commit();
          }}
        />
      ) : (
        <div
          ref={bodyRef}
          className={`sticky-body md-body${clipped ? ' is-clipped' : ''}`}
          onClick={() => { setDraft(note.body); setEditing(true); }}
          title={clipped ? 'Click to read and edit the rest' : 'Click to edit'}
        >
          {note.body ? (
            <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
              {note.body}
            </ReactMarkdown>
          ) : (
            <span className="sticky-placeholder">Empty, click to write.</span>
          )}
        </div>
      )}

      {footer && <div className="sticky-foot">{footer}</div>}
    </article>
  );
}
