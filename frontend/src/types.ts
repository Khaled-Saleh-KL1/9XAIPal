// ── Core domain types ────────────────────────────────────────────────────────

export interface Paper {
  id: string;
  title: string;
  authors: string;
  venue: string;
  pages: number;
  added: string;
  progress: number; // 0..1
  tags: string[];
  pinned?: boolean;
  // Raw backend status (e.g. "processing" / "failed") so cards can render
  // status labels without re-querying the API.
  rawStatus?: string;
  // Fine-grained ingestion stage when the paper is still processing
  // (queued | extracting | chunking | embedding). Used by the library card to
  // animate a live green progress bar while work continues in the background.
  jobStatus?: string | null;
}

/**
 * Actual chunk types returned by the backend (from MinerU content_list + chunker).
 * Note: The real data uses `structural_type: string` on ChunkData (see api.ts).
 * This type is kept for documentation and future stricter usage.
 */
export type ChunkType =
  | 'text'
  | 'paragraph'      // emitted by sample data + chunker for plain prose blocks
  | 'heading'
  | 'math'
  | 'figure'
  | 'table'
  | 'list'
  | 'section_summary'; // future: when we surface pre-computed summaries as first-class chunks

// Legacy interface — the app primarily uses ChunkData from the API (structural_type is a plain string).
export interface Chunk {
  type: ChunkType;
  level?: 1 | 2;
  text?: string;
  meta?: string;
  label?: string;
  caption?: string;
  placeholder?: string;
  items?: string[];
}

/** All known context routing modes returned by the backend /ask flow. */
export type ContextType =
  | 'LOCAL'
  | 'GLOBAL'
  | 'OVERVIEW'      // Pre-computed high-quality hierarchical section + paper summaries (bypasses vector search)
  | 'EXTERNAL'
  | 'OUT_OF_SCOPE'
  | 'RESEARCH';     // Model performed live iterative research (visible in history)

export interface ChatMessage {
  // 'compaction' = synthetic system bubble inserted when the chat history
  // is auto-summarized to keep context focused.
  role: 'user' | 'assistant' | 'compaction';
  text: string;
  refs?: string[];
  // When the assistant turn involved live research (new capability)
  researchPerformed?: boolean;
  researchSummary?: string;

  // === Sub-thread (nested tangent) support ===
  // Present on turns returned from the backend.
  parentTurnId?: string | null;
  // On assistant turns in the *main* chat view: the user turn id that is the
  // root of the sub-thread (what to pass as thread_root_turn_id when opening).
  threadRootTurnId?: string | null;
}

export interface UploadingFile {
  name: string;
  size: string;
  pages: number;
}

// ── UI state types ────────────────────────────────────────────────────────────

export type Route = 'library' | 'processing' | 'reading' | 'pdf-viewer';
export type LibraryLayout = 'grid' | 'list';
export type SortKey = 'recent' | 'title' | 'pages';

// ── Processing step ───────────────────────────────────────────────────────────

export type StepState = 'pending' | 'active' | 'done' | 'error';

export interface ProcessingStep {
  id: number;
  title: string;
  sub: string;
  detail: string[];
}
