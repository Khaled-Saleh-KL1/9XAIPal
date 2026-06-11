/**
 * API client for 9XAIPal backend.
 * All requests proxy through Vite to http://localhost:8000.
 */
import type { ContextType } from './types';

const BASE = '/api/v1';

export interface PaperMeta {
  id: string;
  filename: string;
  original_filename: string;
  file_size_bytes: number | null;
  page_count: number | null;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string | null;
  extractor?: string | null;            // "mineru" | "pymupdf_fallback"
  doc_kind?: string | null;             // "book" | "paper"
  reading_order?: number[] | null;
  // Fine-grained pipeline stage for in-flight papers (queued | extracting |
  // chunking | embedding | complete | failed). Drives the library's live
  // progress bar without an N+1 poll per card.
  job_status?: string | null;
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
  page_count: number | null;
  error_message?: string | null;
  extractor?: string | null;    // "mineru" | "pymupdf_fallback"
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
 * Re-run only the chunker on the cached MinerU output (fast — no re-extraction).
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

export interface Chapter {
  index: number;
  title: string;
  start_sequence: number;
  end_sequence: number;
  chunk_count: number;
}

/** Fetch the chapter list (derived from top-level headings) for book navigation. */
export async function getChapters(paperId: string): Promise<{ doc_kind: string | null; chapters: Chapter[] }> {
  const res = await fetch(`${BASE}/papers/${paperId}/chapters`);
  if (!res.ok) {
    if (res.status === 404) return { doc_kind: null, chapters: [] };
    throw new Error(`Chapters fetch failed: ${res.status}`);
  }
  const body = await res.json();
  return { doc_kind: body.doc_kind ?? null, chapters: body.chapters || [] };
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

/** Delete a paper (DB cascade + on-disk cleanup) — 204 on success. */
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
  /** Called per generated token — append to the in-progress answer. */
  onToken: (text: string) => void;
  /** Transient status line (e.g. "Researching the web…"). */
  onStatus?: (message: string) => void;
  /** Discard the buffered answer — a research synthesis pass restreams it. */
  onReplace?: () => void;
}

/**
 * Streaming variant of askPaper using Server-Sent Events. Tokens arrive via
 * `handlers` as they are generated; resolves with the final AskResponse
 * (whose `answer` is authoritative — the backend may rewrite image URLs after
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

/** URL to view/download the raw PDF for a paper */
export function getRawPdfUrl(paperId: string): string {
  return `${BASE}/papers/${paperId}/raw`;
}

/** URL to the static asset PDF (for embedding in iframe/viewer) */
export function getStaticPdfUrl(paperId: string): string {
  return `/static/assets/${paperId}.pdf`;
}
