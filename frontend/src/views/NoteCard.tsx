import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import { maskIncompleteMath } from '../lib/pacer';
import {
  CardEyebrow,
  CardGrip,
  Collapsible,
  useCardDrag,
  type CardDrag,
} from './NoteChrome';
import type { PaperNote } from '../api';

/**
 * A margin note: one question, its answer, and any follow-ups, rendered as a
 * card in the gutter beside the passage it is about.
 */

/** A note being generated right now — not yet a complete row. */
export interface PendingNote {
  clientId: string;
  noteId: string | null;
  anchorSequenceId: number;
  anchorKind: string;
  quote: string | null;
  /**
   * The anchor's image, when it has one (figure, equation crop, table crop).
   *
   * ⚠ Carried here only so a retry can send it again. The prompt for those
   * kinds tells the model to trust the attached crop over the transcription,
   * so retrying without it asks the model to trust an image that is not there.
   */
  imageUrl: string | null;
  question: string;
  answer: string;
  status: string | null;
  error: string | null;
  parentNoteId: string | null;
  marginSide: 'left' | 'right';
  /** The model this note was asked with, shown while it streams. */
  model: string | null;
}

export interface NoteGroup {
  /** The root note; follow-ups chain beneath it in one card. */
  root: PaperNote;
  replies: PaperNote[];
}

/**
 * Study state for a card sitting face-up in a deck.
 *
 * `revealed` false means show the prompt only — the question and what it was
 * asked about — so the deck can be used to test recall rather than reread.
 */
export interface StudyState {
  revealed: boolean;
  onReveal: () => void;
}

/**
 * Which model produced this answer.
 *
 * Shown on every answer, not just when several models are in play: the whole
 * point of the picker is comparing two notes asking the same thing, and that
 * comparison is only readable if each card says who spoke.
 */
function ModelTag({ name }: { name: string | null }) {
  if (!name) return null;
  return <span className="note-model" title={`Answered by ${name}`}>{name}</span>;
}

