import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import type { Paper } from '../types';
import { IconBack, IconDoc, IconArrow } from '../components/Icons';
import { ChatPane } from './ChatPane';
import {
  getNextChunk,
  getChunkCount,
  getPaper,
  deletePaper,
  getFigureDescriptions,
  triggerReadingOrderReconstruction,
  reextractPaper,
  rechunkPaper,
  getChapters,
  type ChunkData,
  type PaperMeta,
  type FigureDescription,
  type Chapter,
} from '../api';

// ── Granular reveal helpers ────────────────────────────────────────────────

/** Split a text block into clean paragraphs. Handles common cases well. */
function splitIntoParagraphs(text: string): string[] {
  if (!text) return [];

  // Normalize newlines
  const normalized = text.replace(/\r\n/g, '\n').trim();

  // Split on double newlines (standard paragraph breaks)
  let paras = normalized
    .split(/\n\s*\n+/)
    .map(p => p.trim())
    .filter(Boolean);

  // If we only got one huge block, try splitting on single newlines that look like paragraph starts
  if (paras.length === 1 && normalized.length > 600) {
    paras = normalized
      .split(/\n(?=[A-Z0-9"'\u201C\u2018(])/ ) // rough heuristic for new paragraph
      .map(p => p.trim())
      .filter(Boolean);
  }

  return paras.length > 0 ? paras : [normalized];
}

// Pull display-math blocks ($$...$$, \[...\], \begin{equation}...) out of a
// paragraph so they can be rendered as their own centered KaTeX block instead
// of wrapping across lines mid-formula. Mirrors backend split logic so older
// chunks (ingested before the backend fix) still display correctly.
const DISPLAY_MATH_RE =
  /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath)\}[\s\S]+?\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath)\})/g;

function splitParagraphAroundMath(text: string): Array<{ kind: 'text' | 'math'; body: string }> {
  if (!text || (!text.includes('$$') && !text.includes('\\[') && !text.includes('\\begin{'))) {
    return [{ kind: 'text', body: text }];
  }
  const segments: Array<{ kind: 'text' | 'math'; body: string }> = [];
  let lastEnd = 0;
  for (const m of text.matchAll(DISPLAY_MATH_RE)) {
    const idx = m.index ?? 0;
    if (idx > lastEnd) {
      const prefix = text.slice(lastEnd, idx).trim();
      if (prefix) segments.push({ kind: 'text', body: prefix });
    }
    let body = m[0].trim();
    if (body.startsWith('$$') && body.endsWith('$$')) body = body.slice(2, -2).trim();
    else if (body.startsWith('\\[') && body.endsWith('\\]')) body = body.slice(2, -2).trim();
    segments.push({ kind: 'math', body });
    lastEnd = idx + m[0].length;
  }
  if (lastEnd < text.length) {
    const tail = text.slice(lastEnd).trim();
    if (tail) segments.push({ kind: 'text', body: tail });
  }
  return segments.length ? segments : [{ kind: 'text', body: text }];
}

type RevealedUnit =
  | { kind: 'paragraph'; text: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'heading'; text: string; level: number; sourceChunkId: string; sourceSeq: number }
  | { kind: 'table'; markdown: string; sourceChunkId: string; sourceSeq: number; tableJson?: any }
  | { kind: 'figure'; imageUrl?: string; caption?: string; filename?: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'math'; latex: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'code'; markdown: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'footnote'; text: string; sourceChunkId: string; sourceSeq: number };

interface Props {
  paper: Paper;
  paperId: string;
  onBack: () => void;
}

