/**
 * API client for 9XAIPal backend.
 *
 * Base URL resolution:
 *  - Local dev: leave VITE_API_BASE_URL unset: requests stay relative
 *    ('/api/v1') and Vite proxies them to the backend on http://localhost:8000.
 *  - Hosted frontend: set VITE_API_BASE_URL to the backend's public origin.
 *    Otherwise the static host has no /api and every call 404s.
 */
import type { ContextType, User } from './types';

// Trailing slashes trimmed so `${API_ORIGIN}/api/v1` never doubles up.
const API_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
const BASE = `${API_ORIGIN}/api/v1`;

/**
 * Whether there is a backend to talk to at all: either an explicit origin was
 * configured, or we're on localhost where Vite proxies /api during dev. When
 * false, meaning a hosted frontend with no backend configured, API calls would hit a
 * static host and 404, so callers should surface NO_BACKEND_MESSAGE instead.
 */
export const HAS_BACKEND =
  API_ORIGIN !== '' ||
  (typeof window !== 'undefined' &&
    ['localhost', '127.0.0.1'].includes(window.location.hostname));

export const NO_BACKEND_MESSAGE =
  'No backend connected. This is a UI preview: run 9XAIPal locally, or set ' +
  'VITE_API_BASE_URL to a reachable backend (see the README).';

export interface PaperMeta {
  id: string;
  filename: string;
  original_filename: string;
  /** Reader-chosen display name. null = no override, fall back to the filename. */
  title?: string | null;
  file_size_bytes: number | null;
  page_count: number | null;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string | null;
  extractor?: string | null;            // "mineru" | "pymupdf_fallback" | "trafilatura" | "tavily-extract"
  doc_kind?: string | null;             // "book" | "paper" | "article"
  /** The page a doc_kind='article' row was imported from. null otherwise. */
  source_url?: string | null;
  reading_order?: number[] | null;
  // Fine-grained pipeline stage for in-flight papers (queued | extracting |
  // chunking | embedding | complete | failed). Drives the library's live
  // progress bar without an N+1 poll per card.
  job_status?: string | null;
  // Real progress *within* job_status (e.g. pages extracted / total while
  // extracting). null/undefined when nothing finer than the status exists.
  job_progress_fraction?: number | null;
  // Raw HTML snapshot crawl for a doc_kind='article' import — see
  // ProgressResponse above for the same fields' meaning.
  raw_snapshot_status?: string | null;
  raw_page_count?: number | null;
}

export interface ChunkData {
  id: string;
  paper_id: string;
  sequence_order: number;
  content_markdown: string;
  structural_type: string;
  plain_text: string;
  page_start: number | null;
  page_end: number | null;
  heading_path: string[] | null;
  image_url: string | null;
  image_refs?: string[] | null;
}

export interface Citation {
  chunk_id?: string;
  sequence_id?: number;
  page?: number;
  text_snippet?: string;
  url?: string;
  source?: string;
}

export interface AskResponse {
  answer: string;
  context_type: ContextType | string; // ContextType for known values, string for forward compatibility
  router_reason: string;
  citations: Citation[];
  model: string;
  conversation_id: string | null;
  // New research capability signals (from hybrid research agent)
  research_performed?: boolean;
  research_summary?: string | null;
}

export interface ChatTurn {
  id: string;
  conversation_id: string | null;
  role: 'user' | 'assistant' | 'compaction';
  content: string;
  context_type: ContextType | string | null;
  citations: Citation[] | null;
  /** The agent's tool trail, when an agent answered this turn (books). */
  agent_steps?: AgentStep[] | null;
  created_at: string | null;

  // === Sub-thread (nested tangent) support ===
  parent_turn_id?: string | null;
  // Only present on assistant turns in main chat responses: the user turn that
  // is the root of the sub-thread (pass this as thread_root_turn_id when entering).
  thread_root_turn_id?: string | null;
}

export interface ConversationSummary {
  conversation_id: string;
  turn_count: number;
  started_at: string | null;
  last_at: string | null;
  first_user_message: string | null;
}

export interface ProgressResponse {
  paper_id: string;
  status: string;
  job_status?: string | null;   // finer stage: extracting | chunking | embedding | ...
  // Real progress *within* job_status (e.g. pages extracted / total while
  // extracting). null when there's nothing finer than the status.
  progress_fraction?: number | null;
  // 1-based position among still-queued jobs while job_status is 'queued'
  // (this box's Celery worker runs at --concurrency=1, so this is a real
  // wait, not decoration); null once extraction actually starts.
  queue_position?: number | null;
  page_count: number | null;
  error_message?: string | null;
  extractor?: string | null;    // "mineru" | "pymupdf_fallback"
  // Raw HTML snapshot crawl for a doc_kind='article' import (see backend
  // services/article_crawl.py). 'none' for anything that isn't an article.
  // Independent of `status` above — a failed/pending snapshot never means
  // the article itself failed to import.
  raw_snapshot_status?: string | null;  // "none" | "pending" | "complete" | "failed"
  raw_page_count?: number | null;
}

