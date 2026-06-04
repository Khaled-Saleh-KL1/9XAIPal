import { useState, useRef, useEffect, useCallback, type ImgHTMLAttributes, type AnchorHTMLAttributes } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import type { ChatMessage } from '../types';
import { IconSend, IconSpinner } from '../components/Icons';
import {
  askPaper, getPaperChat, listPaperConversations,
  type Citation, type ConversationSummary,
} from '../api';

const MARKDOWN_REMARK = [remarkGfm, remarkMath];
const MARKDOWN_REHYPE = [rehypeRaw, rehypeKatex];

// Lightbox is opened by dispatching a CustomEvent — keeps the markdown
// renderer at module scope (no React state needed) while letting the
// ChatPane (or any other component) listen and show the overlay.
type LightboxDetail = { src: string; alt?: string };
function openLightbox(detail: LightboxDetail) {
  window.dispatchEvent(new CustomEvent<LightboxDetail>('pal:lightbox', { detail }));
}

// Custom renderers for markdown nodes that need styling in chat.
// Image: render as a centered figure with a thin border + optional alt caption
// underneath, and lazy-load. Click opens the full image in a centered lightbox
// with a blurred backdrop (handled inside ChatPane).
//
// We use a small SafeWebImage wrapper so we can cleanly handle hotlink failures
// (common with images returned by SearXNG / web research). Instead of a broken
// red X, we show a helpful fallback with a direct link to the original source.
const SafeWebImage: React.FC<ImgHTMLAttributes<HTMLImageElement>> = ({ src, alt, ...rest }) => {
  const [failed, setFailed] = useState(false);

  if (!src) return null;

  if (failed) {
    // Graceful fallback when the image host blocks hotlinking (very common).
    return (
      <div
        className="my-3 rounded-md border px-3 py-2 text-[12px] font-mono"
        style={{
          borderColor: 'var(--border)',
          background: 'var(--bg-2)',
          color: 'var(--muted)',
        }}
      >
        <div>Image blocked by source (hotlink protection)</div>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="underline"
          style={{ color: 'var(--accent)' }}
        >
          Open original image in new tab →
        </a>
        {alt && <div className="mt-1 opacity-70">{alt}</div>}
      </div>
    );
  }

  return (
    <span className="block my-3">
      <img
        src={src}
        alt={alt || ''}
        loading="lazy"
        referrerPolicy="no-referrer"
        onClick={() => openLightbox({ src, alt: alt || undefined })}
        title="Click to enlarge"
        onError={() => setFailed(true)}
        style={{
          maxWidth: '100%',
          maxHeight: 360,
          borderRadius: 6,
          border: '1px solid var(--border)',
          background: 'var(--bg-2)',
          display: 'block',
          margin: '0 auto',
          cursor: 'zoom-in',
        }}
        {...rest}
      />
      {alt && (
        <span
          className="block text-center mt-1 text-[11px] font-mono"
          style={{ color: 'var(--muted)' }}
        >
          {alt}
        </span>
      )}
    </span>
  );
};

const MD_COMPONENTS = {
  img: SafeWebImage,
  a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} target="_blank" rel="noreferrer noopener" />
  ),
};

interface Props {
  paperId: string;
  currentSequenceOrder: number | null;
  revealedCount: number;
}

// Belt-and-suspenders: the backend prompt now forbids a Sources section, but
// if an older turn (or a stubborn model) still emits "Sources: None." or
// "**Sources:** None" as the last line, strip it so it doesn't render as raw
// text below the answer.
function stripTrailingSourcesNone(text: string): string {
  if (!text) return text;
  return text.replace(
    /\n+\s*\**\s*(Sources?|References?|Citations?)\s*\**\s*:?\s*(None|N\/A|—|-)?\s*\.?\s*$/i,
    '',
  );
}

