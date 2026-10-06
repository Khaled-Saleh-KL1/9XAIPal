import { useEffect, useRef, useState } from 'react';
import { CardEyebrow } from './NoteChrome';
import { ModelPicker } from '../components/ModelPicker';
import { useAutoGrowTextarea } from '../lib/useAutoGrowTextarea';
import type { ModelCatalog } from '../api';

const MAX_TEXTAREA_HEIGHT = 240;

/**
 * The composer that opens in the margin when the reader highlights something.
 *
 * It shows what is being asked about, takes the question, and hands it up. It
 * never talks to the network: the reader owns the request so the resulting
 * note can be placed and streamed into.
 */

export interface ComposerTarget {
  sequenceId: number;
  chunkId: string | null;
  kind: 'text' | 'figure' | 'equation' | 'table' | 'block';
  quote: string | null;
  imageUrl: string | null;
  /** Which margin the composer (and the resulting note) sits in. */
  marginSide: 'left' | 'right';
}

export function AskComposer({
  target,
  onSubmit,
  onCancel,
  onFlip,
  catalog,
  model,
  onModelChange,
}: {
  target: ComposerTarget;
  onSubmit: (question: string) => void;
  onCancel: () => void;
  /** Move the composer to the other margin; null when only one margin fits. */
  onFlip: (() => void) | null;
  /** Askable models, or null while the catalog is still loading. */
  catalog: ModelCatalog | null;
  model: string;
  onModelChange: (name: string) => void;
}) {
  const [question, setQuestion] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutoGrowTextarea(ref, question, MAX_TEXTAREA_HEIGHT);

  useEffect(() => {
    // ⚠ preventScroll is load-bearing. The card is positioned by a transform
    // the gutter applies on layout; focusing before that lands makes the
    // browser scroll to where the card momentarily is: the top of the
    // gutter, and `scroll-behavior: smooth` then animates the whole article
    // back to the start of the paper while you are trying to type.
    ref.current?.focus({ preventScroll: true });
  }, [target.sequenceId, target.quote]);

  const send = () => {
    const q = question.trim();
    if (!q) return;
    onSubmit(q);
    setQuestion('');
  };

  // A table is media: the quote carried to the model is its transcription,
  // which is a wall of pipes and would be unreadable echoed back here. The
  // reader gets the crop and a label, exactly as they do for a figure.
  const isMedia =
    target.kind === 'figure' || target.kind === 'equation' || target.kind === 'table';
  const label =
    target.kind === 'figure'
      ? 'On this figure'
      : target.kind === 'equation'
      ? 'On this equation'
      : target.kind === 'table'
      ? 'On this table'
      : target.quote
      ? `“${target.quote}”`
      : 'On this passage';

  return (
    <article className="note-card is-composer">
      <CardEyebrow tone="composer" seq={target.sequenceId} word="New question" />
      <div className={`note-quote ${target.quote && !isMedia ? '' : 'note-quote-figure'}`}>
        {label}
      </div>
      {isMedia && target.imageUrl && (
        <img className="composer-thumb" src={target.imageUrl} alt="" />
      )}
      <textarea
        dir="auto"
        ref={ref}
        rows={3}
        value={question}
        placeholder="Ask about this…"
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            send();
          }
          if (e.key === 'Escape') onCancel();
        }}
      />
      {catalog && catalog.models.length > 0 && (
        <ModelPicker
          catalog={catalog}
          model={model}
          onChange={onModelChange}
          title="Which model answers this note"
        />
      )}

      <div className="note-actions">
        <button type="button" onClick={send} disabled={!question.trim()}>Ask</button>
        <button type="button" onClick={onCancel}>Cancel</button>
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
        <span className="note-hint">↵ to send</span>
      </div>
    </article>
  );
}