export async function reextractPaper(paperId: string): Promise<{ paper_id: string; status: string; job_id: string; message: string }> {
  const res = await fetch(`${BASE}/papers/${paperId}/reextract`, { method: 'POST' });
  if (!res.ok) {
    let detail = `Re-extract failed: ${res.status}`;
    try { const body = await res.json(); if (body?.detail) detail = body.detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Re-run only the chunker on the cached MinerU output (fast, no re-extraction).
 * Use after improving the chunker to apply it to a paper already on disk.
 */
export async function rechunkPaper(paperId: string): Promise<{ paper_id: string; status: string; chunks_total: number; message: string }> {
  const res = await fetch(`${BASE}/papers/${paperId}/rechunk`, { method: 'POST' });
  if (!res.ok) {
    let detail = `Re-chunk failed: ${res.status}`;
    try { const body = await res.json(); if (body?.detail) detail = body.detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// ── Papers ────────────────────────────────────────────────────────────────────

export async function listPapers(): Promise<PaperMeta[]> {
  const res = await fetch(`${BASE}/papers`);
  if (!res.ok) throw new Error(`Failed to list papers: ${res.status}`);
  const data = await res.json();
  return data.documents;
}

export type DocKind = 'book' | 'paper';

export async function uploadPaper(file: File, kind: DocKind = 'paper'): Promise<{ id: string; status: string }> {
  if (!HAS_BACKEND) throw new Error(NO_BACKEND_MESSAGE);
  const form = new FormData();
  form.append('file', file);
  form.append('kind', kind);
  const res = await fetch(`${BASE}/papers/upload`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    let detail = `Upload failed: ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body not JSON or no detail; keep status-only message
    }
    throw new Error(detail);
  }
  return res.json();
}

/**
 * Import a web article by URL: the third ingestion pipeline, alongside
 * uploadPaper. `kind` is set when the link was pasted through the "Book" or
 * "Research paper" picker rather than the generic "Article by URL" one —
 * it only takes effect on the backend if the link turns out to be a PDF;
 * otherwise the document still becomes doc_kind='article' regardless.
 */
export async function importArticleUrl(
  url: string,
  kind?: 'book' | 'paper' | null,
): Promise<{ id: string; status: string }> {
  if (!HAS_BACKEND) throw new Error(NO_BACKEND_MESSAGE);
  const res = await fetch(`${BASE}/papers/import-url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(kind ? { url, kind } : { url }),
  });
  if (!res.ok) {
    let detail = `Import failed: ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body not JSON or no detail; keep status-only message
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getPaperProgress(paperId: string): Promise<ProgressResponse> {
  const res = await fetch(`${BASE}/papers/${paperId}/progress`);
  if (!res.ok) throw new Error(`Progress check failed: ${res.status}`);
  return res.json();
}

// ── Chunks ────────────────────────────────────────────────────────────────────

export async function getChunk(paperId: string, sequenceOrder: number): Promise<ChunkData> {
  const res = await fetch(`${BASE}/papers/${paperId}/chunks/${sequenceOrder}`);
  if (!res.ok) throw new Error(`Chunk fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch the next chunk whose sequence is strictly greater than `afterSequence`.
 * Pass 0 for the first chunk. Returns null when there is no further chunk.
 * Advancing this way is gap-tolerant: a missing sequence number can never
 * truncate the document mid-read.
 */
export async function getNextChunk(paperId: string, afterSequence: number): Promise<ChunkData | null> {
  const res = await fetch(`${BASE}/papers/${paperId}/chunks/after/${afterSequence}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Chunk fetch failed: ${res.status}`);
  return res.json();
}

/**
 * Bulk, gap-tolerant fetch of every chunk after `afterSequence`, up to `limit`.
 * Used to fast-forward to a saved reading position in one request instead of
 * one `getNextChunk` round trip per chunk.
 */
export async function getChunksRange(
  paperId: string,
  afterSequence: number,
  limit = 500,
): Promise<ChunkData[]> {
  const res = await fetch(`${BASE}/papers/${paperId}/chunks/range?after=${afterSequence}&limit=${limit}`);
  if (!res.ok) throw new Error(`Chunk range fetch failed: ${res.status}`);
  const data = await res.json();
  return data.chunks as ChunkData[];
}

// ── Whole document (article reader) ──────────────────────────────────────────

export interface DocBlock {
  id: string;
  sequence_order: number;
  structural_type: string;
  content_markdown: string;
  plain_text: string;
  heading_path: string[] | null;
  page_start: number | null;
  page_end: number | null;
  table_json?: { headers?: string[]; rows?: string[][] } | null;
  image_url: string | null;
}

export interface OutlineEntry {
  sequence_order: number;
  text: string;
  level: number;
}

export interface FullDocument {
  paper_id: string;
  title: string;
  doc_kind: string | null;
  status: string;
  page_count: number | null;
  extractor: string | null;
  /** The page a doc_kind='article' row was imported from. null otherwise. */
  source_url: string | null;
  blocks: DocBlock[];
  outline: OutlineEntry[];
  total: number;
}

/**
 * Fetch the entire paper in one request. The article reader renders all of it;
 * there is no paging, so there is no reason to make N round-trips for it.
 */
export async function getFullDocument(paperId: string): Promise<FullDocument> {
  const res = await fetch(`${BASE}/papers/${paperId}/document`);
  if (!res.ok) throw new Error(`Document fetch failed: ${res.status}`);
  return res.json();
}

// ── Notes (anchored margin annotations) ──────────────────────────────────────

/**
 * What a note hangs off.
 *
 * `document` is the holistic level: the question is about the paper as a
 * whole, asked from the assistant panel rather than from a selection. The
 * server derives the note's `scope` from this, so the two can never disagree.
 */
export type AnchorKind =
  | 'text'
  | 'figure'
  | 'equation'
  | 'table'
  | 'block'
  | 'document';
export type MarginSide = 'left' | 'right';

// ── Model catalog ────────────────────────────────────────────────────────────

export interface ModelInfo {
  name: string;
  /** Run on Ollama's infrastructure rather than from local weights. */
  is_cloud: boolean;
  size_bytes: number;
}

export interface ModelCatalog {
  models: ModelInfo[];
  /** The model used when none is chosen explicitly. */
  default: string;
}

/** Models available to answer a question, local first and cloud-hosted last. */
export async function listModels(): Promise<ModelCatalog> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error(`Model list failed: ${res.status}`);
  return res.json();
}

export interface NoteAnchor {
  kind: AnchorKind;
  sequence_id: number;
  chunk_id?: string | null;
  quote?: string | null;
  image_url?: string | null;
}

/** A source the agent found on the web, listed under its WEB step. */
export interface AgentSource {
  title: string;
  url: string;
}

/**
 * One tool call the agent made, as the reader sees it.
 *
 * Arrives twice while streaming: `running` when the agent announces the call
 * and `done` when it returns, keyed by `id` so the row updates in place.
 * The observation itself (the blocks the model actually read) is deliberately
 * not here: it is thousands of characters the card renders one line of.
 */
export interface AgentStep {
  id: string;
  /** Which tool round this call belonged to, 1-based. */
  n: number;
  /** `NOTE` and `REMEMBER` are writes, not fetches: they pin to a board / to memory. */
  tool: 'SECTION' | 'SEARCH' | 'READ' | 'WEB' | 'NOTE' | 'REMEMBER';
  arg: string;
  state: 'running' | 'done';
  /** The model's own one-line reason, on the first call of each round. */
  think: string | null;
  label: string;
  /** A short summary of what came back ("12 blocks · ¶31–¶48"). */
  result: string;
  /** Block numbers this call pulled in, so the reader can jump to them. */
  seqs: number[];
  sources: AgentSource[];
}

export interface PaperNote {
  id: string;
  anchor_sequence_id: number;
  anchor_chunk_id: string | null;
  anchor_kind: AnchorKind;
  anchor_quote: string | null;
  anchor_image_path: string | null;
  question: string;
  answer: string;
  cited_sequence_ids: number[];
  retrieval_mode: string | null;
  /** 'anchor' = a margin card. 'document' = asked about the whole paper. */
  scope: 'anchor' | 'document';
  /** How the answer was reached. Empty for notes written before this existed. */
  agent_steps: AgentStep[];
  /** What the provider reported answering. Shown on the card. */
  model: string | null;
  /** What the reader picked. Follow-ups inherit this, never override it. */
  requested_model: string | null;
  margin_side: MarginSide;
  parent_note_id: string | null;
  created_at: string | null;
}

/** Move a note to the other margin. */
export async function moveNote(
  paperId: string,
  noteId: string,
  side: MarginSide,
): Promise<void> {
  const res = await fetch(`${BASE}/papers/${paperId}/notes/${noteId}/margin`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ margin_side: side }),
  });
  if (!res.ok) throw new Error(`Move failed: ${res.status}`);
}

export async function listNotes(paperId: string): Promise<PaperNote[]> {
  const res = await fetch(`${BASE}/papers/${paperId}/notes`);
  if (!res.ok) throw new Error(`Notes fetch failed: ${res.status}`);
  const body = await res.json();
  return body.notes || [];
}

export async function deleteNote(paperId: string, noteId: string): Promise<void> {
  const res = await fetch(`${BASE}/papers/${paperId}/notes/${noteId}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`Note delete failed: ${res.status}`);
}

export interface NoteStreamHandlers {
  /** The note row exists, so render the card now, before any answer arrives. */
  onCreated: (noteId: string) => void;
  /** The phase the agent is in ("Reading the passage…", "Writing the answer…"). */
  onStatus: (message: string) => void;
  /** One tool call, announced then completed. Upsert by `step.id`. */
  onStep: (step: AgentStep) => void;
  /** Answer text, token by token. */
  onToken: (text: string) => void;
}

export interface NoteResult {
  note_id: string;
  answer: string;
  model: string;
  retrieval_mode: string | null;
  cited_sequence_ids: number[];
  agent_steps: AgentStep[];
}

/**
 * Ask a question anchored to a place in the paper. The note row is created
 * server-side first (so a failed generation still leaves a visible, retryable
 * card), then the answer streams in.
 */
export async function askNoteStream(
  paperId: string,
  question: string,
  anchor: NoteAnchor,
  parentNoteId: string | null,
  handlers: NoteStreamHandlers,
  signal?: AbortSignal,
  /** Omit to let the server balance the two margins. */
  marginSide?: MarginSide | null,
  /** Omit for the configured default. Ignored by the server on follow-ups. */
  model?: string | null,
): Promise<NoteResult> {
  const res = await fetch(`${BASE}/papers/${paperId}/notes/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      question,
      anchor,
      parent_note_id: parentNoteId,
      margin_side: marginSide ?? null,
      model: model ?? null,
    }),
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body not JSON; keep the status-only detail
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let result: NoteResult | null = null;
  let streamError: string | null = null;

  const handleEvent = (raw: string) => {
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(raw);
    } catch {
      return;
    }
    switch (ev.type) {
      case 'created':
        handlers.onCreated(String(ev.note_id ?? ''));
        break;
      case 'status':
        handlers.onStatus(String(ev.message ?? ''));
        break;
      case 'step':
        handlers.onStep(ev as unknown as AgentStep);
        break;
      case 'token':
        handlers.onToken(String(ev.text ?? ''));
        break;
      case 'error':
        streamError = String(ev.detail || 'Note generation failed');
        break;
      case 'done':
        result = {
          note_id: String(ev.note_id ?? ''),
          answer: String(ev.answer ?? ''),
          model: String(ev.model ?? ''),
          retrieval_mode: (ev.retrieval_mode as string) ?? null,
          cited_sequence_ids: (ev.cited_sequence_ids as number[]) || [],
          agent_steps: (ev.agent_steps as AgentStep[]) || [],
        };
        break;
    }
  };

  // SSE framing: events separated by a blank line, each one `data: {json}`.
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) handleEvent(line.slice(5).trim());
      }
    }
  }

  if (streamError) throw new Error(streamError);
  if (!result) throw new Error('Note stream ended unexpectedly');
  return result;
}

