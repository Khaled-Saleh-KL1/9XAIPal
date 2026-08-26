import { useEffect, useRef, useState } from 'react';
import { NoteCardView, PendingNoteCard, type NoteGroup, type PendingNote } from './NoteCard';
import type { ModelCatalog } from '../api';

/**
 * The holistic level: questions about the paper as a whole.
 *
 * ⚠ **This exists because scope is a real distinction, not a layout one.**
 * Everything in the margin is anchored — you highlight a sentence and ask
 * about *that*. But "what is this paper actually claiming?" has no sentence to
 * hang off, and forcing it into a margin card anchored to whatever happened to
 * be on screen produced answers that read the question as being about that
 * block. Two levels, two surfaces: highlight for the passage, open this for
 * the paper.
 *
 * ⚠ **It replaced two floating buttons with one.** "Ask" and "Note" sat side
 * by side in the corner doing what a selection already does better, so they
 * read as a second, worse way to do the same thing. One button that opens one
 * panel leaves the corner meaning exactly one thing — and gives the
 * cross-paper level somewhere to land when it arrives.
 */

type Tab = 'paper' | 'library';

function ModelPicker({
  catalog,
  model,
  onChange,
}: {
  catalog: ModelCatalog | null;
  model: string;
  onChange: (name: string) => void;
}) {
  if (!catalog || catalog.models.length === 0) return null;
  return (
    <label className="model-picker" title="Which model answers this question">
      <select value={model} onChange={(e) => onChange(e.target.value)}>
        {catalog.models.some((m) => !m.is_cloud) && (
          <optgroup label="Local">
            {catalog.models.filter((m) => !m.is_cloud).map((m) => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </optgroup>
        )}
        {catalog.models.some((m) => m.is_cloud) && (
          <optgroup label="Cloud">
            {catalog.models.filter((m) => m.is_cloud).map((m) => (
              <option key={m.name} value={m.name}>{m.name}</option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
}

export function AssistantPanel({
  open,
  onClose,
  paperTitle,
  groups,
  pending,
  onAsk,
  onFollowUp,
  onDelete,
  onRetry,
  onDismiss,
  onJump,
  catalog,
  model,
  onModelChange,
}: {
  open: boolean;
  onClose: () => void;
  paperTitle: string;
  /** Answered whole-paper questions, oldest first. */
  groups: NoteGroup[];
  /** Whole-paper questions still generating. */
  pending: PendingNote[];
  onAsk: (question: string) => void;
  onFollowUp: (parentNoteId: string, question: string) => void;
  onDelete: (noteId: string) => void;
  onRetry: (clientId: string) => void;
  onDismiss: (clientId: string) => void;
  /** Scroll the article to a block. Closes the panel on the way. */
  onJump: (seq: number) => void;
  catalog: ModelCatalog | null;
  model: string;
  onModelChange: (name: string) => void;
}) {
  const [tab, setTab] = useState<Tab>('paper');
  const [question, setQuestion] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus({ preventScroll: true });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Follow the newest question down as the agent works. Without this a long
  // trail pushes the row being written below the fold of the panel.
  useEffect(() => {
    if (!open) return;
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
  }, [open, pending.length, groups.length]);

  if (!open) return null;

  const send = () => {
    const q = question.trim();
    if (!q) return;
    onAsk(q);
    setQuestion('');
  };

  const jump = (seq: number) => {
    onJump(seq);
    onClose();
  };

  const empty = groups.length === 0 && pending.length === 0;

  return (
    <div className="asst-scrim" onClick={onClose}>
      <aside
        className="asst-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Assistant"
      >
        <div className="asst-head">
          <div className="asst-tabs">
            <button
              type="button"
              className={`asst-tab${tab === 'paper' ? ' is-on' : ''}`}
              onClick={() => setTab('paper')}
            >
              This paper
              {groups.length > 0 && <span className="marg-badge">{groups.length}</span>}
            </button>
            <button
              type="button"
              className="asst-tab is-soon"
              onClick={() => setTab('library')}
              title="Questions across every paper in the library — not built yet"
            >
              Across papers
              <span className="asst-soon">soon</span>
            </button>
          </div>
          <button type="button" className="marg-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {tab === 'library' ? (
          <div className="asst-empty">
            <p className="asst-empty-lead">Ask across your whole library.</p>
            <p>
              The level above this one: one question answered from every paper you
              have, with each claim attributed to the paper it came from.
            </p>
            <p className="marg-hint">
              Not built yet. This panel is where it will live.
            </p>
            <button type="button" className="marg-cta" onClick={() => setTab('paper')}>
              Ask about this paper instead
            </button>
          </div>
        ) : (
          <>
            <div className="asst-list thin-scroll" ref={listRef}>
              {empty ? (
                <div className="asst-empty">
                  <p className="asst-empty-lead">Ask about the whole paper.</p>
                  <p>
                    Questions here range over all of {paperTitle} — what it argues,
                    how the method works, whether the results support the claim. The
                    agent reads the contents, fetches the sections it needs, and
                    shows you which ones it used.
                  </p>
                  <p className="marg-hint">
                    Asking about one passage instead? Highlight it in the article and
                    press <span className="kbd">A</span> — that answer lands in the
                    margin, beside the text it is about.
                  </p>
                </div>
              ) : (
                <>
                  {groups.map((group) => (
                    <NoteCardView
                      key={group.root.id}
                      group={group}
                      active={false}
                      onFocus={() => {}}
                      onJump={jump}
                      onDelete={onDelete}
                      onFollowUp={onFollowUp}
                      // No margins in a panel, and no decks: a deck is a spatial
                      // arrangement of cards in a gutter that has none here.
                      onFlip={null}
                    />
                  ))}
                  {pending.map((note) => (
                    <PendingNoteCard
                      key={note.clientId}
                      note={note}
                      onRetry={() => onRetry(note.clientId)}
                      onDismiss={() => onDismiss(note.clientId)}
                      onJump={jump}
                    />
                  ))}
                </>
              )}
            </div>

            <div className="asst-composer">
              <textarea
                ref={inputRef}
                rows={3}
                value={question}
                placeholder="Ask about the whole paper…"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <ModelPicker catalog={catalog} model={model} onChange={onModelChange} />
              <div className="note-actions">
                <button type="button" onClick={send} disabled={!question.trim()}>
                  Ask
                </button>
                <span className="note-hint">↵ to send</span>
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
