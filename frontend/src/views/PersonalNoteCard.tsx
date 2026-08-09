import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import { formatRelativeTime } from '../lib/time';
import {
  CardEyebrow,
  CardGrip,
  Collapsible,
  useCardDrag,
  type CardDrag,
} from './NoteChrome';
import type { StudyState } from './NoteCard';
import type { PersonalNote } from '../lib/personalNotes';

/**
 * A user-authored margin note. Wears the same chrome as an AI note card but is
 * fully local, editable, and persists across sessions.
 */

function Quote({ quote }: { quote: string | null }) {
  if (!quote) {
    return <div className="note-quote note-quote-figure">On this passage</div>;
  }
  return <div className="note-quote">“{quote}”</div>;
}

function Body({ text }: { text: string }) {
  return (
    <div className="note-answer">
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function PersonalNoteCard({
  note,
  active,
  onFocus,
  onJump,
  onDelete,
  onEdit,
  onFlip,
  drag = null,
  inDeck = false,
  study = null,
}: {
  note: PersonalNote;
  active: boolean;
  onFocus: () => void;
  onJump?: (seq: number) => void;
  onDelete: (id: string) => void;
  onEdit: (id: string, body: string) => void;
  /** Move this card to the other margin; null when only one margin fits. */
  onFlip: (() => void) | null;
  /** Drag-to-stack wiring; null inside a deck, where the deck is the handle. */
  drag?: CardDrag | null;
  /** Rendered face-up inside a deck: the deck owns the frame and the eyebrow. */
  inDeck?: boolean;
  study?: StudyState | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.body);
  const ref = useRef<HTMLTextAreaElement>(null);
  const { dragging, isDropTarget, zoneProps, gripProps } = useCardDrag(drag);

  useEffect(() => {
    if (editing) ref.current?.focus({ preventScroll: true });
  }, [editing]);

  const save = () => {
    const body = draft.trim();
    if (!body) {
      // Empty body means delete — otherwise we keep a blank card around.
      onDelete(note.id);
      return;
    }
    onEdit(note.id, body);
    setEditing(false);
  };

  const cancel = () => {
    setDraft(note.body);
    setEditing(false);
  };

  // Study front: your own note is the answer, so the quote is the prompt.
  if (study && !study.revealed && !editing) {
    return (
      <div className="note-body is-study-front">
        <Quote quote={note.quote} />
        <div className="note-question">What did you write here?</div>
        <button type="button" className="card-reveal" onClick={study.onReveal}>
          Reveal note
        </button>
      </div>
    );
  }

  const body = editing ? (
    <>
      <Quote quote={note.quote} />
      <textarea
        ref={ref}
        rows={3}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            save();
          }
          if (e.key === 'Escape') cancel();
        }}
      />
      <div className="note-actions">
        <button type="button" onClick={save} disabled={!draft.trim()}>
          Save
        </button>
        <button type="button" onClick={cancel}>
          Cancel
        </button>
        <span className="note-hint">⌘/ctrl + ↵ to save</span>
      </div>
    </>
  ) : (
    <>
      <Quote quote={note.quote} />
      <Collapsible max={inDeck ? 400 : 260}>
        <Body text={note.body} />
      </Collapsible>
      <div className="note-footer">
        <div className="note-actions note-actions-quiet">
          <button type="button" onClick={() => setEditing(true)}>
            Edit
          </button>
          <button type="button" onClick={() => onDelete(note.id)}>
            Delete
          </button>
          {onFlip && (
            <button
              type="button"
              className="note-flip"
              onClick={onFlip}
              title={`Move to the ${note.marginSide === 'right' ? 'left' : 'right'} margin`}
            >
              {note.marginSide === 'right' ? '←' : '→'}
            </button>
          )}
        </div>
      </div>
    </>
  );

  if (inDeck) return <div className="note-body">{body}</div>;

  return (
    <article
      className={[
        'note-card is-personal',
        active ? 'is-active' : '',
        dragging ? 'is-dragging' : '',
        isDropTarget ? 'is-drop-target' : '',
      ].filter(Boolean).join(' ')}
      onMouseEnter={onFocus}
      {...zoneProps}
    >
      <CardEyebrow
        tone="personal"
        seq={note.anchorSequenceId}
        onJump={onJump}
        grip={<CardGrip {...gripProps} />}
        right={<span className="note-when">{formatRelativeTime(note.updatedAt)}</span>}
      />
      {body}
    </article>
  );
}

export interface PersonalComposerTarget {
  sequenceId: number;
  chunkId: string | null;
  kind: 'text' | 'figure' | 'equation' | 'block';
  quote: string | null;
  imageUrl: string | null;
  marginSide: 'left' | 'right';
}

export function PersonalNoteComposer({
  target,
  onSubmit,
  onCancel,
  onFlip,
}: {
  target: PersonalComposerTarget;
  onSubmit: (body: string) => void;
  onCancel: () => void;
  onFlip: (() => void) | null;
}) {
  const [body, setBody] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
  }, [target.sequenceId, target.quote]);

  // ⚠ The draft is deliberately NOT cleared here. Saving is a round trip now,
  // and the parent closes this composer only once the note is safely stored —
  // so on a failed save the text the reader typed is still sitting in the box
  // rather than gone with nothing to show for it.
  const send = () => {
    const text = body.trim();
    if (!text) return;
    onSubmit(text);
  };

  const isMedia = target.kind === 'figure' || target.kind === 'equation';
  const label =
    target.kind === 'figure'
      ? 'On this figure'
      : target.kind === 'equation'
      ? 'On this equation'
      : target.quote
      ? `“${target.quote}”`
      : 'On this passage';

  return (
    <article className="note-card is-composer is-personal">
      <CardEyebrow tone="personal" seq={target.sequenceId} word="New note" />
      <div className={`note-quote ${target.quote && !isMedia ? '' : 'note-quote-figure'}`}>
        {label}
      </div>
      {isMedia && target.imageUrl && (
        <img className="composer-thumb" src={target.imageUrl} alt="" />
      )}
      <textarea
        ref={ref}
        rows={3}
        value={body}
        placeholder="Write your own note…"
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            send();
          }
          if (e.key === 'Escape') onCancel();
        }}
      />
      <div className="note-actions">
        <button type="button" onClick={send} disabled={!body.trim()}>
          Save
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        {onFlip && (
          <button
            type="button"
            className="note-flip"
            onClick={onFlip}
            title={`Move to the ${target.marginSide === 'right' ? 'left' : 'right'} margin`}
          >
            {target.marginSide === 'right' ? '←' : '→'}
          </button>
        )}
        <span className="note-hint">⌘/ctrl + ↵ to save · esc cancel</span>
      </div>
    </article>
  );
}