export interface Chapter {
  index: number;
  title: string;
  start_sequence: number;
  end_sequence: number;
  chunk_count: number;
  /** Outline depth, 1 = top-level chapter. Used to indent nested sections. */
  level?: number;
}

/** Fetch the chapter list (derived from top-level headings) for book navigation. */
export async function getChapters(
  paperId: string,
): Promise<{ doc_kind: string | null; source: string; chapters: Chapter[] }> {
  const res = await fetch(`${BASE}/papers/${paperId}/chapters`);
  if (!res.ok) {
    if (res.status === 404) return { doc_kind: null, source: 'none', chapters: [] };
    throw new Error(`Chapters fetch failed: ${res.status}`);
  }
  const body = await res.json();
  return {
    doc_kind: body.doc_kind ?? null,
    // "pdf_outline" (the publisher's own bookmarks) or "headings" (derived).
    source: body.source ?? 'headings',
    chapters: body.chapters || [],
  };
}

/** Fetch the total chunk count for a paper (and an optional first page). */
export async function getChunkCount(paperId: string): Promise<number> {
  const res = await fetch(`${BASE}/papers/${paperId}/chunks?limit=1`);
  if (!res.ok) throw new Error(`Chunk count failed: ${res.status}`);
  const data = await res.json();
  return data.total as number;
}