export function ReadingView({ paper, paperId, onBack }: Props) {
  const [chunks, setChunks] = useState<ChunkData[]>([]);
  // Cursor for gap-tolerant paging: the highest sequence_order loaded so far.
  // We always ask the backend for "the next chunk after this", starting at 0.
  const [lastSeq, setLastSeq] = useState(0);
  const [loading, setLoading] = useState(false);
  const [atEnd, setAtEnd] = useState(false);
  const [meta, setMeta] = useState<PaperMeta | null>(null);
  const [totalChunks, setTotalChunks] = useState<number>(0);

  // ── Book mode: chapter-by-chapter navigation ───────────────────────────────
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [activeChapter, setActiveChapter] = useState<Chapter | null>(null);
  // Tracks which chapter ("-1" for a linear paper) has been initialized into the
  // reader so we don't re-run the loader on every render.
  const initedKeyRef = useRef<string | null>(null);
  const isBook = meta?.doc_kind === 'book';

  // Granular reveal: we break text into paragraphs and treat tables/figures as atomic clean units
  const [revealedUnits, setRevealedUnits] = useState<RevealedUnit[]>([]);
  const [currentChunkIndex, setCurrentChunkIndex] = useState(0);
  const [paragraphIndexInCurrent, setParagraphIndexInCurrent] = useState(0);

  // Pointer into readingOrder when useLogicalOrder is active
  const [logicalPointer, setLogicalPointer] = useState(0);

  // Rich figure descriptions (generated at ingestion with VLM)
  const [figureDescriptions, setFigureDescriptions] = useState<Record<string, FigureDescription>>({});

  // LLM-corrected reading order for two-column / complex papers
  const [readingOrder, setReadingOrder] = useState<number[] | null>(null);
  const [useLogicalOrder, setUseLogicalOrder] = useState(false);
  const [reconstructionStatus, setReconstructionStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');

  const [deleting, setDeleting] = useState(false);
  const readerRef = useRef<HTMLDivElement>(null);
  const dPressed = useRef(false);

  // ── Resizable split: chat-pane width as a percentage of the layout area.
  // Persisted across sessions; clamped to [20%, 75%] so neither pane vanishes.
  const splitRef = useRef<HTMLDivElement>(null);
  const [chatWidthPct, setChatWidthPct] = useState<number>(() => {
    try {
      const stored = parseFloat(localStorage.getItem('pal:chat:width') || '');
      if (Number.isFinite(stored) && stored >= 20 && stored <= 75) return stored;
    } catch { /* localStorage blocked — fall through */ }
    return 40;
  });
  const draggingRef = useRef(false);
  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!draggingRef.current || !splitRef.current) return;
      const rect = splitRef.current.getBoundingClientRect();
      const fromRight = rect.right - e.clientX;
      const pct = (fromRight / rect.width) * 100;
      const clamped = Math.max(20, Math.min(75, pct));
      setChatWidthPct(clamped);
    }
    function onUp() {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try { localStorage.setItem('pal:chat:width', String(chatWidthPct)); } catch { /* no-op */ }
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [chatWidthPct]);

  // ── Hydrate paper metadata + total chunk count, and re-poll while the
  // paper is still processing on the backend so the header stays honest.
  useEffect(() => {
    let alive = true;
    let interval: ReturnType<typeof setInterval> | null = null;

    const refresh = async () => {
      try {
        const [m, n] = await Promise.all([
          getPaper(paperId),
          getChunkCount(paperId).catch(() => 0),
        ]);
        if (!alive) return;
        setMeta(m);
        setTotalChunks(n);

        if (m.reading_order && Array.isArray(m.reading_order)) {
          setReadingOrder(m.reading_order);
          setReconstructionStatus('done');
        }

        if (m.status === 'complete' || m.status === 'failed') {
          if (interval) clearInterval(interval);
          interval = null;
        }
      } catch {
        // ignore; transient
      }
    };

    refresh();
    interval = setInterval(refresh, 2000);
    return () => {
      alive = false;
      if (interval) clearInterval(interval);
    };
  }, [paperId]);

  // ── Convert a raw backend chunk into one or more small revealable units ─────
  const chunkToUnits = useCallback((chunk: ChunkData): RevealedUnit[] => {
    const seq = chunk.sequence_order ?? 0; // backend uses sequence_order in some responses
    const id = chunk.id;

    if (chunk.structural_type === 'heading') {
      const text = chunk.plain_text || chunk.content_markdown.replace(/^#+\s*/, '');
      const level = chunk.heading_path?.length ?? 1;
      return [{ kind: 'heading', text, level, sourceChunkId: id, sourceSeq: seq }];
    }

    if (chunk.structural_type === 'table') {
      return [{
        kind: 'table',
        markdown: chunk.content_markdown,
        tableJson: (chunk as any).table_json, // we added this server-side
        sourceChunkId: id,
        sourceSeq: seq,
      }];
    }

    if (chunk.structural_type === 'figure') {
      return [{
        kind: 'figure',
        imageUrl: chunk.image_url || undefined,
        caption: chunk.plain_text || undefined,
        filename: chunk.image_refs?.[0],
        sourceChunkId: id,
        sourceSeq: seq,
      }];
    }

    if (chunk.structural_type === 'math') {
      const body = chunk.content_markdown.trim();
      const latex = body.startsWith('$$') ? body : `$$\n${body}\n$$`;
      return [{ kind: 'math', latex, sourceChunkId: id, sourceSeq: seq }];
    }

    if (chunk.structural_type === 'code') {
      // Already fenced by the backend chunker; render verbatim as one unit so
      // indentation survives (no paragraph splitting).
      const body = chunk.content_markdown || chunk.plain_text || '';
      const markdown = body.includes('```') ? body : `\`\`\`\n${body}\n\`\`\``;
      return [{ kind: 'code', markdown, sourceChunkId: id, sourceSeq: seq }];
    }

    if (chunk.structural_type === 'footnote') {
      const text = chunk.plain_text || chunk.content_markdown || '';
      return [{ kind: 'footnote', text, sourceChunkId: id, sourceSeq: seq }];
    }

    // Text chunk → split into paragraphs for granular reveal, and within each
    // paragraph promote any inline display-math block into its own math unit
    // so the formula renders as a centered KaTeX block instead of wrapping.
    const text = chunk.content_markdown || chunk.plain_text || '';
    const paragraphs = splitIntoParagraphs(text);

    const units: RevealedUnit[] = [];
    for (const p of paragraphs) {
      const segments = splitParagraphAroundMath(p);
      for (const seg of segments) {
        if (seg.kind === 'math') {
          units.push({
            kind: 'math',
            latex: `$$\n${seg.body}\n$$`,
            sourceChunkId: id,
            sourceSeq: seq,
          });
        } else {
          units.push({
            kind: 'paragraph',
            text: seg.body,
            sourceChunkId: id,
            sourceSeq: seq,
          });
        }
      }
    }
    return units;
  }, []);

  // ── Reading-progress persistence (survives page refresh) ────────────────────
  // Stored per paper, per chapter ("-1" for a linear paper), so reopening or
  // refreshing restores exactly where you left off instead of resetting to the
  // first chunk.
  const progressKey = `pal:progress:${paperId}`;
  const loadProgress = useCallback((): { lastChapter: number | null; seqByChapter: Record<string, number> } => {
    try {
      const raw = localStorage.getItem(progressKey);
      if (raw) {
        const p = JSON.parse(raw);
        return { lastChapter: p.lastChapter ?? null, seqByChapter: p.seqByChapter || {} };
      }
    } catch { /* localStorage blocked / bad JSON */ }
    return { lastChapter: null, seqByChapter: {} };
  }, [progressKey]);
  const saveProgress = useCallback((chapterIndex: number | null, seq: number) => {
    try {
      const p = loadProgress();
      p.seqByChapter[String(chapterIndex ?? -1)] = seq;
      p.lastChapter = chapterIndex;
      localStorage.setItem(progressKey, JSON.stringify(p));
    } catch { /* no-op */ }
  }, [progressKey, loadProgress]);

  // ── Loader: start (or restore) reading a chapter (null = whole paper). ──────
  const startReading = useCallback(async (chapter: Chapter | null) => {
    const chapKey = String(chapter?.index ?? -1);
    initedKeyRef.current = chapKey;
    setLoading(true);
    setAtEnd(false);

    const startCursor = chapter ? chapter.start_sequence - 1 : 0;
    const prog = loadProgress();
    const savedSeq = prog.seqByChapter[chapKey];
    const restoring = typeof savedSeq === 'number' && savedSeq > startCursor;

    const loaded: ChunkData[] = [];
    const units: RevealedUnit[] = [];
    let cursor = startCursor;
    let reachedEnd = false;
    try {
      let guard = 0;
      while (guard++ < 8000) {
        const c = await getNextChunk(paperId, cursor);
        if (!c) { reachedEnd = true; break; }
        if (chapter && c.sequence_order > chapter.end_sequence) { reachedEnd = true; break; }
        loaded.push(c);
        units.push(...chunkToUnits(c));
        cursor = c.sequence_order;
        if (restoring) { if (cursor >= savedSeq) break; }
        else if (loaded.length >= 2) break;
      }
    } catch { /* fall through with whatever loaded */ }

    setChunks(loaded);
    setRevealedUnits(units);
    setCurrentChunkIndex(Math.max(0, loaded.length - 1));
    setParagraphIndexInCurrent(0);
    setLastSeq(cursor);
    setAtEnd(reachedEnd);
    if (chapter) setActiveChapter(chapter);
    saveProgress(chapter?.index ?? null, cursor);
    setLoading(false);
  }, [paperId, chunkToUnits, loadProgress, saveProgress]);

  // ── Reveal the next small unit (paragraph or special element) ──────────────
  const revealNextUnit = useCallback(() => {
    if (loading || atEnd) return;

    // If we have more paragraphs inside the current chunk, just advance the pointer
    const currentChunk = chunks[currentChunkIndex];
    if (currentChunk) {
      const unitsFromCurrent = chunkToUnits(currentChunk);
      const isTextChunk = currentChunk.structural_type === 'text' || !currentChunk.structural_type;

      if (isTextChunk && paragraphIndexInCurrent < unitsFromCurrent.length - 1) {
        setParagraphIndexInCurrent(prev => prev + 1);
        // Scroll to bottom after render
        setTimeout(() => {
          if (readerRef.current) readerRef.current.scrollTop = readerRef.current.scrollHeight;
        }, 20);
        return;
      }
    }

    // Otherwise we need to fetch the next raw chunk from the backend
    fetchAndAppend();
  }, [chunks, currentChunkIndex, paragraphIndexInCurrent, chunkToUnits, loading, atEnd]);

  // Core function: fetch the next raw chunk (after the current cursor) and
  // convert it into units. Uses the gap-tolerant "after" endpoint so a hole in
  // the sequence numbers never truncates the document.
  const fetchAndAppend = useCallback(async () => {
    if (loading || atEnd) return;
    setLoading(true);

    try {
      const chunk = await getNextChunk(paperId, lastSeq);
      // Stop at end-of-document, or at the end of the active chapter (book mode).
      if (!chunk || (activeChapter && chunk.sequence_order > activeChapter.end_sequence)) {
        setAtEnd(true);
        return;
      }

      setChunks((prev) => [...prev, chunk]);

      const newUnits = chunkToUnits(chunk);

      // Append the new units
      setRevealedUnits(prev => [...prev, ...newUnits]);

      // Reset paragraph pointer for the new chunk
      setCurrentChunkIndex(chunks.length); // the index it will have after setState
      setParagraphIndexInCurrent(0);

      setLastSeq(chunk.sequence_order);
      saveProgress(activeChapter?.index ?? null, chunk.sequence_order);

      setTimeout(() => {
        if (readerRef.current) {
          readerRef.current.scrollTop = readerRef.current.scrollHeight;
        }
      }, 30);
    } catch {
      setAtEnd(true);
    } finally {
      setLoading(false);
    }
  }, [paperId, loading, atEnd, chunkToUnits, chunks.length, lastSeq, activeChapter, saveProgress]);

  // Load rich figure descriptions (for beautiful architecture rendering)
  useEffect(() => {
    if (!meta || meta.status !== 'complete') return;
    getFigureDescriptions(paperId)
      .then((descs) => {
        const map: Record<string, FigureDescription> = {};
        for (const d of descs) {
          map[d.chunk_id] = d;
        }
        setFigureDescriptions(map);
      })
      .catch(() => {});
  }, [paperId, meta?.status]);

  // Linear papers: start (or restore) reading as soon as chunks exist — no need
  // to wait for "complete" (embeddings/summaries/figures keep running).
  useEffect(() => {
    if (!meta) return;
    if (totalChunks === 0) return;
    if (isBook) return;                       // books wait for a chapter choice
    if (initedKeyRef.current === '-1') return; // already started
    startReading(null);
  }, [meta, totalChunks, isBook, startReading]);

  // Books: load the chapter list, and auto-resume the last chapter you were in.
  useEffect(() => {
    if (!isBook) return;
    if (totalChunks === 0) return;
    if (chapters.length > 0) return;
    getChapters(paperId)
      .then(({ chapters: chs }) => {
        setChapters(chs);
        const prog = loadProgress();
        if (prog.lastChapter != null) {
          const ch = chs.find((c) => c.index === prog.lastChapter);
          if (ch) startReading(ch);
        }
      })
      .catch(() => {});
  }, [isBook, totalChunks, paperId, chapters.length, loadProgress, startReading]);

  // Legacy alias kept for minimal breakage
  const revealNext = revealNextUnit;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'd' || e.key === 'D') dPressed.current = true;
      if (e.key === 'ArrowDown' && dPressed.current) {
        e.preventDefault();
        revealNext();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'd' || e.key === 'D') dPressed.current = false;
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, [revealNext]);

  const handleReconstructOrder = useCallback(async () => {
    setReconstructionStatus('running');
    try {
      await triggerReadingOrderReconstruction(paperId);
      // Poll a bit until it appears on the document
      const poll = setInterval(async () => {
        try {
          const m = await getPaper(paperId);
          if (m.reading_order && Array.isArray(m.reading_order)) {
            setReadingOrder(m.reading_order);
            setReconstructionStatus('done');
            clearInterval(poll);
          }
        } catch {}
      }, 3000);
      setTimeout(() => clearInterval(poll), 120000); // safety
    } catch (e) {
      setReconstructionStatus('error');
      console.error(e);
    }
  }, [paperId]);

  const handleDelete = useCallback(async () => {
    const ok = window.confirm(
      `Delete "${meta?.original_filename || paper.title}"?\n\nThis removes the database rows AND the on-disk files (raw PDF, extracted images, MinerU output). It cannot be undone.`,
    );
    if (!ok) return;
    setDeleting(true);
    try {
      await deletePaper(paperId);
      onBack();
    } catch (e) {
      window.alert(`Delete failed: ${(e as Error).message}`);
      setDeleting(false);
    }
  }, [paperId, meta, paper.title, onBack]);

  const displayTitle = meta?.original_filename?.replace(/\.pdf$/i, '') || paper.title;
  const displayPages = meta?.page_count ?? paper.pages ?? 0;
  const status = meta?.status ?? 'queued';
  const isReady = status === 'complete';
  const isFailed = status === 'failed';
  const total = totalChunks > 0 ? totalChunks : chunks.length;
  const progress = total > 0 ? Math.min(1, chunks.length / total) : 0;
  // Reading can begin as soon as chunks exist, even while the paper is still
  // being embedded / summarized in the background. In book mode a chapter must
  // be chosen first.
  const canRead = total > 0 && (!isBook || activeChapter !== null);
  // Show the chapter chooser when this is a book and no chapter is open yet.
  const showChapterPicker = isBook && total > 0 && !activeChapter;

  const handleSelectChapter = useCallback((ch: Chapter) => {
    initedKeyRef.current = null; // force re-init for the newly chosen chapter
    startReading(ch);
  }, [startReading]);

  const handleBackToChapters = useCallback(() => {
    setActiveChapter(null);
    setChunks([]);
    setRevealedUnits([]);
    setAtEnd(false);
    setLastSeq(0);
    initedKeyRef.current = null;
  }, []);

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg)' }}>

      {/* ── Top bar ── */}
      <header
        className="shrink-0 px-6 h-13 py-2.5 flex items-center gap-4"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[12.5px]"
          style={{ color: 'var(--muted)' }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--fg)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
        >
          <IconBack className="w-3.5 h-3.5" />
          <span>Library</span>
        </button>
        <span className="h-4 w-px" style={{ background: 'var(--border)' }} />
        <div className="flex items-center gap-2 min-w-0">
          <IconDoc className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--muted)' }} />
          <span
            className="font-serif text-[14.5px] tracking-tight truncate"
            style={{ color: 'var(--fg)' }}
          >
            {displayTitle}
          </span>
          {!isReady && (
            <span
              className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded shrink-0"
              style={{
                color: isFailed ? '#c0392b' : 'var(--accent)',
                background: 'var(--bg-2)',
                border: '1px solid var(--border)',
              }}
            >
              {status}
            </span>
          )}
        </div>
        <div className="ml-auto flex items-center gap-4">
          {/* extractor pill — confirms which parser produced these chunks */}
          {meta?.extractor && (
            <ExtractorPill
              extractor={meta.extractor}
              onReextract={async () => {
                if (!confirm(
                  'Re-extract this paper with MinerU?\n\n' +
                  'This will wipe the cached chunks/embeddings and re-run MinerU ' +
                  'from the original PDF. For a large book this can take a while.'
                )) return;
                try {
                  await reextractPaper(paperId);
                  alert('Re-extraction queued. Watch the status pill in the header.');
                  // Trigger an immediate metadata refresh so status flips to processing.
                  const m = await getPaper(paperId);
                  setMeta(m);
                } catch (e) {
                  alert(`Re-extract failed: ${(e as Error).message}`);
                }
              }}
              onRechunk={async () => {
                if (!confirm(
                  'Re-chunk this paper from the cached extraction?\n\n' +
                  'Fast (seconds) — re-runs only the chunker (gap-free sequencing, ' +
                  'code/JSON blocks, inline math) on the already-extracted output. ' +
                  'Embeddings regenerate in the background afterward.'
                )) return;
                try {
                  const r = await rechunkPaper(paperId);
                  alert(`Re-chunked: ${r.chunks_total} chunks. Reopen the paper to see the new reading flow.`);
                  // Reset the reader so it reloads from the first chunk.
                  setChunks([]);
                  setRevealedUnits([]);
                  setLastSeq(0);
                  setAtEnd(false);
                  const n = await getChunkCount(paperId).catch(() => 0);
                  setTotalChunks(n);
                } catch (e) {
                  alert(`Re-chunk failed: ${(e as Error).message}`);
                }
              }}
            />
          )}
          <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            chunk {chunks.length} / {total || '?'}
          </span>
          <div
            className="w-32 h-[3px] rounded-full overflow-hidden"
            style={{ background: 'var(--bg-3)' }}
          >
            <div
              className="h-full transition-[width] duration-300"
              style={{
                width: `${progress * 100}%`,
                background: 'var(--accent)',
              }}
            />
          </div>
          <button
            onClick={handleDelete}
            disabled={deleting}
            title="Delete this paper"
            className="text-[12px] px-2.5 py-1.5 rounded-md flex items-center gap-1.5"
            style={{
              color: '#c0392b',
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              opacity: deleting ? 0.5 : 1,
            }}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </header>

      {/* ── Split layout (drag the divider to resize the chat pane) ── */}
      <div ref={splitRef} className="flex-1 min-h-0 flex">

        {/* Left: reading pane */}
        <section
          className="relative flex flex-col min-h-0"
          style={{
            width: `calc(100% - ${chatWidthPct}% - 6px)`,
            borderRight: '1px solid var(--border)',
            background: 'var(--bg)',
          }}
        >
          <div ref={readerRef} className="flex-1 overflow-y-auto thin-scroll">
            <div className="max-w-[680px] mx-auto px-10 pt-16 pb-40">

              {/* paper meta — driven by real backend data */}
              <div className="mb-12">
                <div
                  className="text-[10.5px] font-mono uppercase tracking-[0.12em]"
                  style={{ color: 'var(--muted)' }}
                >
                  {displayPages > 0 ? `${displayPages} pages` : '— pages'}
                  {totalChunks > 0 ? ` · ${totalChunks} chunks` : ''}
                  {meta?.file_size_bytes
                    ? ` · ${(meta.file_size_bytes / (1024 * 1024)).toFixed(1)} MB`
                    : ''}
                </div>
                <div className="mt-2 text-[12px]" style={{ color: 'var(--muted)' }}>
                  Reading one structural unit at a time. Press{' '}
                  <kbd className="kbd">D</kbd> + <kbd className="kbd">↓</kbd>{' '}
                  to advance.
                </div>
              </div>

              {/* Book mode: which chapter am I reading + jump back to the list */}
              {isBook && activeChapter && (
                <div className="mb-8 flex items-center gap-3">
                  <span className="text-[10.5px] font-mono uppercase tracking-[0.12em]" style={{ color: 'var(--muted)' }}>
                    Chapter
                  </span>
                  <span className="font-serif text-[15px]" style={{ color: 'var(--fg)' }}>
                    {activeChapter.title}
                  </span>
                  <button
                    onClick={handleBackToChapters}
                    className="ml-auto text-[11.5px] px-2.5 py-1 rounded-md"
                    style={{ color: 'var(--accent)', border: '1px solid var(--border)', background: 'var(--bg)' }}
                  >
                    All chapters
                  </button>
                </div>
              )}

              {/* Book mode: chapter chooser (shown until a chapter is opened) */}
              {showChapterPicker && (
                <ChapterPicker
                  chapters={chapters}
                  onSelect={handleSelectChapter}
                  progress={loadProgress()}
                />
              )}

              {/* Status banner if not ready */}
              {!isReady && !isFailed && (
                <div
                  className="mb-8 px-4 py-3 rounded-md text-[12.5px]"
                  style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--muted)' }}
                >
                  This paper is still <span style={{ color: 'var(--accent)' }}>{status}</span> in the
                  background. Chunks become available as soon as the pipeline finishes. The page
                  refreshes automatically.
                </div>
              )}
              {isFailed && (
                <div
                  className="mb-8 px-4 py-3 rounded-md text-[12.5px]"
                  style={{ background: 'var(--bg-2)', border: '1px solid #c0392b', color: '#c0392b' }}
                >
                  Pipeline failed. {meta?.error_message || 'Check backend logs for details.'}
                </div>
              )}

              {!showChapterPicker && (
                <div className="space-y-6">
                  {revealedUnits.map((unit, i) => (
                    <GranularUnit
                      key={`${unit.sourceChunkId}-${i}`}
                      unit={unit}
                      isLast={i === revealedUnits.length - 1}
                      figureDescriptions={figureDescriptions}
                    />
                  ))}
                  {loading && (
                    <div className="text-[13px] text-center py-2" style={{ color: 'var(--muted)' }}>
                      Loading…
                    </div>
                  )}
                </div>
              )}

              {!showChapterPicker && canRead && atEnd && (
                <div className="mt-12 text-[12px] font-mono" style={{ color: 'var(--muted)' }}>
                  {isBook && activeChapter ? (
                    <div className="flex items-center gap-3">
                      <span>— end of “{activeChapter.title}” —</span>
                      <button
                        onClick={handleBackToChapters}
                        className="px-2.5 py-1 rounded-md"
                        style={{ color: 'var(--accent)', border: '1px solid var(--border)', background: 'var(--bg)' }}
                      >
                        Choose another chapter
                      </button>
                    </div>
                  ) : (
                    <span>— end of indexed chunks —</span>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* AI Reading Order Reconstruction Controls (for two-column papers) */}
          {isReady && (
            <div className="mb-4 flex items-center gap-3 text-[12.5px]">
              <button
                onClick={handleReconstructOrder}
                disabled={reconstructionStatus === 'running'}
                className="px-3 py-1 rounded border text-[12px]"
                style={{ borderColor: 'var(--border)' }}
              >
                {reconstructionStatus === 'running' ? 'Reconstructing with AI…' :
                 reconstructionStatus === 'done' ? 'Reconstruction done ✓' :
                 reconstructionStatus === 'error' ? 'Reconstruction failed' :
                 'Reconstruct Reading Order (AI)'}
              </button>

              {readingOrder && readingOrder.length > 0 && (
                <>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={useLogicalOrder}
                      onChange={(e) => {
                        const enabled = e.target.checked;
                        setUseLogicalOrder(enabled);
                        if (enabled && readingOrder) {
                          // Reset reveal when switching to logical order
                          setRevealedUnits([]);
                          setLogicalPointer(0);
                          // Could trigger loading first few in logical order here
                        }
                      }}
                    />
                    <span>Use AI-corrected reading order</span>
                  </label>
                  <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
                    ({readingOrder.length} items)
                  </span>
                </>
              )}
            </div>
          )}

          {/* keyboard reveal cue — only when there's something to reveal */}
          {canRead && !atEnd && (
            <div className="absolute bottom-5 left-1/2 -translate-x-1/2 z-10">
              <div className="reveal-bar rounded-full px-3.5 py-2 flex items-center gap-2.5">
                <span className="text-[11.5px]" style={{ color: 'var(--muted)' }}>Press</span>
                <span className="flex items-center gap-1">
                  <kbd className="kbd">D</kbd>
                  <span className="text-[10px]" style={{ color: 'var(--muted)' }}>+</span>
                  <kbd className="kbd">↓</kbd>
                </span>
                <span className="text-[11.5px]" style={{ color: 'var(--muted)' }}>to reveal next part</span>
                <button
                  onClick={revealNext}
                  className="ml-1 text-[11.5px] flex items-center gap-1"
                  style={{ color: 'var(--accent)' }}
                >
                  next <IconArrow className="w-3 h-3" />
                </button>
              </div>
            </div>
          )}
        </section>

        {/* Drag handle — 6px wide, full-height, becomes accent-colored on hover/drag */}
        <div
          role="separator"
          aria-orientation="vertical"
          title="Drag to resize chat"
          className="shrink-0 relative group cursor-col-resize select-none"
          style={{ width: 6, background: 'var(--border)' }}
          onMouseDown={(e) => {
            e.preventDefault();
            draggingRef.current = true;
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
          }}
          onDoubleClick={() => {
            // double-click resets to default
            setChatWidthPct(40);
            try { localStorage.setItem('pal:chat:width', '40'); } catch { /* no-op */ }
          }}
        >
          {/* Hover/drag indicator: a 2px accent stripe down the middle */}
          <div
            className="absolute inset-y-0 left-1/2 -translate-x-1/2 transition-all"
            style={{
              width: 2,
              background: draggingRef.current ? 'var(--accent)' : 'transparent',
            }}
          />
          <style>{`
            div[role="separator"]:hover > div { background: var(--accent); }
          `}</style>
        </div>

        {/* Right: chat pane */}
        <div
          className="min-h-0 flex flex-col"
          style={{ width: `${chatWidthPct}%` }}
        >
          <ChatPane
            paperId={paperId}
            currentSequenceOrder={chunks.length > 0 ? chunks[chunks.length - 1].sequence_order : null}
            revealedCount={revealedUnits.length}
          />
        </div>
      </div>
    </div>
  );
}

// ── Chunk renderer for API chunks ─────────────────────────────────────────────
//
// Match ChapterPal's reading pane: no left-margin sequence labels, generous
// serif body, KaTeX inline math, real HTML tables (via remark-gfm + rehype-raw
// since MinerU may emit either pipe-tables or raw <table> HTML), centered
// figures with a tiny filename caption, and a faint heading rule.

const MARKDOWN_REMARK = [remarkGfm, remarkMath];
const MARKDOWN_REHYPE = [rehypeRaw, rehypeKatex];

function Md({ children }: { children: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

function ApiChunkBlock({
  chunk,
  active,
  isLastRevealed,
}: {
  chunk: ChunkData;
  active: boolean;
  isLastRevealed: boolean;
}) {
  const dim = active ? undefined : { opacity: 0.6 };
  const cursorClass = isLastRevealed && chunk.structural_type === 'text' ? 'chunk-cursor' : '';

  if (chunk.structural_type === 'heading') {
    const text = chunk.plain_text || chunk.content_markdown.replace(/^#+\s*/, '');
    const level = chunk.heading_path?.length ?? 1;
    const size = level === 1 ? 32 : level === 2 ? 26 : 22;
    return (
      <div className="transition-opacity duration-300" style={dim}>
        <h2
          className="chunk-heading"
          style={{ fontSize: size }}
        >
          {text}
        </h2>
      </div>
    );
  }

  if (chunk.structural_type === 'figure' && chunk.image_url) {
    const filename = chunk.image_refs?.[0] ?? '';
    return (
      <div className="transition-opacity duration-300" style={dim}>
        <div className="flex flex-col items-center">
          <img
            src={chunk.image_url}
            alt={chunk.plain_text || 'figure'}
            className="max-h-[520px] object-contain rounded-sm"
            style={{ background: '#fff', padding: 16 }}
          />
          {filename && (
            <div
              className="mt-4 text-[12.5px] font-mono self-start"
              style={{ color: 'var(--muted)' }}
            >
              {filename}
            </div>
          )}
          {chunk.plain_text && (
            <div
              className="mt-2 font-serif text-[14px] leading-[1.5] italic"
              style={{ color: 'var(--fg-2)' }}
            >
              {chunk.plain_text}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (chunk.structural_type === 'math') {
    // Wrap raw LaTeX in $$...$$ if the producer didn't already so rehype-katex
    // catches it. MinerU's content_list emits bare LaTeX in the "text" field;
    // the chunker now wraps it, but be defensive for the markdown-fallback path.
    const body = chunk.content_markdown.trim();
    const wrapped = body.startsWith('$$') ? body : `$$\n${body}\n$$`;
    return (
      <div className="transition-opacity duration-300 my-4 flex justify-center overflow-x-auto" style={dim}>
        <Md>{wrapped}</Md>
      </div>
    );
  }

  if (chunk.structural_type === 'footnote') {
    const text = chunk.plain_text || chunk.content_markdown;
    return (
      <div className="transition-opacity duration-300 my-3" style={dim}>
        <div
          className="rounded-md border-l-4 pl-3 pr-3 py-2 text-[12.5px] leading-[1.55]"
          style={{
            borderColor: 'var(--accent)',
            background: 'var(--bg-2)',
            color: 'var(--fg-2)',
          }}
        >
          <span
            className="font-mono uppercase tracking-[1px] text-[10px] mr-2"
            style={{ color: 'var(--accent)' }}
          >
            Side Note
          </span>
          <span className="italic">{text}</span>
        </div>
      </div>
    );
  }

  if (chunk.structural_type === 'table') {
    return (
      <div className="transition-opacity duration-300" style={dim}>
        <Md>{chunk.content_markdown}</Md>
      </div>
    );
  }

  // Default: text chunk — render the markdown body so inline math, italics,
  // bold, lists, and bracketed citations look right.
  return (
    <div className={`transition-opacity duration-300 ${cursorClass}`} style={dim}>
      <Md>{chunk.content_markdown || chunk.plain_text}</Md>
    </div>
  );
}

// ── NEW: Granular reveal renderer (paragraph by paragraph + clean special elements) ──

function GranularUnit({
  unit,
  isLast,
  figureDescriptions = {},
}: {
  unit: RevealedUnit;
  isLast: boolean;
  figureDescriptions?: Record<string, FigureDescription>;
}) {
  const baseClass = "transition-opacity duration-300";
  const lastClass = isLast ? "animate-[fadeIn_0.2s_ease]" : "";

  if (unit.kind === 'heading') {
    const size = unit.level === 1 ? 30 : unit.level === 2 ? 24 : 20;
    return (
      <div className={`${baseClass} ${lastClass}`}>
        <h2 className="chunk-heading" style={{ fontSize: size }}>
          {unit.text}
        </h2>
      </div>
    );
  }

  if (unit.kind === 'paragraph') {
    return (
      <div className={`${baseClass} ${lastClass} md-body`}>
        <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
          {unit.text}
        </ReactMarkdown>
      </div>
    );
  }

  if (unit.kind === 'table') {
    // Use structured data if available (much cleaner), fall back to markdown
    const json = unit.tableJson;
    if (json?.headers && json?.rows) {
      return (
        <div className={`${baseClass} ${lastClass} my-4`}>
          <div className="rounded-lg border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
            <table className="w-full text-[13.5px]">
              <thead>
                <tr>
                  {json.headers.map((h: string, i: number) => (
                    <th key={i} className="px-4 py-2 text-left font-semibold border-b" style={{ borderColor: 'var(--border)', background: 'var(--bg-2)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {json.rows.map((row: string[], ri: number) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci} className="px-4 py-2 border-b align-top" style={{ borderColor: 'var(--border)' }}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-[11px] mt-1.5 font-mono" style={{ color: 'var(--muted)' }}>
            Table • seq {unit.sourceSeq}
          </div>
        </div>
      );
    }

    // Fallback to markdown rendering
    return (
      <div className={`${baseClass} ${lastClass} my-4`}>
        <div className="rounded-lg border p-4" style={{ borderColor: 'var(--border)', background: 'var(--bg-2)' }}>
          <Md>{unit.markdown}</Md>
        </div>
      </div>
    );
  }

  if (unit.kind === 'figure') {
    const richDesc = figureDescriptions?.[unit.sourceChunkId];

    return (
      <div className={`${baseClass} ${lastClass} my-6`}>
        <div className="flex flex-col items-center rounded-lg border p-4" style={{ borderColor: 'var(--border)' }}>
          {unit.imageUrl && (
            <img
              src={unit.imageUrl}
              alt={unit.caption || 'figure'}
              className="max-h-[480px] object-contain rounded"
              style={{ background: '#fff', padding: 12 }}
            />
          )}
          {unit.filename && (
            <div className="mt-3 text-[11.5px] font-mono" style={{ color: 'var(--muted)' }}>
              {unit.filename}
            </div>
          )}

          {/* Rich VLM description (high quality, generated at ingestion) */}
          {richDesc?.description_markdown ? (
            <div className="mt-4 w-full max-w-[72ch] text-[13.5px] leading-relaxed border-t pt-3" style={{ borderColor: 'var(--border)', color: 'var(--fg)' }}>
              <div className="uppercase tracking-[1px] text-[10px] mb-1.5" style={{ color: 'var(--muted)' }}>
                AI Description (from paper diagram)
              </div>
              <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE}>
                {richDesc.description_markdown}
              </ReactMarkdown>
            </div>
          ) : unit.caption ? (
            <div className="mt-2 text-[13.5px] italic text-center max-w-[72ch]" style={{ color: 'var(--fg-2)' }}>
              {unit.caption}
            </div>
          ) : null}

          <div className="mt-2 text-[10px] font-mono" style={{ color: 'var(--muted)' }}>
            Figure • seq {unit.sourceSeq}
          </div>
        </div>
      </div>
    );
  }

  if (unit.kind === 'math') {
    return (
      <div className={`${baseClass} ${lastClass} my-4 flex justify-center overflow-x-auto`}>
        <Md>{unit.latex}</Md>
      </div>
    );
  }

  if (unit.kind === 'code') {
    // Fenced code/JSON: render verbatim in a monospace block (remark-gfm).
    return (
      <div className={`${baseClass} ${lastClass} my-4 code-block`}>
        <Md>{unit.markdown}</Md>
      </div>
    );
  }

  if (unit.kind === 'footnote') {
    return (
      <div className={`${baseClass} ${lastClass} my-4`}>
        <div
          className="rounded-md border-l-4 pl-3 pr-3 py-2 text-[12.5px] leading-[1.55]"
          style={{
            borderColor: 'var(--accent)',
            background: 'var(--bg-2)',
            color: 'var(--fg-2)',
          }}
        >
          <span
            className="font-mono uppercase tracking-[1px] text-[10px] mr-2"
            style={{ color: 'var(--accent)' }}
          >
            Side Note
          </span>
          <span className="italic">{unit.text}</span>
        </div>
      </div>
    );
  }

  return null;
}


// ── Extractor pill ──────────────────────────────────────────────────────────
// Shows whether the paper was parsed by MinerU (full fidelity) or by the
// PyMuPDF fallback (degraded). For fallback docs, exposes a one-click
// "Re-extract with MinerU" button so users can upgrade the parse in place.

// ── Chapter picker (book mode) ────────────────────────────────────────────────
// Lets the reader jump straight to a chapter (incl. the introduction / front
// matter) instead of paging the whole book, and shows a "resume" hint where
// reading progress was saved.

function ChapterPicker({
  chapters,
  onSelect,
  progress,
}: {
  chapters: Chapter[];
  onSelect: (ch: Chapter) => void;
  progress: { lastChapter: number | null; seqByChapter: Record<string, number> };
}) {
  if (chapters.length === 0) {
    return (
      <div className="text-[13px]" style={{ color: 'var(--muted)' }}>
        No chapters detected yet. They appear once processing finishes — or read it as a paper.
      </div>
    );
  }
  return (
    <div>
      <div className="text-[13px] mb-4" style={{ color: 'var(--muted)' }}>
        Pick a chapter to read. Your place in each chapter is remembered.
      </div>
      <div className="space-y-2">
        {chapters.map((ch) => {
          const saved = progress.seqByChapter[String(ch.index)];
          const started = typeof saved === 'number' && saved >= ch.start_sequence;
          return (
            <button
              key={ch.index}
              onClick={() => onSelect(ch)}
              className="w-full text-left rounded-lg px-4 py-3 flex items-center gap-3 transition-colors"
              style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
            >
              <span className="font-mono text-[11px] w-6 text-center" style={{ color: 'var(--muted)' }}>
                {ch.index + 1}
              </span>
              <span className="font-serif text-[15px] flex-1 min-w-0 truncate" style={{ color: 'var(--fg)' }}>
                {ch.title}
              </span>
              {started && (
                <span className="text-[10.5px] font-mono px-1.5 py-0.5 rounded" style={{ color: 'var(--accent)', border: '1px solid var(--border)' }}>
                  resume
                </span>
              )}
              <span className="text-[10.5px] font-mono" style={{ color: 'var(--muted)' }}>
                {ch.chunk_count} blocks
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ExtractorPill({
  extractor,
  onReextract,
  onRechunk,
}: {
  extractor: string;
  onReextract: () => void;
  onRechunk?: () => void;
}) {
  const isMineru = extractor === 'mineru';
  const isFallback = extractor === 'pymupdf_fallback';
  const dotColor = isMineru ? 'var(--ok)' : isFallback ? '#f59e0b' : 'var(--muted)';
  const label = isMineru ? 'MinerU' : isFallback ? 'PyMuPDF fallback' : extractor;
  const title = isMineru
    ? 'Parsed by MinerU — typed equations, footnotes, table structure.'
    : isFallback
    ? 'Parsed by PyMuPDF fallback (no math LaTeX, no table structure). Click to re-extract with MinerU.'
    : `Extractor: ${extractor}`;

  return (
    <div
      title={title}
      className="flex items-center gap-2 px-2 py-1 rounded-md text-[11px] font-mono"
      style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--muted)' }}
    >
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: dotColor }} />
      <span>{label}</span>
      {onRechunk && (
        <button
          onClick={onRechunk}
          title="Re-run only the chunker on the cached extraction (fast). Applies the latest chunking (gap-free, code/JSON, inline math)."
          className="ml-1 px-1.5 py-0.5 rounded text-[10.5px]"
          style={{ color: 'var(--accent)', border: '1px solid var(--border)', background: 'var(--bg)' }}
        >
          re-chunk
        </button>
      )}
      {isFallback && (
        <button
          onClick={onReextract}
          className="ml-1 px-1.5 py-0.5 rounded text-[10.5px]"
          style={{ color: 'var(--accent)', border: '1px solid var(--border)', background: 'var(--bg)' }}
        >
          re-extract
        </button>
      )}
    </div>
  );
}