function Answer({ text }: { text: string }) {
  return (
    <div className="note-answer">
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Rewrite the agent's [[42]] block markers into clickable links.
 *
 * Done as a text transform before markdown rather than as a rehype plugin: the
 * markers are the model's own convention, not markdown, and keeping the
 * rewrite here means the markdown pipeline stays shared with the reader.
 *
 * ⚠ Matches a whole bracket blob, not a single number. Models group references
 * as "[[16], [42]]" often enough that a strict single-number pattern leaves
 * raw brackets sitting in the rendered answer.
 */
function withCitationLinks(text: string): string {
  return text.replace(/\[\[([0-9,;\s[\]]+?)\]\]/g, (whole, inner: string) => {
    const seqs = inner.match(/\d+/g);
    if (!seqs) return whole;
    return seqs.map((seq) => `[¶${seq}](#blk-${seq})`).join(' ');
  });
}

function CitationChips({
  cited,
  onJump,
}: {
  cited: number[];
  onJump: (seq: number) => void;
}) {
  if (!cited.length) return null;
  return (
    <div className="note-cites">
      {cited.map((seq) => (
        <button key={seq} type="button" onClick={() => onJump(seq)} className="note-cite">
          ¶{seq}
        </button>
      ))}
    </div>
  );
}

function Quote({ kind, quote }: { kind: string; quote: string | null }) {
  if (kind === 'figure') {
    return <div className="note-quote note-quote-figure">On this figure</div>;
  }
  if (kind === 'equation') {
    return <div className="note-quote note-quote-figure">On this equation</div>;
  }
  if (kind === 'table') {
    // Never the quote: a table's quote is its whole transcription.
    return <div className="note-quote note-quote-figure">On this table</div>;
  }
  if (!quote) {
    return <div className="note-quote note-quote-figure">On this passage</div>;
  }
  return <div className="note-quote">“{quote}”</div>;
}

/**
 * Destructive actions get one step of friction.
 *
 * Delete sits next to "Follow up" in a strip of quiet buttons, and a note can
 * represent a minute of model time and a question you may not remember how you
 * phrased. Arming the button rather than opening a dialog keeps the cost of a
 * deliberate delete at two clicks and the cost of a misclick at zero.
 */
function DeleteButton({ onConfirm }: { onConfirm: () => void }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      className={armed ? 'is-armed' : ''}
      onClick={() => (armed ? onConfirm() : setArmed(true))}
    >
      {armed ? 'Really?' : 'Delete'}
    </button>
  );
}

export function PendingNoteCard({
  note,
  onRetry,
  onDismiss,
  onJump,
}: {
  note: PendingNote;
  onRetry: () => void;
  onDismiss: () => void;
  onJump?: (seq: number) => void;
}) {
  return (
    <article className="note-card is-pending">
      <CardEyebrow
        tone="pending"
        seq={note.anchorSequenceId}
        onJump={onJump}
        right={<ModelTag name={note.model} />}
      />
      <Quote kind={note.anchorKind} quote={note.quote} />
      <div className="note-question">{note.question}</div>
      {note.error ? (
        <>
          <div className="note-error">{note.error}</div>
          <div className="note-actions">
            <button type="button" onClick={onRetry}>Retry</button>
            <button type="button" onClick={onDismiss}>Dismiss</button>
          </div>
        </>
      ) : note.answer ? (
        // Still streaming: withhold a half-written LaTeX span so the reader
        // doesn't watch raw markup type itself out and then snap into a symbol.
        <Answer text={withCitationLinks(maskIncompleteMath(note.answer))} />
      ) : (
        <div className="note-status">
          <span className="note-dot" />
          {note.status || 'Thinking…'}
        </div>
      )}
    </article>
  );
}

export function NoteCardView({
  group,
  active,
  onFocus,
  onJump,
  onDelete,
  onFollowUp,
  onFlip,
  drag = null,
  inDeck = false,
  study = null,
}: {
  group: NoteGroup;
  active: boolean;
  onFocus: () => void;
  onJump: (seq: number) => void;
  onDelete: (noteId: string) => void;
  onFollowUp: (parentNoteId: string, question: string) => void;
  /** Move this card to the other margin; null when only one margin fits. */
  onFlip: (() => void) | null;
  /** Drag-to-stack wiring; null inside a deck, where the deck is the handle. */
  drag?: CardDrag | null;
  /** Rendered face-up inside a deck: the deck owns the frame and the eyebrow. */
  inDeck?: boolean;
  study?: StudyState | null;
}) {
  const [followUp, setFollowUp] = useState('');
  const [composing, setComposing] = useState(false);
  const followUpRef = useRef<HTMLTextAreaElement>(null);
  const last = group.replies.length ? group.replies[group.replies.length - 1] : group.root;
  const { dragging, isDropTarget, zoneProps, gripProps } = useCardDrag(drag);
  const rootModel = group.root.model || group.root.requested_model;

  // Focus without scrolling — see the note in AskComposer: an autoFocus here
  // yanks the article away from the passage the note is about.
  useEffect(() => {
    if (composing) followUpRef.current?.focus({ preventScroll: true });
  }, [composing]);

  const send = () => {
    const q = followUp.trim();
    if (!q) return;
    onFollowUp(last.id, q);
    setFollowUp('');
    setComposing(false);
  };

  // Study front: the prompt only. Everything the answer would give away stays
  // behind the reveal.
  if (study && !study.revealed) {
    return (
      <div className="note-body is-study-front">
        <Quote kind={group.root.anchor_kind} quote={group.root.anchor_quote} />
        <div className="note-question">{group.root.question}</div>
        <button type="button" className="card-reveal" onClick={study.onReveal}>
          Reveal answer
        </button>
      </div>
    );
  }

  const body = (
    <>
      <Quote kind={group.root.anchor_kind} quote={group.root.anchor_quote} />
      <div className="note-question">{group.root.question}</div>

      <Collapsible max={inDeck ? 400 : 300}>
        <Answer text={withCitationLinks(group.root.answer)} />
        <CitationChips cited={group.root.cited_sequence_ids} onJump={onJump} />

        {group.replies.map((reply) => (
          <div key={reply.id} className="note-reply">
            <div className="note-question">{reply.question}</div>
            {/* Follow-ups inherit the root's model, so the tag is only worth
                the space when something actually answered differently. */}
            {(reply.model || reply.requested_model) !== rootModel && (
              <ModelTag name={reply.model || reply.requested_model} />
            )}
            <Answer text={withCitationLinks(reply.answer)} />
            <CitationChips cited={reply.cited_sequence_ids} onJump={onJump} />
          </div>
        ))}
      </Collapsible>

      <div className="note-footer">
        {composing ? (
          <div className="note-followup">
            <textarea
              ref={followUpRef}
              rows={2}
              value={followUp}
              placeholder="Follow up…"
              onChange={(e) => setFollowUp(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
                if (e.key === 'Escape') setComposing(false);
              }}
            />
            <div className="note-actions">
              <button type="button" onClick={send} disabled={!followUp.trim()}>Ask</button>
              <button type="button" onClick={() => setComposing(false)}>Cancel</button>
              <span className="note-hint">
                stays on {group.root.requested_model || group.root.model || 'this model'}
              </span>
            </div>
          </div>
        ) : (
          <div className="note-actions note-actions-quiet">
            <button type="button" onClick={() => setComposing(true)}>Follow up</button>
            <DeleteButton onConfirm={() => onDelete(group.root.id)} />
            {onFlip && (
              <button
                type="button"
                className="note-flip"
                onClick={onFlip}
                title={`Move to the ${group.root.margin_side === 'right' ? 'left' : 'right'} margin`}
              >
                {group.root.margin_side === 'right' ? '←' : '→'}
              </button>
            )}
            {group.root.retrieval_mode === 'agent' && (
              <span className="note-mode" title="This paper was too large to read at once, so the model searched it.">
                searched
              </span>
            )}
          </div>
        )}
      </div>
    </>
  );

  if (inDeck) return <div className="note-body">{body}</div>;

  return (
    <article
      className={[
        'note-card is-ai',
        active ? 'is-active' : '',
        dragging ? 'is-dragging' : '',
        isDropTarget ? 'is-drop-target' : '',
      ].filter(Boolean).join(' ')}
      onMouseEnter={onFocus}
      {...zoneProps}
    >
      <CardEyebrow
        tone="ai"
        seq={group.root.anchor_sequence_id}
        onJump={onJump}
        grip={<CardGrip {...gripProps} />}
        right={
          <>
            {group.replies.length > 0 && (
              <span className="note-thread-count" title={`${group.replies.length} follow-ups`}>
                +{group.replies.length}
              </span>
            )}
            <ModelTag name={rootModel} />
          </>
        }
      />
      {body}
    </article>
  );
}