/** Fetch paper metadata (status, page_count, etc.). */
export async function getPaper(paperId: string): Promise<PaperMeta> {
  const res = await fetch(`${BASE}/papers/${paperId}`);
  if (!res.ok) throw new Error(`Paper fetch failed: ${res.status}`);
  return res.json();
}

/** Delete a paper (DB cascade + on-disk cleanup): 204 on success. */
/**
 * Rename a paper.
 *
 * Sets a display title used everywhere a name is shown. Passing an empty
 * string clears it and restores the uploaded filename: the server treats
 * blank as "no override" rather than storing an empty name.
 */
export async function renamePaper(paperId: string, title: string): Promise<PaperMeta> {
  const res = await fetch(`${BASE}/papers/${paperId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title.trim() || null }),
  });
  if (!res.ok) throw new Error(`Rename failed: ${res.status}`);
  return res.json();
}

/**
 * URL of a paper's first-page thumbnail.
 *
 * ⚠ Served as 204 No Content when the page cannot be rendered, which an
 * <img> reports as a load error, so every caller needs an onError fallback.
 * A 404 would be worse: the library requests one per card, and a console full
 * of them makes a working library look broken.
 */
export function getCoverUrl(paperId: string): string {
  return `${BASE}/papers/${paperId}/cover`;
}

export async function deletePaper(paperId: string): Promise<void> {
  const res = await fetch(`${BASE}/papers/${paperId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// ── Ask ───────────────────────────────────────────────────────────────────────

export async function askPaper(
  paperId: string,
  query: string,
  currentSequenceOrder: number | null,
  conversationId: string | null = null,
  options?: {
    visibleSequenceOrders?: number[];
    focusedElement?: string | null;
    imagesB64?: string[];   // raw base64, no data: prefix; sent to multimodal model
    // Sub-thread support
    parentTurnId?: string | null;
    threadRootTurnId?: string | null;
  },
  signal?: AbortSignal,
): Promise<AskResponse> {
  const res = await fetch(`${BASE}/papers/${paperId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      query,
      current_sequence_order: currentSequenceOrder,
      conversation_id: conversationId,
      visible_sequence_orders: options?.visibleSequenceOrders ?? null,
      focused_element: options?.focusedElement ?? null,
      images_b64: options?.imagesB64 ?? null,
      parent_turn_id: options?.parentTurnId ?? null,
      thread_root_turn_id: options?.threadRootTurnId ?? null,
    }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body not JSON; keep status-only detail
    }
    throw new Error(detail);
  }
  return res.json();
}

export interface AskStreamHandlers {
  /** Called per generated token: append to the in-progress answer. */
  onToken: (text: string) => void;
  /** Transient status line (e.g. "Researching the web…"). */
  onStatus?: (message: string) => void;
  /** Discard the buffered answer: a research synthesis pass restreams it. */
  onReplace?: () => void;
  /** One tool call from the agent (books). Emitted twice: running → done. */
  onStep?: (step: AgentStep) => void;
}

/**
 * Streaming variant of askPaper using Server-Sent Events. Tokens arrive via
 * `handlers` as they are generated; resolves with the final AskResponse
 * (whose `answer` is authoritative, since the backend may rewrite image URLs after
 * streaming completes).
 */
export async function askPaperStream(
  paperId: string,
  query: string,
  currentSequenceOrder: number | null,
  conversationId: string | null = null,
  options:
    | {
        visibleSequenceOrders?: number[];
        focusedElement?: string | null;
        imagesB64?: string[];
        parentTurnId?: string | null;
        threadRootTurnId?: string | null;
      }
    | undefined,
  handlers: AskStreamHandlers,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const res = await fetch(`${BASE}/papers/${paperId}/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      query,
      current_sequence_order: currentSequenceOrder,
      conversation_id: conversationId,
      visible_sequence_orders: options?.visibleSequenceOrders ?? null,
      focused_element: options?.focusedElement ?? null,
      images_b64: options?.imagesB64 ?? null,
      parent_turn_id: options?.parentTurnId ?? null,
      thread_root_turn_id: options?.threadRootTurnId ?? null,
    }),
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // body not JSON; keep status-only detail
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let result: AskResponse | null = null;

  const handleEvent = (raw: string) => {
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(raw);
    } catch {
      return;
    }
    switch (ev.type) {
      case 'token':
        handlers.onToken(String(ev.text ?? ''));
        break;
      case 'status':
        handlers.onStatus?.(String(ev.message ?? ''));
        break;
      case 'step':
        handlers.onStep?.(ev as unknown as AgentStep);
        break;
      case 'replace':
        handlers.onReplace?.();
        break;
      case 'error':
        throw new Error(String(ev.detail || 'Answer stream failed'));
      case 'done':
        result = {
          answer: String(ev.answer ?? ''),
          context_type: String(ev.context_type ?? ''),
          router_reason: String(ev.router_reason ?? ''),
          citations: (ev.citations as Citation[]) || [],
          model: String(ev.model ?? ''),
          conversation_id: (ev.conversation_id as string) ?? null,
          research_performed: Boolean(ev.research_performed),
          research_summary: (ev.research_summary as string) ?? null,
        };
        break;
    }
  };

  // SSE framing: events are separated by a blank line; each carries one
  // `data: {json}` line.
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) handleEvent(line.slice(5).trim());
      }
    }
  }

  if (!result) throw new Error('Answer stream ended unexpectedly');
  return result;
}

