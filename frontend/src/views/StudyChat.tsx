import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE } from '../lib/markdown';
import { maskIncompleteMath } from '../lib/pacer';
import { AgentTrail } from './AgentTrail';
import { CitationRef } from './CitationRef';
import type { AgentStep, ModelCatalog, StudyPaper, StudyTurn } from '../api';

/**
 * The desk's chat.
 *
 * ⚠ A transcript, not a list of notes. A margin note is one anchored Q+A and
 * stands alone; a desk conversation has follow-ups, pronouns, and "and the
 * second one?", so it reads as a chat, and the server carries history.
 */

/** A question in flight: what the agent is doing, and the answer so far. */
export interface PendingTurn {
  clientId: string;
  question: string;
  answer: string;
  status: string | null;
  steps: AgentStep[];
  error: string | null;
}

/**
 * Rewrite `[[P2:41]]` markers into links the renderer swaps for citation chips.
 *
 * ⚠ **A text transform before markdown, not a split around it.** Splitting the
 * answer on its markers and rendering each fragment separately makes every
 * fragment its own block: a citation mid-sentence then breaks the paragraph in
 * two and strands the rest of the sentence, including a lone trailing full
 * stop, on its own line. Turning the marker into an inline link keeps the
 * paragraph whole and lets `components.a` do the swap.
 *
 * ⚠ Matches a whole bracket blob, not one reference. Models group them as
 * "[[P1:33, P1:35]]" often enough that a strict single-reference pattern leaves
 * raw brackets sitting in the rendered answer.
 */
const CITE_BLOB = /\[\[([Pp0-9,;:\s[\]]+?)\]\]/g;
const ONE_REF = /P?(\d+)\s*[:.]\s*(\d+)/gi;

function withCitationLinks(text: string): string {
  return text.replace(CITE_BLOB, (whole, inner: string) => {
    const refs = [...inner.matchAll(ONE_REF)];
    if (!refs.length) return whole;
    return refs.map(([, p, s]) => `[P${p}:${s}](#cite-${p}-${s})`).join(' ');
  });
}

/** Renders an answer, with its citation links swapped for expandable chips. */
function Answer({
  text,
  papers,
  onOpenPaper,
}: {
  text: string;
  papers: StudyPaper[];
  onOpenPaper?: (documentId: string, sequenceId: number) => void;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={MARKDOWN_REMARK}
      rehypePlugins={MARKDOWN_REHYPE}
      components={{
        a({ href, children, ...rest }) {
          const m = /^#cite-(\d+)-(\d+)$/.exec(href || '');
          if (!m) return <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>{children}</a>;
          const paper = papers[Number(m[1]) - 1];
          // A citation into a paper the study no longer holds cannot be
          // opened. Plain struck-through text is honest; a dead button is not.
          if (!paper) return <span className="cite-dead">P{m[1]}:{m[2]}</span>;
          return (
            <CitationRef
              cite={{
                paper: Number(m[1]),
                document_id: paper.id,
                label: paper.title,
                sequence_id: Number(m[2]),
              }}
              onOpenPaper={onOpenPaper}
            />
          );
        },
      }}
    >
      {withCitationLinks(text)}
    </ReactMarkdown>
  );
}

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
    <label className="model-picker" title="Which model answers here">
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

const OPENERS = [
  'What do these papers disagree about?',
  'Summarise each paper in two sentences.',
  'What does each one measure, and are the numbers comparable?',
  'What would I read first, and why?',
];