// Normalize math syntax the model often emits in shapes remark-math won't pick up:
// - LaTeX-native delimiters `\(...\)` and `\[...\]` → `$...$` / `$$...$$`
// - Orphan `\begin{env}...\end{env}` blocks not wrapped in `$$...$$`
// - Stray trailing `$$` next to an already-unwrapped block (model bug)
function normalizeMath(text: string): string {
  if (!text) return text;
  let out = text;
  // \[ ... \]  → $$ ... $$
  out = out.replace(/\\\[(.+?)\\\]/gs, (_, body) => `\n$$${body}$$\n`);
  // \( ... \)  → $ ... $
  out = out.replace(/\\\((.+?)\\\)/g, (_, body) => `$${body}$`);

  // Wrap orphan \begin{env}...\end{env} in $$...$$ when not already wrapped.
  // Allows an optional trailing $$ that the model emitted without an opener.
  const mathEnvs = 'cases|align\\*?|aligned|equation\\*?|matrix|pmatrix|bmatrix|vmatrix|gather\\*?|split|array';
  const blockRe = new RegExp(
    `(?<!\\$)\\\\begin\\{(${mathEnvs})\\}([\\s\\S]*?)\\\\end\\{\\1\\}(\\s*\\$\\$)?`,
    'g',
  );
  out = out.replace(blockRe, (_m, env, body) => `\n$$\n\\begin{${env}}${body}\\end{${env}}\n$$\n`);

  return out;
}

function citationsToRefs(citations: Citation[] | null | undefined): string[] {
  if (!citations) return [];
  return citations
    .map((c) =>
      c.text_snippet || c.source || (c.sequence_id ? `§${c.sequence_id}` : '') || ''
    )
    .filter(Boolean);
}

function previewLabel(c: ConversationSummary): string {
  const raw = (c.first_user_message || '').trim();
  if (!raw) return 'Untitled chat';
  return raw.length > 48 ? raw.slice(0, 48) + '…' : raw;
}