export interface ChatHistoryResponse {
  turns: ChatTurn[];
  isSubThread: boolean;
  /** Sub-thread depth: 0 = main, 1..MAX = sub-thread layers. */
  depth: number;
  /** Cap on sub-thread nesting reported by the backend (currently 3). */
  maxDepth: number;
}

export async function getPaperChat(
  paperId: string,
  conversationId?: string | null,
  threadRootTurnId?: string | null,
  signal?: AbortSignal,
): Promise<ChatHistoryResponse> {
  const params = new URLSearchParams();
  if (conversationId) params.set('conversation_id', conversationId);
  if (threadRootTurnId) params.set('thread_root_turn_id', threadRootTurnId);
  const qs = params.toString() ? `?${params}` : '';
  const res = await fetch(`${BASE}/papers/${paperId}/chat${qs}`, { signal });
  if (!res.ok) throw new Error(`Chat history fetch failed: ${res.status}`);
  const body = await res.json();
  return {
    turns: body.turns || [],
    isSubThread: !!body.is_sub_thread,
    depth: typeof body.depth === 'number' ? body.depth : 0,
    maxDepth: typeof body.max_depth === 'number' ? body.max_depth : 3,
  };
}

export async function listPaperConversations(paperId: string): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/papers/${paperId}/conversations`);
  if (!res.ok) throw new Error(`Conversations fetch failed: ${res.status}`);
  const body = await res.json();
  return body.conversations || [];
}

// ── Rich Figure Descriptions (from VLM at ingestion time) ───────────────────

export interface FigureDescription {
  id: string;
  chunk_id: string;
  image_path: string;
  description_markdown: string;
  description_plain: string;
  source_sequence_start?: number;
  model: string;
  created_at: string;
}

export async function getFigureDescriptions(paperId: string): Promise<FigureDescription[]> {
  const res = await fetch(`${BASE}/papers/${paperId}/figure-descriptions`);
  if (!res.ok) {
    if (res.status === 404) return [];
    throw new Error(`Figure descriptions fetch failed: ${res.status}`);
  }
  const body = await res.json();
  return body.descriptions || [];
}

export async function triggerReadingOrderReconstruction(paperId: string) {
  const res = await fetch(`${BASE}/papers/${paperId}/reconstruct-reading-order`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to trigger reading order reconstruction: ${res.status}`);
  return res.json();
}