export function StudyChat({
  scopeName,
  papers,
  turns,
  pending,
  onAsk,
  onRetry,
  onClear,
  onOpenPaper,
  catalog,
  model,
  onModelChange,
}: {
  scopeName: string;
  papers: StudyPaper[];
  turns: StudyTurn[];
  pending: PendingTurn | null;
  onAsk: (question: string) => void;
  onRetry: () => void;
  onClear: () => void;
  onOpenPaper?: (documentId: string, sequenceId: number) => void;
  catalog: ModelCatalog | null;
  model: string;
  onModelChange: (name: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  // ⚠ Only autoscroll when the reader is already at the bottom. A long answer
  // streaming in while they are reading an earlier turn must not drag the view
  // away from what they are looking at.
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  useLayoutEffect(() => {
    if (!atBottom.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, pending?.answer, pending?.steps.length, pending?.status]);

  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, [scopeName]);

  const send = () => {
    const q = draft.trim();
    if (!q || pending) return;
    onAsk(q);
    setDraft('');
    atBottom.current = true;
  };

  const empty = turns.length === 0 && !pending;

  return (
    <section className="chat">
      <header className="chat-head">
        <div className="chat-head-title">
          <h2>{scopeName}</h2>
          <span className="chat-head-sub">
            {papers.length === 0
              ? 'no papers in scope'
              : `answers drawn from ${papers.length} paper${papers.length === 1 ? '' : 's'}`}
          </span>
        </div>
        {turns.length > 0 && (
          <button type="button" className="chat-clear" onClick={onClear}>
            Clear
          </button>
        )}
      </header>

      <div className="chat-scroll thin-scroll" ref={scrollRef} onScroll={onScroll}>
        {empty ? (
          <div className="chat-empty">
            <p className="chat-empty-lead">Ask across {papers.length || 'your'} papers.</p>
            <p>
              The assistant reads each paper's contents, fetches the sections your
              question turns on, and shows you every one it opened. Citations
              expand where they sit, so you can check a claim without leaving here.
            </p>
            {papers.length > 0 && (
              <div className="chat-openers">
                {OPENERS.map((q) => (
                  <button key={q} type="button" onClick={() => onAsk(q)}>{q}</button>
                ))}
              </div>
            )}
          </div>
        ) : (
          turns.map((turn) => (
            <Fragment key={turn.id}>
              {turn.role === 'user' ? (
                <div className="msg is-user"><div className="msg-body">{turn.content}</div></div>
              ) : (
                <div className="msg is-assistant">
                  <div className="msg-meta">
                    {turn.model && <span className="note-model">{turn.model}</span>}
                  </div>
                  <AgentTrail steps={turn.agent_steps} />
                  <div className="msg-body md-body">
                    <Answer text={turn.content} papers={papers} onOpenPaper={onOpenPaper} />
                  </div>
                  {turn.cited.length > 0 && (
                    <div className="msg-sources">
                      <span className="msg-sources-label">Read from</span>
                      {[...new Set(turn.cited.map((c) => c.paper))].map((p) => (
                        <span key={p} className="msg-source">
                          P{p} · {papers[p - 1]?.title ?? 'a paper no longer in scope'}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </Fragment>
          ))
        )}

        {pending && (
          <>
            <div className="msg is-user"><div className="msg-body">{pending.question}</div></div>
            <div className="msg is-assistant">
              {/* Live, the trail IS the progress indicator: the answer has not
                  started yet and a bare spinner says nothing about what it is
                  doing across five papers. */}
              <AgentTrail steps={pending.steps} live />
              {pending.error ? (
                <>
                  <div className="note-error">{pending.error}</div>
                  <div className="note-actions"><button type="button" onClick={onRetry}>Retry</button></div>
                </>
              ) : pending.answer ? (
                <div className="msg-body md-body">
                  <Answer
                    text={maskIncompleteMath(pending.answer)}
                    papers={papers}
                    onOpenPaper={onOpenPaper}
                  />
                </div>
              ) : (
                <div className="note-status">
                  <span className="note-dot" />
                  {pending.status || 'Thinking…'}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      <div className="chat-composer">
        <textarea
          ref={inputRef}
          rows={2}
          value={draft}
          disabled={!!pending}
          placeholder={
            papers.length ? `Ask ${scopeName}…` : 'Add a paper to this study first…'
          }
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="chat-composer-row">
          <ModelPicker catalog={catalog} model={model} onChange={onModelChange} />
          <button
            type="button"
            className="chat-send"
            onClick={send}
            disabled={!draft.trim() || !!pending}
          >
            {pending ? 'Working…' : 'Ask'}
          </button>
        </div>
      </div>
    </section>
  );
}
