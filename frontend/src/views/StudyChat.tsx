import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type AnchorHTMLAttributes } from 'react';
import { m, useReducedMotion } from 'motion/react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE, MARKDOWN_COMPONENTS } from '../lib/markdown';
import { maskIncompleteMath } from '../lib/pacer';
import { useAutoGrowTextarea } from '../lib/useAutoGrowTextarea';
import { Reasoning } from './AgentTrail';
import { EvidencePanel } from './EvidencePanel';
import { CitationRef } from './CitationRef';
import { ModelPicker } from '../components/ModelPicker';
import type { AgentStep, ConversationSummary, ModelCatalog, StudyPaper, StudyTurn } from '../api';
import { textDirection } from '../lib/documentDirection';
import { playful, Pressable, reducedMotionFade } from '../motion';
import { StreamingCaret } from './StreamingCaret';
import { AnswerViewport } from '../components/AnswerViewport';

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
  notice?: string | null;
  /** The answer is complete; the evidence check is running (see api.ts). */
  verifying: boolean;
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
  streaming = false,
}: {
  text: string;
  papers: StudyPaper[];
  onOpenPaper?: (documentId: string, sequenceId: number) => void;
  streaming?: boolean;
}) {
  // The study owner currently passes an inline `onOpenPaper` callback. Keep
  // current values in refs so ReactMarkdown's component map can stay stable
  // while a streamed answer grows; otherwise React remounts CitationRef and
  // discards its open state and fetched passage on every token.
  const papersRef = useRef(papers);
  papersRef.current = papers;
  const onOpenPaperRef = useRef(onOpenPaper);
  onOpenPaperRef.current = onOpenPaper;
  const components = useMemo(() => ({
    // Shared first: images and diagrams. The citation anchor below is the
    // desk's own and must win, so it is spread after.
    ...MARKDOWN_COMPONENTS,
    a({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) {
      const m = /^#cite-(\d+)-(\d+)$/.exec(href || '');
      if (!m) return <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>{children}</a>;
      const currentPapers = papersRef.current;
      const paper = currentPapers[Number(m[1]) - 1];
      // A citation into a paper the study no longer holds cannot be
      // opened. Plain struck-through text is honest; a dead button is not.
      if (!paper) return <span className="cite-dead">P{m[1]}:{m[2]}</span>;
      return (
        <CitationRef
          key={`${paper.id}:${m[2]}`}
          cite={{
            paper: Number(m[1]),
            document_id: paper.id,
            label: paper.title,
            sequence_id: Number(m[2]),
          }}
          onOpenPaper={onOpenPaperRef.current}
        />
      );
    },
  }), []);

  return (
    <AnswerViewport streaming={streaming} dir={textDirection(text) ?? 'auto'}>
      <ReactMarkdown
        remarkPlugins={MARKDOWN_REMARK}
        rehypePlugins={MARKDOWN_REHYPE}
        components={components}
      >
        {withCitationLinks(text)}
      </ReactMarkdown>
    </AnswerViewport>
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
  conversations,
  conversationId,
  onSelectConversation,
  onNewChat,
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
  /** Deletes the conversation on screen. */
  onClear: () => void;
  /** The scope's past conversations, most recent first, and the one on screen. */
  conversations: ConversationSummary[];
  conversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  onOpenPaper?: (documentId: string, sequenceId: number) => void;
  catalog: ModelCatalog | null;
  model: string;
  onModelChange: (name: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrowTextarea(inputRef, draft, 200);
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
  const reducedMotion = useReducedMotion();
  const messageInitial = reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.6, rotate: -3, y: 12 };
  const messageAnimate = reducedMotion
    ? { opacity: 1 }
    : { opacity: 1, scale: 1, rotate: 0, y: 0 };
  const messageTransition = reducedMotion ? reducedMotionFade : playful;

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
        <ConversationSwitch
          conversations={conversations}
          conversationId={conversationId}
          canStartNew={turns.length > 0 || pending !== null}
          onSelect={onSelectConversation}
          onNew={onNewChat}
        />
        {turns.length > 0 && (
          <button type="button" className="chat-clear" onClick={onClear} title="Delete this conversation">
            Delete chat
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
                <m.div
                  key={`${turn.id}-message`}
                  data-testid="study-message-motion"
                  data-study-message-key={turn.id}
                  initial={false}
                  animate={messageAnimate}
                  transition={messageTransition}
                  style={{ transformOrigin: 'center' }}
                >
                  <div className="msg is-user"><div className="msg-body" dir={textDirection(turn.content) ?? 'auto'}>{turn.content}</div></div>
                </m.div>
              ) : (
                <m.div
                  key={`${turn.id}-message`}
                  data-testid="study-message-motion"
                  data-study-message-key={turn.id}
                  initial={false}
                  animate={messageAnimate}
                  transition={messageTransition}
                  style={{ transformOrigin: 'center' }}
                  className="msg is-assistant"
                >
                  <div className="msg-meta">
                    {turn.model && <span className="note-model">{turn.model}</span>}
                  </div>
                  <Reasoning steps={turn.agent_steps} />
                  <div className="msg-body md-body">
                    <Answer text={turn.content} papers={papers} onOpenPaper={onOpenPaper} />
                  </div>
                  {/* Evidence spans papers here, so each quote names its P-number. */}
                  <EvidencePanel
                    report={turn.grounding}
                    onJump={onOpenPaper ? (doc, seq) => { if (doc) onOpenPaper(doc, seq); } : undefined}
                    paperLabel={(doc) => {
                      const i = papers.findIndex((p) => p.id === doc);
                      return i === -1 ? null : `P${i + 1}`;
                    }}
                  />
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
                </m.div>
              )}
            </Fragment>
          ))
        )}

        {pending && (
          <>
            <m.div
              key={`${pending.clientId}-user`}
              data-testid="study-message-motion"
              data-study-message-key={`${pending.clientId}-user`}
              initial={messageInitial}
              animate={messageAnimate}
              transition={messageTransition}
              style={{ transformOrigin: 'center' }}
            >
              <div className="msg is-user"><div className="msg-body" dir={textDirection(pending.question) ?? 'auto'}>{pending.question}</div></div>
            </m.div>
            <m.div
              key={`${pending.clientId}-assistant`}
              data-testid="study-message-motion"
              data-study-message-key={`${pending.clientId}-assistant`}
              data-streaming={pending.answer && !pending.verifying && !pending.error ? 'true' : undefined}
              initial={messageInitial}
              animate={messageAnimate}
              transition={messageTransition}
              style={{ transformOrigin: 'center' }}
              className="msg is-assistant"
            >
              {/* The mascot status gives progress before the first tool step
                  arrives and while answer text streams. */}
              <Reasoning
                steps={pending.steps}
                live={!pending.error}
                writing={Boolean(pending.answer)}
              />
              {pending.notice && (
                <div className="model-fallback-notice" role="status">{pending.notice}</div>
              )}
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
                    streaming={!pending.verifying}
                  />
                  {!pending.verifying && <StreamingCaret />}
                </div>
              ) : null}
              {pending.answer && !pending.error && (
                <EvidencePanel report={null} verifying={pending.verifying} />
              )}
            </m.div>
          </>
        )}
      </div>

      <div className="chat-composer">
        <textarea
          dir="auto"
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
          <ModelPicker
            catalog={catalog}
            model={model}
            onChange={onModelChange}
            title="Which model answers here"
          />
          <Pressable
            type="button"
            className="chat-send"
            onClick={send}
            disabled={!draft.trim() || !!pending}
          >
            {pending ? 'Working…' : 'Ask'}
          </Pressable>
        </div>
      </div>
    </section>
  );
}