export async function getPaperWithOrder(paperId: string) {
  // Re-use the normal getPaper but the response now may include reading_order
  const res = await fetch(`${BASE}/papers/${paperId}`);
  if (!res.ok) throw new Error(`Paper fetch failed: ${res.status}`);
  return res.json();
}

// ── Health ────────────────────────────────────────────────────────────────────

export async function checkHealth(): Promise<{ status: string; database: string }> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

// ── Raw Files ─────────────────────────────────────────────────────────────────

/** URL to view/download the raw copy of a document: the original PDF for a
 * paper/book, or a sanitized raw HTML snapshot of the imported page for an
 * article — the backend branches on doc_kind, this URL is the same either
 * way. */
export function getRawFileUrl(paperId: string): string {
  return `${BASE}/papers/${paperId}/raw`;
}

/** URL to the static asset PDF (for embedding in iframe/viewer) */
export function getStaticPdfUrl(paperId: string): string {
  return `/static/assets/${paperId}.pdf`;
}

// ── Personal reading state (bookmarks, own notes, decks) ──────────────────────
//
// Everything in the reader that belongs to the person rather than the paper.
// Server-owned since it moved out of localStorage: marks made on the desktop
// have to be there when the same paper is opened from the LAN server on
// another device, and clearing site data must not destroy them.

export interface WireBookmark {
  id: string;
  sequence_id: number;
  snippet: string | null;
  kind: 'text' | 'figure' | 'equation' | 'table' | 'block';
  page: number | null;
  progress: number;
  label: string | null;
  updated_at: string | null;
}

export interface WirePersonalNote {
  id: string;
  anchor_sequence_id: number;
  anchor_chunk_id: string | null;
  anchor_quote: string | null;
  body: string;
  margin_side: MarginSide;
  created_at: string | null;
  updated_at: string | null;
}

export interface WireDeckMember {
  kind: 'ai' | 'personal';
  id: string;
}

export interface WireDeck {
  id: string;
  label: string | null;
  top: number;
  margin_side: MarginSide;
  study: boolean;
  members: WireDeckMember[];
}

export interface WirePersonalState {
  bookmarks: WireBookmark[];
  notes: WirePersonalNote[];
  decks: WireDeck[];
}

/**
 * All three collections in one request.
 *
 * Decks reference the other two, so fetching them separately can produce a
 * deck describing a note a concurrent delete already removed.
 */
export async function getPersonalState(paperId: string): Promise<WirePersonalState> {
  const res = await fetch(`${BASE}/papers/${paperId}/personal`);
  if (!res.ok) throw new Error(`Personal state fetch failed: ${res.status}`);
  return res.json();
}

export interface BookmarkInput {
  sequence_id: number;
  snippet?: string | null;
  kind?: string;
  page?: number | null;
  progress?: number;
  label?: string | null;
}

export async function createBookmark(
  paperId: string,
  input: BookmarkInput,
): Promise<WireBookmark> {
  const res = await fetch(`${BASE}/papers/${paperId}/bookmarks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Bookmark save failed: ${res.status}`);
  return res.json();
}

export async function deleteBookmark(paperId: string, bookmarkId: string): Promise<void> {
  const res = await fetch(`${BASE}/papers/${paperId}/bookmarks/${bookmarkId}`, {
    method: 'DELETE',
  });
  if (!res.ok && res.status !== 404) throw new Error(`Bookmark delete failed: ${res.status}`);
}