export function ChatPane({ paperId, currentSequenceOrder, revealedCount }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  // Image attachments for the next /ask call. Stored as { dataUrl, name } so
  // we can show a thumbnail in the input area; we strip the data: prefix when
  // sending to the backend (Ollama wants raw base64).
  const [attachments, setAttachments] = useState<{ dataUrl: string; name: string }[]>([]);
  const [dragOver, setDragOver] = useState(false);
  // Lightbox state — opened when any chat image dispatches `pal:lightbox`.
  const [lightbox, setLightbox] = useState<LightboxDetail | null>(null);

  // === Sub-thread (nested tangent) state ===
  // Stack of sub-thread roots the user has navigated into. Length encodes depth:
  //   []                          → main chat (depth 0)
  //   [r1]                        → first sub-thread (depth 1)
  //   [r1, r2]                    → sub-sub (depth 2)
  //   [r1, r2, r3]                → sub-sub-sub (depth 3, MAX)
  // Stored as objects so the "back one level" button can show a preview.
  type ThreadFrame = { rootTurnId: string; preview: string };
  const [threadStack, setThreadStack] = useState<ThreadFrame[]>([]);
  const [maxDepth, setMaxDepth] = useState<number>(3);
  const currentThreadRoot = threadStack.length > 0 ? threadStack[threadStack.length - 1].rootTurnId : null;
  const currentDepth = threadStack.length;
  const atMaxDepth = currentDepth >= maxDepth;

  useEffect(() => {
    function onOpen(e: Event) {
      const ce = e as CustomEvent<LightboxDetail>;
      if (ce.detail?.src) setLightbox(ce.detail);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        if (lightbox) {
          setLightbox(null);
        } else if (threadStack.length > 0) {
          // ESC pops one sub-thread level (L3 → L2 → L1 → main)
          popSubThread();
        }
      }
    }
    window.addEventListener('pal:lightbox', onOpen as EventListener);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pal:lightbox', onOpen as EventListener);
      window.removeEventListener('keydown', onKey);
    };
  }, [lightbox, threadStack]);

  // Read a File as a data URL via FileReader so it survives the React render.
  // Rejects non-image MIME types and files over 8 MB so a stray drop doesn't
  // freeze the page or blow up the JSON payload.
  const ingestFile = useCallback(async (file: File) => {
    if (!file.type.startsWith('image/')) {
      console.warn('Ignored non-image drop:', file.type);
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      alert(`Image too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max 8 MB.`);
      return;
    }
    const reader = new FileReader();
    const dataUrl: string = await new Promise((resolve, reject) => {
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    setAttachments((prev) => [...prev, { dataUrl, name: file.name }]);
  }, []);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, thinking, scrollToBottom]);

  // Close the picker when clicking outside of it.
  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [pickerOpen]);

  // Reset whenever the paper changes — each paper has its own threads only.
  useEffect(() => {
    let alive = true;
    setMessages([]);
    setConversationId(null);
    setConversations([]);
    setPickerOpen(false);
    setThreadStack([]);

    (async () => {
      try {
        const convs = await listPaperConversations(paperId);
        if (!alive) return;
        setConversations(convs);
        // Resume the most recent thread if there is one.
        if (convs.length > 0) {
          const head = convs[0];
          const { turns, maxDepth: md } = await getPaperChat(paperId, head.conversation_id);
          if (!alive) return;
          setMaxDepth(md);
          setConversationId(head.conversation_id);
          setMessages(turns.map((t) => ({
            role: t.role,
            text: t.content,
            refs: t.role === 'assistant' ? citationsToRefs(t.citations) : undefined,
            parentTurnId: t.parent_turn_id ?? undefined,
            threadRootTurnId: t.thread_root_turn_id ?? undefined,
          })));
        }
      } catch {
        // Backend hiccup — leave the pane empty and let the user retry.
      }
    })();

    return () => { alive = false; };
  }, [paperId]);

  const refreshConversations = useCallback(async () => {
    try {
      const convs = await listPaperConversations(paperId);
      setConversations(convs);
    } catch {
      // Non-fatal — picker just won't reflect the newest thread.
    }
  }, [paperId]);

  const startNewChat = useCallback(() => {
    setMessages([]);
    setConversationId(null);
    setInput('');
    setPickerOpen(false);
    textareaRef.current?.focus();
  }, []);

  const selectConversation = useCallback(async (convId: string) => {
    setPickerOpen(false);
    if (convId === conversationId) return;
    setConversationId(convId);
    setMessages([]);
    setThreadStack([]); // always exit any sub-thread when switching top-level chats
    try {
      const { turns, maxDepth: md } = await getPaperChat(paperId, convId);
      setMaxDepth(md);
      setMessages(turns.map((t) => ({
        role: t.role,
        text: t.content,
        refs: t.role === 'assistant' ? citationsToRefs(t.citations) : undefined,
        parentTurnId: t.parent_turn_id ?? undefined,
        threadRootTurnId: t.thread_root_turn_id ?? undefined,
      })));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setMessages([{ role: 'assistant', text: `Backend error: ${detail}` }]);
    }
  }, [paperId, conversationId]);

  // Load turns for whatever sub-thread / main view the stack currently encodes,
  // and update local depth state from the server's response. Used by every
  // navigation transition so frontend depth never disagrees with the backend.
  const loadFromStack = useCallback(async (stack: ThreadFrame[]) => {
    if (!conversationId) return;
    const tail = stack.length > 0 ? stack[stack.length - 1].rootTurnId : null;
    try {
      const { turns, depth: serverDepth, maxDepth: md } = await getPaperChat(
        paperId,
        conversationId,
        tail ?? undefined,
      );
      setMaxDepth(md);
      // Server depth is authoritative — if it disagrees, trust it.
      // (Should always equal stack.length; this guards against stale UI state.)
      if (serverDepth !== stack.length && tail === null) {
        // Main chat: server says 0, stack length must be 0.
      }
      setMessages(turns.map((t) => ({
        role: t.role,
        text: t.content,
        refs: t.role === 'assistant' ? citationsToRefs(t.citations) : undefined,
        parentTurnId: t.parent_turn_id ?? undefined,
        threadRootTurnId: t.thread_root_turn_id ?? undefined,
      })));
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setMessages([{ role: 'assistant', text: `Failed to load thread: ${detail}` }]);
    }
  }, [paperId, conversationId]);

  // Enter a sub-thread (clicking "Thread →" on a message bubble).
  // Pushes onto the navigation stack; refuses to go deeper than maxDepth.
  const enterSubThread = useCallback(async (rootTurnId: string, rootPreview?: string) => {
    if (!conversationId) return;
    if (threadStack.length >= maxDepth) {
      // UI should already hide the affordance; this is belt-and-suspenders.
      return;
    }
    const nextStack = [...threadStack, { rootTurnId, preview: rootPreview ?? '' }];
    setThreadStack(nextStack);
    setMessages([]);
    await loadFromStack(nextStack);
  }, [conversationId, threadStack, maxDepth, loadFromStack]);

  // Back one level (ESC, "← Up one level" button).
  const popSubThread = useCallback(async () => {
    if (threadStack.length === 0) return;
    const nextStack = threadStack.slice(0, -1);
    setThreadStack(nextStack);
    setMessages([]);
    await loadFromStack(nextStack);
  }, [threadStack, loadFromStack]);

  // Jump all the way back to the main chat ("← Back to main chat" button).
  const exitSubThread = useCallback(async () => {
    if (threadStack.length === 0) return;
    setThreadStack([]);
    setMessages([]);
    await loadFromStack([]);
  }, [threadStack, loadFromStack]);

  const send = async () => {
    const q = input.trim();
    // Allow sending an image-only message ("what's in this picture?") when
    // the text is empty but attachments are present.
    if ((!q && attachments.length === 0) || thinking) return;
    const wasNewChat = conversationId === null;

    // Strip the data:image/...;base64, prefix; backend expects raw base64.
    const imagesB64 = attachments.map((a) => a.dataUrl.replace(/^data:[^,]+,/, ''));
    // Show what was sent in the user bubble: text + small thumbnails.
    const userText = q || '(image attached)';
    const userImageMarkdown = attachments.map((a) => `![${a.name}](${a.dataUrl})`).join('\n\n');
    const userBubbleText = userImageMarkdown
      ? `${userText}\n\n${userImageMarkdown}`
      : userText;

    setInput('');
    setAttachments([]);
    setMessages((prev) => [...prev, { role: 'user', text: userBubbleText }]);
    setThinking(true);

    try {
      const threadOpts = currentThreadRoot
        ? {
            parentTurnId: currentThreadRoot, // for the first continuation; deeper levels can be refined by frontend if needed
            threadRootTurnId: currentThreadRoot,
          }
        : undefined;

      const res = await askPaper(
        paperId,
        q || 'Describe / explain the attached image in the context of this paper.',
        currentSequenceOrder,
        conversationId,
        imagesB64.length > 0
          ? { imagesB64, ...threadOpts }
          : threadOpts,
      );
      const newConvId = res.conversation_id || conversationId;
      if (res.conversation_id) setConversationId(res.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: res.answer,
          refs: citationsToRefs(res.citations),
          researchPerformed: res.research_performed,
          researchSummary: res.research_summary || undefined,
        },
      ]);
      // Refetch so the freshly-created user/assistant turns gain their server
      // ids — without this the "Thread →" affordance never appears on a new
      // exchange (it depends on threadRootTurnId, which only the backend
      // emits). Scoped to the current sub-thread when applicable.
      if (newConvId) {
        try {
          const { turns, maxDepth: md } = await getPaperChat(paperId, newConvId, currentThreadRoot ?? undefined);
          setMaxDepth(md);
          setMessages(turns.map((t) => ({
            role: t.role,
            text: t.content,
            refs: t.role === 'assistant' ? citationsToRefs(t.citations) : undefined,
            parentTurnId: t.parent_turn_id ?? undefined,
            threadRootTurnId: t.thread_root_turn_id ?? undefined,
          })));
        } catch {
          // non-fatal — the optimistic bubbles are already on screen
        }
      }
      if (wasNewChat) refreshConversations();
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: `Backend error: ${detail}` },
      ]);
    } finally {
      setThinking(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <aside
      className="flex flex-col min-h-0"
      style={{ background: 'var(--bg-2)' }}
    >
      {/* pane header */}
      <div
        className="px-6 py-3.5 flex items-center gap-2 shrink-0 relative"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--accent)' }} />
        <span className="font-serif text-[14px] tracking-tight" style={{ color: 'var(--fg)' }}>
          Ask this paper
        </span>

        {/* Sub-thread header (only visible when inside a tangent) */}
        {currentThreadRoot && (
          <>
            <div className="ml-3 flex items-center gap-2 text-[11px] font-mono px-2 py-0.5 rounded"
                 style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
                 title={`Sub-thread depth ${currentDepth} of ${maxDepth} (max)`}>
              SUB-THREAD · L{currentDepth}/{maxDepth}
            </div>
            {currentDepth > 1 && (
              <button
                onClick={popSubThread}
                className="ml-2 text-[11px] font-mono px-2.5 py-1 rounded flex items-center gap-1"
                style={{ border: '1px solid var(--border)', background: 'var(--bg)' }}
                title="Step up one sub-thread level (ESC also works)"
              >
                ← Up one level
              </button>
            )}
            <button
              onClick={exitSubThread}
              className="ml-2 text-[11px] font-mono px-2.5 py-1 rounded flex items-center gap-1"
              style={{ border: '1px solid var(--border)', background: 'var(--bg)' }}
              title="Exit all sub-threads and return to the main paper chat"
            >
              ← Back to main
            </button>
            <span className="text-[10px] font-mono ml-1" style={{ color: 'var(--muted)' }}>
              (ESC = up one)
            </span>
          </>
        )}

        <div ref={pickerRef} className="ml-auto flex items-center gap-1.5 relative">
          {conversations.length > 0 && (
            <button
              onClick={() => setPickerOpen((v) => !v)}
              className="text-[11.5px] font-mono px-2 py-1 rounded flex items-center gap-1"
              style={{
                border: '1px solid var(--border)',
                background: 'var(--bg)',
                color: 'var(--fg-2)',
              }}
              title="Switch chat"
            >
              <span>Chats · {conversations.length}</span>
              <span style={{ color: 'var(--muted)' }}>▾</span>
            </button>
          )}
          <button
            onClick={startNewChat}
            disabled={conversationId === null && messages.length === 0}
            className="text-[11.5px] font-mono px-2 py-1 rounded flex items-center gap-1"
            style={{
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              color: conversationId === null && messages.length === 0
                ? 'var(--muted)'
                : 'var(--fg-2)',
              opacity: conversationId === null && messages.length === 0 ? 0.55 : 1,
            }}
            title="Start a new chat for this paper"
          >
            + New chat
          </button>
          <span className="text-[11px] font-mono ml-1" style={{ color: 'var(--muted)' }}>
            /ask
          </span>

          {pickerOpen && (
            <div
              className="absolute right-0 top-full mt-1.5 w-[280px] rounded-lg overflow-hidden z-10 shadow-lg"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
            >
              <div
                className="px-3 py-1.5 text-[10.5px] font-mono uppercase tracking-wider"
                style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}
              >
                This paper · {conversations.length} chat{conversations.length !== 1 ? 's' : ''}
              </div>
              <div className="max-h-[300px] overflow-y-auto thin-scroll">
                {conversations.map((c) => {
                  const active = c.conversation_id === conversationId;
                  return (
                    <button
                      key={c.conversation_id}
                      onClick={() => selectConversation(c.conversation_id)}
                      className="w-full text-left px-3 py-2 flex items-start gap-2"
                      style={{
                        background: active ? 'var(--bg-2)' : 'transparent',
                        borderTop: '1px solid var(--border)',
                      }}
                      onMouseEnter={(e) => {
                        if (!active) e.currentTarget.style.background = 'var(--bg-2)';
                      }}
                      onMouseLeave={(e) => {
                        if (!active) e.currentTarget.style.background = 'transparent';
                      }}
                    >
                      <div className="flex-1 min-w-0">
                        <div
                          className="text-[12.5px] leading-snug truncate"
                          style={{ color: 'var(--fg)' }}
                        >
                          {previewLabel(c)}
                        </div>
                        <div
                          className="text-[10.5px] font-mono mt-0.5"
                          style={{ color: 'var(--muted)' }}
                        >
                          {c.turn_count} turn{c.turn_count !== 1 ? 's' : ''}
                          {c.last_at && ' · ' + new Date(c.last_at).toLocaleString([], {
                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                          })}
                        </div>
                      </div>
                      {active && (
                        <span className="text-[10px] font-mono mt-0.5" style={{ color: 'var(--accent)' }}>
                          •
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* message history */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto thin-scroll">
        <div className="px-6 py-6 space-y-6">
          {messages.length === 0 && (
            <p className="text-[13px]" style={{ color: 'var(--muted)' }}>
              {conversationId === null
                ? 'New chat. Ask anything grounded in this paper.'
                : 'Ask anything grounded in the chunks you’ve revealed.'}
            </p>
          )}
          {messages.map((m, i) => {
            // For user bubbles, inherit the threadRootTurnId from the next
            // assistant bubble (same exchange) so the user message is also
            // clickable to enter the sub-thread.
            const pairRoot = m.threadRootTurnId
              ?? (m.role === 'user' && messages[i + 1]?.role === 'assistant'
                    ? messages[i + 1].threadRootTurnId
                    : undefined);
            // Hide the affordance when the user is already at the maximum
            // allowed sub-thread depth — opening a deeper sub-thread would
            // exceed the cap and the backend would reject it anyway.
            const canOpen = !!pairRoot && !atMaxDepth;
            return (
              <MessageBubble
                key={i}
                m={m}
                onOpenThread={canOpen ? () => enterSubThread(pairRoot!, m.text.slice(0, 60)) : undefined}
              />
            );
          })}
          {thinking && (
            <div className="flex items-center gap-2 text-[12.5px]" style={{ color: 'var(--muted)' }}>
              <IconSpinner className="w-3.5 h-3.5 spin" />
              <span>thinking…</span>
              <span className="opacity-60">(may include live research)</span>
            </div>
          )}
        </div>
      </div>

      {/* input — supports drag-drop / paste / file-picker for image attachments */}
      <div
        className="px-4 py-3 shrink-0 relative"
        style={{ borderTop: '1px solid var(--border)', background: 'var(--bg)' }}
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={async (e) => {
          e.preventDefault();
          setDragOver(false);
          const files = Array.from(e.dataTransfer.files);
          for (const f of files) await ingestFile(f);
        }}
      >
        {/* thumbnail strip — visible while images are attached, removable */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2 px-1">
            {attachments.map((a, i) => (
              <div
                key={`${a.name}-${i}`}
                className="relative group rounded overflow-hidden"
                style={{ border: '1px solid var(--border)', background: 'var(--bg-2)' }}
              >
                <img
                  src={a.dataUrl}
                  alt={a.name}
                  style={{ width: 56, height: 56, objectFit: 'cover', display: 'block' }}
                />
                <button
                  type="button"
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                  title={`Remove ${a.name}`}
                  className="absolute top-0 right-0 w-4 h-4 flex items-center justify-center text-[11px] leading-none"
                  style={{ background: 'rgba(0,0,0,0.6)', color: 'white' }}
                >×</button>
              </div>
            ))}
          </div>
        )}

        {/* dropzone visual overlay while dragging */}
        {dragOver && (
          <div
            className="absolute inset-2 rounded-lg flex items-center justify-center pointer-events-none z-10 text-[12px] font-mono"
            style={{
              background: 'color-mix(in oklch, var(--accent), transparent 85%)',
              border: '2px dashed var(--accent)',
              color: 'var(--accent)',
            }}
          >
            drop image to attach
          </div>
        )}

        <div
          className="flex items-end gap-2 rounded-lg px-3 py-2.5"
          style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
        >
          <span
            className="text-[12px] font-mono pt-0.5 select-none"
            style={{ color: 'var(--muted)' }}
          >
            /ask
          </span>
          {/* file-picker button (paperclip) — equivalent to drag-drop */}
          <label
            className="cursor-pointer text-[14px] select-none pt-0.5"
            title="Attach image"
            style={{ color: 'var(--muted)' }}
          >
            📎
            <input
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={async (e) => {
                const files = Array.from(e.target.files || []);
                for (const f of files) await ingestFile(f);
                e.target.value = '';
              }}
            />
          </label>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onPaste={async (e) => {
              // Capture pasted images (e.g. Cmd+V of a screenshot)
              const items = e.clipboardData?.items;
              if (!items) return;
              for (let i = 0; i < items.length; i++) {
                const it = items[i];
                if (it.kind === 'file' && it.type.startsWith('image/')) {
                  e.preventDefault();
                  const f = it.getAsFile();
                  if (f) await ingestFile(f);
                }
              }
            }}
            rows={1}
            placeholder={attachments.length > 0 ? 'add a question about the image (optional)…' : 'why is τ so small here?'}
            className="flex-1 bg-transparent resize-none text-[13.5px] leading-[1.55]"
            style={{
              color: 'var(--fg)',
              maxHeight: '120px',
              outline: 'none',
            }}
          />
          <button
            onClick={send}
            disabled={!input.trim() && attachments.length === 0}
            className="w-7 h-7 rounded flex items-center justify-center shrink-0"
            style={{
              background: 'var(--accent)',
              opacity: (input.trim() || attachments.length > 0) ? 1 : 0.3,
            }}
          >
            <IconSend className="w-3.5 h-3.5" style={{ color: 'var(--accent-fg)' }} />
          </button>
        </div>
        <div className="mt-2 flex items-center gap-3 px-1">
          <span className="text-[10.5px] font-mono" style={{ color: 'var(--muted)' }}>
            <kbd className="kbd">↵</kbd> send · <kbd className="kbd">⇧↵</kbd> newline
          </span>
          <span className="ml-auto text-[10.5px] font-mono" style={{ color: 'var(--muted)' }}>
            {revealedCount} chunk{revealedCount !== 1 ? 's' : ''} in context
          </span>
        </div>
      </div>

      {/* Fullscreen image lightbox — opens when any chat image is clicked.
          Click outside the image (or press Esc) to close. */}
      {lightbox && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setLightbox(null)}
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{
            background: 'color-mix(in oklch, var(--bg), transparent 25%)',
            backdropFilter: 'blur(14px)',
            WebkitBackdropFilter: 'blur(14px)',
            cursor: 'zoom-out',
          }}
        >
          {/* close button (top-right) */}
          <button
            onClick={(e) => { e.stopPropagation(); setLightbox(null); }}
            title="Close (Esc)"
            className="absolute top-4 right-5 w-9 h-9 rounded-full flex items-center justify-center text-[18px] leading-none"
            style={{
              background: 'rgba(0,0,0,0.55)',
              color: 'white',
              border: '1px solid rgba(255,255,255,0.2)',
            }}
          >
            ×
          </button>

          {/* the image itself: capped to ~92% viewport so it never crops.
              We also protect the lightbox viewer against hotlinked images. */}
          <LightboxImage src={lightbox.src} alt={lightbox.alt} />

          {/* caption + "open in tab" link (optional, doesn't dismiss on click) */}
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-4 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-full flex items-center gap-3 text-[12px] font-mono max-w-[80vw]"
            style={{
              background: 'rgba(0,0,0,0.55)',
              color: 'white',
              border: '1px solid rgba(255,255,255,0.15)',
            }}
          >
            {lightbox.alt && <span className="truncate">{lightbox.alt}</span>}
            <a
              href={lightbox.src}
              target="_blank"
              rel="noreferrer noopener"
              className="underline shrink-0"
              style={{ color: 'white' }}
            >
              open in tab
            </a>
          </div>
        </div>
      )}
    </aside>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────

// Small helper for the lightbox so we can safely handle load failures
// without violating React hook rules.
function LightboxImage({ src, alt }: { src: string; alt?: string }) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div
        className="px-6 py-8 text-center rounded-lg"
        style={{ background: 'var(--bg-2)', color: 'var(--muted)' }}
      >
        <div className="text-[13px] mb-2">Image could not be loaded (blocked by source)</div>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="underline text-[12px]"
          style={{ color: 'var(--accent)' }}
        >
          Open original URL in new tab
        </a>
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt || ''}
      referrerPolicy="no-referrer"
      onClick={(e) => e.stopPropagation()}
      onError={() => setFailed(true)}
      style={{
        maxWidth: '92vw',
        maxHeight: '88vh',
        borderRadius: 8,
        boxShadow: '0 30px 80px -20px rgba(0,0,0,0.55)',
        background: 'var(--bg-2)',
        cursor: 'default',
      }}
    />
  );
}

function MessageBubble({
  m,
  onOpenThread,
}: {
  m: ChatMessage;
  onOpenThread?: () => void;
}) {
  if (m.role === 'user') {
    const clickable = !!onOpenThread;
    return (
      <div className="flex justify-end">
        <div
          onClick={clickable ? () => onOpenThread!() : undefined}
          title={clickable ? 'Click to open this exchange in a focused sub-thread' : undefined}
          className={`max-w-[88%] rounded-2xl rounded-tr-sm px-3.5 py-2 text-[13.5px] ${clickable ? 'cursor-pointer hover:opacity-90' : ''}`}
          style={{
            background: 'var(--bg-3)',
            color: 'var(--fg)',
            border: clickable ? '1px dashed var(--accent)' : '1px solid transparent',
          }}
        >
          <span className="font-mono text-[11.5px] mr-1.5" style={{ color: 'var(--muted)' }}>
            /ask
          </span>
          {m.text}
        </div>
      </div>
    );
  }

  // Special rendering for automatic chat compaction summaries
  if (m.role === 'compaction') {
    return (
      <div className="flex flex-col gap-1.5 border-l-2 pl-3" style={{ borderColor: 'var(--accent)' }}>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono uppercase tracking-[1px]" style={{ color: 'var(--accent)' }}>
            COMPRESSED
          </span>
          <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
            Conversation compacted to keep context focused and reduce hallucination risk
          </span>
        </div>
        <div className="md-body text-[12.5px] leading-[1.55]" style={{ color: 'var(--fg-2)' }}>
          <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE} components={MD_COMPONENTS}>
            {normalizeMath(m.text)}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  const clickable = !!onOpenThread;
  return (
    <div
      onClick={clickable ? (e) => {
        // Don't hijack clicks on links, images, buttons inside the bubble
        const target = e.target as HTMLElement;
        if (target.closest('a, button, img, .md-body code')) return;
        onOpenThread!();
      } : undefined}
      title={clickable ? 'Click this exchange to open it in a focused sub-thread' : undefined}
      className={`flex flex-col gap-2 rounded-lg p-2 -m-2 ${clickable ? 'cursor-pointer hover:bg-[color:var(--bg-3)]' : ''}`}
    >
      <div className="flex items-center gap-2">
        <div
          className="w-5 h-5 rounded-full flex items-center justify-center"
          style={{ background: 'var(--accent)' }}
        >
          <span className="text-[10px] font-mono" style={{ color: 'var(--accent-fg)' }}>9</span>
        </div>
        <span className="text-[11.5px] font-mono" style={{ color: 'var(--muted)' }}>9xaipal</span>

        {m.researchPerformed && (
          <span
            className="text-[10px] font-mono px-1.5 py-0.5 rounded"
            style={{
              background: 'var(--accent)',
              color: 'var(--accent-fg)',
              letterSpacing: '0.5px',
            }}
            title={m.researchSummary || 'Live research was performed for this answer'}
          >
            RESEARCH
          </span>
        )}

        {/* "Thread →" affordance — only shown on assistant turns that start a sub-thread */}
        {onOpenThread && (
          <button
            onClick={(e) => { e.stopPropagation(); onOpenThread(); }}
            className="ml-auto text-[10px] font-mono px-2 py-0.5 rounded flex items-center gap-1"
            style={{
              border: '1px solid var(--accent)',
              color: 'var(--accent)',
              background: 'transparent',
            }}
            title="Continue this tangent in its own focused sub-thread (keeps the main paper chat clean)"
          >
            Thread →
            {m.threadRootTurnId && <span className="opacity-60">(open)</span>}
          </button>
        )}
      </div>
      <div className="md-body text-[13.5px] leading-[1.6]" style={{ color: 'var(--fg)' }}>
        <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE} components={MD_COMPONENTS}>
          {normalizeMath(stripTrailingSourcesNone(m.text))}
        </ReactMarkdown>
      </div>

      {m.researchPerformed && m.researchSummary && (
        <div className="text-[11px] font-mono mt-1" style={{ color: 'var(--muted)' }}>
          ↳ {m.researchSummary}
        </div>
      )}

      {m.refs && (
        <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
          {m.refs.map((r) => (
            <button
              key={r}
              className="text-[11px] font-mono px-1.5 py-0.5 rounded"
              style={{
                color: 'var(--fg-2)',
                border: '1px solid var(--border)',
                background: 'var(--bg)',
              }}
            >
              {r}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