/**
 * "Chats · N ▾" and "+ New chat" — the same pair the book reader's pane has,
 * so a reader who learned it there finds it here. The list shows each
 * conversation's first question, its turn count and when it was last used.
 */
function ConversationSwitch({
  conversations, conversationId, canStartNew, onSelect, onNew,
}: {
  conversations: ConversationSummary[];
  conversationId: string | null;
  canStartNew: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div ref={ref} className="chat-convs">
      {conversations.length > 0 && (
        <button type="button" className="chat-convs-toggle" onClick={() => setOpen((v) => !v)} title="Switch chat" aria-expanded={open}>
          Chats · {conversations.length} <span aria-hidden="true">▾</span>
        </button>
      )}
      <button
        type="button"
        className="chat-convs-toggle"
        onClick={() => { setOpen(false); onNew(); }}
        disabled={!canStartNew && conversationId === null}
        title="Start a new chat in this scope. The current one stays in the list"
      >
        + New chat
      </button>
      {open && (
        <div className="chat-convs-list" role="menu">
          <div className="chat-convs-head">This scope · {conversations.length} chat{conversations.length !== 1 ? 's' : ''}</div>
          <div className="chat-convs-scroll thin-scroll">
            {conversations.map((c) => {
              const active = c.conversation_id === conversationId;
              const exchanges = Math.floor(c.turn_count / 2);
              return (
                <button
                  key={c.conversation_id}
                  type="button"
                  role="menuitem"
                  className={`chat-convs-row${active ? ' is-on' : ''}`}
                  onClick={() => { setOpen(false); onSelect(c.conversation_id); }}
                >
                  <span className="chat-convs-title">{c.first_user_message?.trim() || 'Untitled chat'}</span>
                  <span className="chat-convs-meta">
                    {exchanges} exchange{exchanges === 1 ? '' : 's'}
                    {c.last_at ? ` · ${new Date(c.last_at).toLocaleDateString()}` : ''}
                    {active ? ' · open' : ''}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