export async function renameBookmark(
  paperId: string,
  bookmarkId: string,
  label: string | null,
): Promise<WireBookmark> {
  const res = await fetch(`${BASE}/papers/${paperId}/bookmarks/${bookmarkId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  });
  if (!res.ok) throw new Error(`Bookmark rename failed: ${res.status}`);
  return res.json();
}

export interface PersonalNoteInput {
  anchor_sequence_id: number;
  body: string;
  anchor_quote?: string | null;
  margin_side?: MarginSide;
}

export async function createPersonalNote(
  paperId: string,
  input: PersonalNoteInput,
): Promise<WirePersonalNote> {
  const res = await fetch(`${BASE}/papers/${paperId}/personal-notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Note save failed: ${res.status}`);
  return res.json();
}

export async function updatePersonalNote(
  paperId: string,
  noteId: string,
  patch: { body?: string; margin_side?: MarginSide },
): Promise<WirePersonalNote> {
  const res = await fetch(`${BASE}/papers/${paperId}/personal-notes/${noteId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Note update failed: ${res.status}`);
  return res.json();
}

export async function deletePersonalNote(paperId: string, noteId: string): Promise<void> {
  const res = await fetch(`${BASE}/papers/${paperId}/personal-notes/${noteId}`, {
    method: 'DELETE',
  });
  if (!res.ok && res.status !== 404) throw new Error(`Note delete failed: ${res.status}`);
}

/**
 * Replace the whole deck arrangement for a paper.
 *
 * One drag can dissolve a deck, create another, and move a card between two
 * more. That is a single arrangement, so it travels as one request rather
 * than an ordered sequence of calls with a half-applied state between each.
 */
export async function putDecks(paperId: string, decks: WireDeck[]): Promise<WireDeck[]> {
  const res = await fetch(`${BASE}/papers/${paperId}/decks`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decks }),
  });
  if (!res.ok) throw new Error(`Deck save failed: ${res.status}`);
  return (await res.json()).decks || [];
}

// ── The desk: studies, their chats, and sticky notes ─────────────────────────

/**
 * The path segment that means "every paper" rather than a saved group.
 *
 * ⚠ A real scope, not a null. The server stores `study_id IS NULL` for its
 * turns, and both are the library-wide chat: treating either as "unset" loses
 * the reader's main conversation.
 */
export const LIBRARY_SCOPE = 'library';

export interface Study {
  id: string;
  name: string;
  description: string | null;
  paper_count: number;
  created_at: string | null;
  updated_at: string | null;
}

/** A paper as the desk sees it: identity, and the P-number the agent cites it by. */
export interface StudyPaper {
  id: string;
  title: string;
  page_count: number | null;
  status: string;
  paper: number;
}

/** One `[[P2:41]]` an answer used, resolved to something openable. */
export interface StudyCitation {
  paper: number;
  document_id: string;
  label: string;
  sequence_id: number;
}

export interface StudyTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model: string | null;
  cited: StudyCitation[];
  agent_steps: AgentStep[];
  created_at: string | null;
}

export async function listStudies(): Promise<Study[]> {
  const res = await fetch(`${BASE}/studies`);
  if (!res.ok) throw new Error(`Studies fetch failed: ${res.status}`);
  return (await res.json()).studies || [];
}

export async function getStudy(
  studyId: string,
): Promise<{ study: Study; papers: StudyPaper[] }> {
  const res = await fetch(`${BASE}/studies/${studyId}`);
  if (!res.ok) throw new Error(`Study fetch failed: ${res.status}`);
  return res.json();
}

export async function createStudy(name: string): Promise<Study> {
  const res = await fetch(`${BASE}/studies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Could not create the study (${res.status})`);
  return res.json();
}

export async function renameStudy(studyId: string, name: string): Promise<Study> {
  const res = await fetch(`${BASE}/studies/${studyId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Rename failed: ${res.status}`);
  return res.json();
}

export async function deleteStudy(studyId: string): Promise<void> {
  const res = await fetch(`${BASE}/studies/${studyId}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`Delete failed: ${res.status}`);
}

/**
 * Replace a study's papers. List order sets citation order.
 *
 * ⚠ Whole-collection on purpose: the P-numbers the reader is looking at come
 * from this order, so a partial update could repoint citations already on screen.
 */
export async function setStudyPapers(
  studyId: string,
  documentIds: string[],
): Promise<StudyPaper[]> {
  const res = await fetch(`${BASE}/studies/${studyId}/papers`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_ids: documentIds }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return (await res.json()).papers || [];
}

export async function getStudyChat(studyId: string): Promise<StudyTurn[]> {
  const res = await fetch(`${BASE}/studies/${studyId}/chat`);
  if (!res.ok) throw new Error(`Chat fetch failed: ${res.status}`);
  return (await res.json()).turns || [];
}

export async function clearStudyChat(studyId: string): Promise<void> {
  const res = await fetch(`${BASE}/studies/${studyId}/chat`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`Clear failed: ${res.status}`);
}

export interface StudyStreamHandlers {
  onStatus: (message: string) => void;
  onStep: (step: AgentStep) => void;
  onToken: (text: string) => void;
}

export interface StudyResult {
  turn_id: string;
  answer: string;
  model: string;
  cited: StudyCitation[];
  agent_steps: AgentStep[];
}

/**
 * Ask the study a question. Same SSE shapes as `askNoteStream`, so the trail
 * component renders both.
 */
export async function askStudyStream(
  studyId: string,
  question: string,
  handlers: StudyStreamHandlers,
  model?: string | null,
  signal?: AbortSignal,
): Promise<StudyResult> {
  const res = await fetch(`${BASE}/studies/${studyId}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({ question, model: model ?? null }),
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let result: StudyResult | null = null;
  let streamError: string | null = null;

  const handleEvent = (raw: string) => {
    let ev: Record<string, unknown>;
    try { ev = JSON.parse(raw); } catch { return; }
    switch (ev.type) {
      case 'status':
        handlers.onStatus(String(ev.message ?? ''));
        break;
      case 'step':
        handlers.onStep(ev as unknown as AgentStep);
        break;
      case 'token':
        handlers.onToken(String(ev.text ?? ''));
        break;
      case 'error':
        streamError = String(ev.detail || 'Could not answer that');
        break;
      case 'done':
        result = {
          turn_id: String(ev.turn_id ?? ''),
          answer: String(ev.answer ?? ''),
          model: String(ev.model ?? ''),
          cited: (ev.cited as StudyCitation[]) || [],
          agent_steps: (ev.agent_steps as AgentStep[]) || [],
        };
        break;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) handleEvent(line.slice(5).trim());
      }
    }
  }

  if (streamError) throw new Error(streamError);
  if (!result) throw new Error('The answer stream ended unexpectedly');
  return result;
}

// ── Sticky notes: two boards ─────────────────────────────────────────────────

export type StickyColor = 'yellow' | 'blue' | 'green' | 'pink' | 'orange' | 'plain';

/**
 * Which board a note lives on.
 *
 * ⚠ `board` is not redundant with `scope`. `scope: 'library'` already means the
 * library-wide *chat*, so without the board a note beside that chat and a note
 * on the universal board would be the same thing.
 */
export type StickyBoard = 'chat' | 'universal';

export interface Sticky {
  id: string;
  body: string;
  color: StickyColor;
  pinned: boolean;
  board: StickyBoard;
  /** Study id, or `library`. Meaningless when `board === 'universal'`. */
  scope: string;
  /** Who wrote it. Assistant notes are badged and an edit cannot launder that. */
  origin: 'user' | 'assistant';
  author_model: string | null;
  /** Papers this note references, if any. */
  papers: { document_id: string; label: string }[];
  created_at: string | null;
  updated_at: string | null;
}

/** One board's notes, pinned first then newest. */
export async function listStickies(
  board: StickyBoard,
  scope?: string,
): Promise<Sticky[]> {
  const qs = new URLSearchParams({ board });
  if (board === 'chat') qs.set('scope', scope || LIBRARY_SCOPE);
  const res = await fetch(`${BASE}/stickies?${qs}`);
  if (!res.ok) throw new Error(`Notes fetch failed: ${res.status}`);
  return (await res.json()).stickies || [];
}

export async function createSticky(input: {
  body?: string;
  color?: StickyColor;
  pinned?: boolean;
  board: StickyBoard;
  scope?: string;
  document_ids?: string[];
}): Promise<Sticky> {
  const res = await fetch(`${BASE}/stickies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Could not create the note (${res.status})`);
  return res.json();
}

/**
 * Edit a note, or move it between boards.
 *
 * ⚠ Send `board` **and** `scope` together to move one. `board` alone is not
 * enough: `scope: 'library'` is a real destination, not "unset".
 *
 * ⚠ `origin` is not patchable. A note the assistant wrote stays badged as the
 * assistant's however often it is edited: the badge records where the claim
 * came from, and an edit that launders it makes the badge worthless.
 */
export async function updateSticky(
  stickyId: string,
  patch: {
    body?: string;
    color?: StickyColor;
    pinned?: boolean;
    board?: StickyBoard;
    scope?: string;
    document_ids?: string[];
  },
): Promise<Sticky> {
  const res = await fetch(`${BASE}/stickies/${stickyId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Save failed: ${res.status}`);
  return res.json();
}

/** ⚠ Reader-only. The assistant writes and edits notes but never removes one. */
export async function deleteSticky(stickyId: string): Promise<void> {
  const res = await fetch(`${BASE}/stickies/${stickyId}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`Delete failed: ${res.status}`);
}

// ── Auth ─────────────────────────────────────────────────────────────────────
//
// The session is an httponly cookie: nothing here reads or stores a token.
// `credentials: 'include'` is explicit (not just relying on fetch's
// same-origin default) so these calls stay correct even if this app is ever
// served from a different origin than the API. The other ~50 functions in
// this file don't need it: this deployment is same-origin, so the browser
// already sends the cookie for them without asking.

async function _authError(res: Response, fallback: string): Promise<never> {
  let detail = fallback;
  try {
    const body = await res.json();
    if (body?.detail) detail = body.detail;
  } catch {
    // non-JSON error body, so fall back to the generic message
  }
  throw new Error(detail);
}

export interface MeResponse {
  user: User | null;
  /** False when the site is at its concurrent-active-user cap and this
   * session hasn't been let in yet (see backend app/core/capacity.py).
   * Meaningless when `user` is null. */
  admitted: boolean;
  /** 1-based place in line while `admitted` is false, else null. */
  queuePosition: number | null;
}

export async function getMe(): Promise<MeResponse> {
  const res = await fetch(`${BASE}/auth/me`, { credentials: 'include' });
  if (!res.ok) return { user: null, admitted: true, queuePosition: null };
  const data = await res.json();
  return { user: data.user, admitted: data.admitted, queuePosition: data.queue_position ?? null };
}

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) return _authError(res, 'Login failed');
  return res.json();
}

export async function signup(
  email: string,
  password: string,
  displayName?: string,
): Promise<User> {
  const res = await fetch(`${BASE}/auth/signup`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email,
      password,
      display_name: displayName || undefined,
    }),
  });
  if (!res.ok) return _authError(res, 'Sign up failed');
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
}
