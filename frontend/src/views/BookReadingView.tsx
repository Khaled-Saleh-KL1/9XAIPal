/**
 * Book reader: the original reveal-one-chunk-at-a-time experience.
 *
 * Preserved verbatim for doc_kind='book'. Papers now render as a continuous
 * article with margin notes (see ArticleReader.tsx); ReadingView.tsx picks
 * between the two. Nothing here is on the paper path.
 */
import { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { MARKDOWN_REMARK, MARKDOWN_REHYPE , MARKDOWN_COMPONENTS } from '../lib/markdown';
import { displayTitle as paperDisplayTitle } from '../lib/titles';
import { loadReadingProgress, markChapterFinished, saveReadingPosition } from '../lib/readingPosition';
import type { Paper } from '../types';
import { IconBack, IconDoc, IconArrow } from '../components/Icons';
import { UserMenuInline } from '../components/UserMenu';
import { StrictScopeToggle } from '../components/StrictScopeToggle';
import { TitleEditor } from '../components/TitleEditor';
import { useConfirm } from '../components/ConfirmDialog';
import { ChatPane } from './ChatPane';
import { PageMapProvider, useFetchedPageMap } from '../lib/pageMap';
import {
  getNextChunk,
  getChunk,
  getChunksRange,
  getChunkCount,
  getPaper,
  getDocumentAssetUrl,
  getFigureDescriptions,
  triggerReadingOrderReconstruction,
  reextractPaper,
  rechunkPaper,
  renamePaper,
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
  | { kind: 'table'; markdown: string; sourceChunkId: string; sourceSeq: number; tableJson?: any; imageUrl?: string }
  | { kind: 'figure'; imageUrl?: string; caption?: string; filename?: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'math'; latex: string; imageUrl?: string; sourceChunkId: string; sourceSeq: number }
  | { kind: 'code'; markdown: string; sourceChunkId: string; sourceSeq: number; imageUrl?: string }
  | { kind: 'footnote'; text: string; sourceChunkId: string; sourceSeq: number };

interface Props {
  paper: Paper;
  paperId: string;
  onBack: () => void;
  /** A chunk sequence to jump to on open — the desk, or the raw PDF
   * viewer's "Read structured" button (already resolved to a sequence via
   * pageToSequence). */
  jumpToSequence?: number | null;
  onJumped?: () => void;
  /** The reader's own "Raw file" button: opens the source PDF at the page
   * the current chunk came from (null if the current chunk carries none). */
  onOpenRaw?: (page: number | null) => void;
}

export function BookReadingView({ paper, paperId, onBack, jumpToSequence = null, onJumped, onOpenRaw }: Props) {
  const confirm = useConfirm();
  // Fetched rather than derived from `chunks`: those hold one chapter's window,
  // and the agent cites blocks from chapters it never loaded. See pageMap.ts.
  const pageMap = useFetchedPageMap(paperId);
  const [chunks, setChunks] = useState<ChunkData[]>([]);
  // Cursor for gap-tolerant paging: the highest sequence_order loaded so far.
  // We always ask the backend for "the next chunk after this", starting at 0.
  const [lastSeq, setLastSeq] = useState(0);
  const [loading, setLoading] = useState(false);
  const [atEnd, setAtEnd] = useState(false);
  const [meta, setMeta] = useState<PaperMeta | null>(null);
  const [totalChunks, setTotalChunks] = useState<number>(0);
  /** Whether the header title is being edited in place. */
  const [renamingTitle, setRenamingTitle] = useState(false);

  // ── Book mode: chapter-by-chapter navigation ───────────────────────────────
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [activeChapter, setActiveChapter] = useState<Chapter | null>(null);
  // Tracks which chapter ("-1" for a linear paper) has been initialized into the
  // reader so we don't re-run the loader on every render.
  const initedKeyRef = useRef<string | null>(null);
  const isBook = meta?.doc_kind === 'book';

  // Granular reveal: we break text into paragraphs and treat tables/figures as atomic clean units
  const [revealedUnits, setRevealedUnits] = useState<RevealedUnit[]>([]);
  const [pendingUnits, setPendingUnits] = useState<RevealedUnit[]>([]);
  const [currentChunkIndex, setCurrentChunkIndex] = useState(0);
  const [, setParagraphIndexInCurrent] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Rich figure descriptions (generated at ingestion with VLM)
  const [figureDescriptions, setFigureDescriptions] = useState<Record<string, FigureDescription>>({});

  // LLM-corrected reading order for two-column / complex papers
  const [readingOrder, setReadingOrder] = useState<number[] | null>(null);
  const [useLogicalOrder, setUseLogicalOrder] = useState(false);
  const [reconstructionStatus, setReconstructionStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');

  // The reading ceiling: the furthest chunk any revealed unit came from.
  // Everything the chat is allowed to see is bounded by this, which is what
  // stops an answer from containing the end of a book being read in order.
  const maxRevealedSeq = revealedUnits.length
    ? revealedUnits.reduce((max, u) => (u.sourceSeq > max ? u.sourceSeq : max), 0)
    : null;

  const readerRef = useRef<HTMLDivElement>(null);
  const dPressed = useRef(false);

  // ── Resizable split: chat-pane width as a percentage of the layout area.
  // Persisted across sessions; clamped to [20%, 75%] so neither pane vanishes.
  const splitRef = useRef<HTMLDivElement>(null);
  const [chatWidthPct, setChatWidthPct] = useState<number>(() => {
    try {
      const stored = parseFloat(localStorage.getItem('pal:chat:width') || '');
      if (Number.isFinite(stored) && stored >= 20 && stored <= 75) return stored;
    } catch { /* localStorage blocked, fall through */ }
    return 40;
  });
  const draggingRef = useRef(false);
  useEffect(() => {
    function resizeTo(clientX: number) {
      if (!splitRef.current) return;
      const rect = splitRef.current.getBoundingClientRect();
      const fromRight = rect.right - clientX;
      const pct = (fromRight / rect.width) * 100;
      const clamped = Math.max(20, Math.min(75, pct));
      setChatWidthPct(clamped);
    }
    function onMove(e: MouseEvent) {
      if (!draggingRef.current) return;
      resizeTo(e.clientX);
    }
    function onTouchMove(e: TouchEvent) {
      if (!draggingRef.current || e.touches.length === 0) return;
      // The divider is the drag target, so the page itself must not also
      // scroll/refresh underneath the reader's finger while it's held.
      e.preventDefault();
      resizeTo(e.touches[0].clientX);
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
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('touchend', onUp);
    window.addEventListener('touchcancel', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onUp);
      window.removeEventListener('touchcancel', onUp);
    };
  }, [chatWidthPct]);

  /**
   * Below 820px the fixed side-by-side split (reading pane + chat pane, each
   * a percentage of a viewport that's now only a few hundred px wide) leaves
   * both unreadable rather than one usable: this is the "AI is not shown, I
   * can't ask a question" report for books specifically. Below that width,
   * show one pane at a time, full-width, with a toggle to switch.
   */
  const [narrow, setNarrow] = useState(() => window.innerWidth < 820);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 820);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const [mobilePane, setMobilePane] = useState<'read' | 'chat'>('read');

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
        // Present exactly when the backend withheld table_json because the
        // reconciled structure was unreliable (see chunker.py's
        // _table_rows_are_consistent) — the page crop MinerU always makes,
        // used as the trustworthy fallback instead of guessed structure.
        imageUrl: chunk.image_url || undefined,
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
      return [{ kind: 'math', latex, imageUrl: chunk.image_url || undefined, sourceChunkId: id, sourceSeq: seq }];
    }

    if (chunk.structural_type === 'code') {
      // Already fenced by the backend chunker; render verbatim as one unit so
      // indentation survives (no paragraph splitting).
      const body = chunk.content_markdown || chunk.plain_text || '';
      const markdown = body.includes('```') ? body : `\`\`\`\n${body}\n\`\`\``;
      // A crop of the page (see chunker.py's crop_code_blocks) whenever a
      // literal code/schema listing has one — a faithful copy of the exact
      // layout OCR text alone can't preserve.
      return [{ kind: 'code', markdown, sourceChunkId: id, sourceSeq: seq, imageUrl: chunk.image_url || undefined }];
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
  // first chunk. The store itself is shared with the article reader — see
  // lib/readingPosition.ts, which is this component's original key and shape
  // moved out so both readers write the same slot.
  const loadProgress = useCallback(() => loadReadingProgress(paperId), [paperId]);
  const saveProgress = useCallback(
    (chapterIndex: number | null, seq: number) => saveReadingPosition(paperId, chapterIndex, seq),
    [paperId],
  );

  // Set when a restore has just loaded content up to a saved position, and
  // consumed by the layout effect below once that content has painted.
  // Restoring used to rebuild the reader's content and then leave them at the
  // TOP of it, so "where you left off" was somewhere down an already-revealed
  // chapter that they had to scroll to find by hand.
  const restoredRef = useRef(false);

  // Set when a chapter is opened fresh (not restored), so the reader starts at
  // its first line. Going BACK to the picker empties the reader and lets the
  // browser clamp the scroll to 0 on its own; "Next chapter" never empties it,
  // so without this the new chapter opens at whatever offset the end of the
  // previous one left behind — i.e. already scrolled past its beginning.
  const scrollToTopRef = useRef(false);

  // Land the reader where they stopped, once the restored content exists.
  //
  // The restore loads chunks up to the saved position and no further, so the
  // LAST revealed unit is that position — which makes scrolling to the bottom
  // both the correct answer and the same thing this reader already does every
  // time it reveals more (see fetchAndAppend). useLayoutEffect so it happens
  // before paint: the reader never sees the top of the chapter flash past.
  useLayoutEffect(() => {
    if (!revealedUnits.length) return;
    if (restoredRef.current) {
      restoredRef.current = false;
      if (readerRef.current) readerRef.current.scrollTop = readerRef.current.scrollHeight;
      return;
    }
    if (scrollToTopRef.current) {
      scrollToTopRef.current = false;
      if (readerRef.current) readerRef.current.scrollTop = 0;
    }
  }, [revealedUnits]);


  // ── Loader: start (or restore) reading a chapter (null = whole paper). ──────
  const startReading = useCallback(async (chapter: Chapter | null, logicalMode = useLogicalOrder) => {
    const chapKey = String(chapter?.index ?? -1);
    initedKeyRef.current = chapKey;
    setLoading(true);
    setAtEnd(false);
    setLoadError(null);

    const startCursor = chapter ? chapter.start_sequence - 1 : 0;
    const prog = loadProgress();
    const savedSeq = prog.seqByChapter[chapKey];
    const restoring = typeof savedSeq === 'number' && savedSeq > startCursor;
    const logicalOrder = logicalMode && readingOrder
      ? readingOrder.filter((seq) => !chapter || (seq >= chapter.start_sequence && seq <= chapter.end_sequence))
      : null;

    const loaded: ChunkData[] = [];
    const units: RevealedUnit[] = [];
    let cursor = startCursor;
    let reachedEnd = false;
    // Distinct from reachedEnd, which is also set when the content simply ran
    // out below a stale saved position.
    let finishedChapter = false;
    try {
      if (logicalOrder?.length) {
        const savedIndex = restoring && savedSeq != null ? logicalOrder.indexOf(savedSeq) : 0;
        const lastIndex = savedIndex >= 0 ? savedIndex : 0;
        for (const sequence of logicalOrder.slice(0, lastIndex + 1)) {
          const chunk = await getChunk(paperId, sequence);
          loaded.push(chunk);
          units.push(...chunkToUnits(chunk));
          cursor = sequence;
        }
      } else if (restoring) {
        // Fast-forward to the saved position in one bulk request instead of
        // one getNextChunk round trip per chunk: a deep chapter used to cost
        // hundreds of sequential HTTP calls just to restore where you left off.
        const cap = chapter ? chapter.end_sequence : savedSeq;
        const range = await getChunksRange(paperId, startCursor, Math.max(1, cap - startCursor + 1));
        for (const c of range) {
          if (chapter && c.sequence_order > chapter.end_sequence) {
            reachedEnd = true; finishedChapter = true; break;
          }
          loaded.push(c);
          units.push(...chunkToUnits(c));
          cursor = c.sequence_order;
          if (cursor >= savedSeq) break;
        }
        // Fewer chunks exist than expected (e.g. re-chunked shorter since the
        // position was saved), so we ran out before reaching it.
        if (!reachedEnd && cursor < savedSeq) reachedEnd = true;
      } else {
        let guard = 0;
        while (guard++ < 8000) {
          const c = await getNextChunk(paperId, cursor);
          if (!c) { reachedEnd = true; finishedChapter = true; break; }
          if (chapter && c.sequence_order > chapter.end_sequence) {
            reachedEnd = true; finishedChapter = true; break;
          }
          loaded.push(c);
          units.push(...chunkToUnits(c));
          cursor = c.sequence_order;
          if (loaded.length >= 1) break;
        }
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Could not load this part of the book.');
    }

    setChunks(loaded);
    // A new session shows one unit at a time. Restoring is intentionally
    // different: those units were already read before the refresh.
    setRevealedUnits(restoring ? units : units.slice(0, 1));
    setPendingUnits(restoring ? [] : units.slice(1));
    setCurrentChunkIndex(Math.max(0, loaded.length - 1));
    setParagraphIndexInCurrent(0);
    setLastSeq(cursor);
    setAtEnd(reachedEnd);
    // Only when the chapter genuinely ran out, not when it merely came up
    // short of a stale saved position (a re-chunk can leave `savedSeq` past
    // the last chunk that now exists — that reader never saw an ending).
    if (chapter && finishedChapter) markChapterFinished(paperId, chapter.index);
    if (chapter) setActiveChapter(chapter);
    saveProgress(chapter?.index ?? null, cursor);
    restoredRef.current = restoring;
    scrollToTopRef.current = !restoring;
    setLoading(false);
  }, [paperId, chunkToUnits, loadProgress, saveProgress, readingOrder, useLogicalOrder]);

  // Core function: fetch the next raw chunk (after the current cursor) and
  // convert it into units. Uses the gap-tolerant "after" endpoint so a hole in
  // the sequence numbers never truncates the document.
  const fetchAndAppend = useCallback(async () => {
    if (loading || atEnd) return;
    setLoading(true);
    setLoadError(null);

    try {
      const logicalOrder = useLogicalOrder && readingOrder
        ? readingOrder.filter((seq) => !activeChapter || (seq >= activeChapter.start_sequence && seq <= activeChapter.end_sequence))
        : null;
      const nextLogicalIndex = logicalOrder ? logicalOrder.indexOf(lastSeq) + 1 : -1;
      const chunk = logicalOrder
        ? (nextLogicalIndex >= 0 && nextLogicalIndex < logicalOrder.length
          ? await getChunk(paperId, logicalOrder[nextLogicalIndex])
          : null)
        : await getNextChunk(paperId, lastSeq);
      // Stop at end-of-document, or at the end of the active chapter (book mode).
      if (!chunk || (activeChapter && chunk.sequence_order > activeChapter.end_sequence)) {
        setAtEnd(true);
        // Reaching this line IS finishing the chapter, and it is the only
        // place that knows so — see readingPosition's note on why the saved
        // sequence number can never be compared against end_sequence to work
        // it out afterwards. Deliberately not marked in the catch below: a
        // network error also sets atEnd, and "the request failed" is not
        // "you finished the chapter".
        if (activeChapter) markChapterFinished(paperId, activeChapter.index);
        return;
      }

      setChunks((prev) => [...prev, chunk]);

      const newUnits = chunkToUnits(chunk);

      // Keep the rest of the chunk pending so each action reveals exactly
      // one paragraph or structural unit.
      setRevealedUnits(prev => [...prev, ...newUnits.slice(0, 1)]);
      setPendingUnits(newUnits.slice(1));

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
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Could not load the next part.');
    } finally {
      setLoading(false);
    }
  }, [paperId, loading, atEnd, chunkToUnits, chunks.length, lastSeq, activeChapter, saveProgress, readingOrder, useLogicalOrder]);

  // ── Reveal the next small unit (paragraph or special element) ──────────────
  const revealNextUnit = useCallback(() => {
    if (loading || atEnd) return;

    if (pendingUnits.length > 0) {
      setRevealedUnits(prev => [...prev, pendingUnits[0]]);
      setPendingUnits(prev => prev.slice(1));
      setParagraphIndexInCurrent(prev => prev + 1);
      setTimeout(() => {
        if (readerRef.current) readerRef.current.scrollTop = readerRef.current.scrollHeight;
      }, 20);
      return;
    }

    // Otherwise we need to fetch the next raw chunk from the backend
    fetchAndAppend();
  }, [pendingUnits, loading, atEnd, fetchAndAppend]);

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

  // Linear papers: start (or restore) reading as soon as chunks exist, no need
  // to wait for "complete" (embeddings/summaries/figures keep running).
  useEffect(() => {
    if (!meta) return;
    if (totalChunks === 0) return;
    if (isBook) return;                       // books wait for a chapter choice
    if (initedKeyRef.current === '-1') return; // already started
    startReading(null);
  }, [meta, totalChunks, isBook, startReading]);

  // Books: load the chapter list, and auto-resume the last chapter you were in.
  // Skipped when a jumpToSequence is already pending below — that jump picks
  // its own chapter and starting position, and firing both would race two
  // startReading calls against each other.
  useEffect(() => {
    if (!isBook) return;
    if (totalChunks === 0) return;
    if (chapters.length > 0) return;
    if (jumpToSequence != null) return;
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
  }, [isBook, totalChunks, paperId, chapters.length, jumpToSequence, loadProgress, startReading]);

  // A sequence to jump to (the desk, or the raw PDF viewer's "Read
  // structured" button) needs the chapter list first, same as auto-resume
  // above — load it directly rather than waiting on that effect, since that
  // one deliberately skips itself while this jump is pending.
  //
  // handledJumpRef, not just the jumpToSequence!=null check: this effect's
  // own getChapters()/setChapters() call changes `chapters`, which is one of
  // its own deps, so without the ref it would re-fire and double-call
  // startReading for the same jump before onJumped ever clears the prop.
  const handledJumpRef = useRef<number | null>(null);
  useEffect(() => {
    if (jumpToSequence == null) { handledJumpRef.current = null; return; }
    if (!isBook || handledJumpRef.current === jumpToSequence) return;
    handledJumpRef.current = jumpToSequence;
    let alive = true;
    const chaptersPromise = chapters.length > 0
      ? Promise.resolve(chapters)
      : getChapters(paperId).then(({ chapters: chs }) => { if (alive) setChapters(chs); return chs; });
    chaptersPromise
      .then((chs) => {
        if (!alive) return;
        const target = chs.find((c) => jumpToSequence >= c.start_sequence && jumpToSequence <= c.end_sequence) ?? null;
        saveProgress(target?.index ?? null, jumpToSequence);
        return startReading(target);
      })
      .catch(() => {})
      .then(() => { if (alive) onJumped?.(); });
    return () => { alive = false; };
  }, [isBook, jumpToSequence, chapters, paperId, saveProgress, startReading, onJumped]);

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

  // ⚠ Both timers are held in a ref so they can be stopped, because neither
  // is owned by the render that started them. Reconstruction takes up to two
  // minutes and the reader is free to close the book or open another one
  // while it runs — the poll then went on firing getPaper every 3s at a view
  // that no longer exists, for the rest of the safety window. Re-triggering
  // stacked another poll on top of the first, since nothing cancelled the
  // old one.
  const orderPollRef = useRef<{
    interval: ReturnType<typeof setInterval>;
    safety: ReturnType<typeof setTimeout>;
  } | null>(null);

  const stopOrderPoll = useCallback(() => {
    if (!orderPollRef.current) return;
    clearInterval(orderPollRef.current.interval);
    clearTimeout(orderPollRef.current.safety);
    orderPollRef.current = null;
  }, []);

  // Unmount, and switching to another book, both stop it.
  useEffect(() => stopOrderPoll, [paperId, stopOrderPoll]);

  const handleReconstructOrder = useCallback(async () => {
    setReconstructionStatus('running');
    try {
      await triggerReadingOrderReconstruction(paperId);
      stopOrderPoll(); // never run two polls at once
      // Poll a bit until it appears on the document
      const interval = setInterval(async () => {
        try {
          const m = await getPaper(paperId);
          if (m.reading_order && Array.isArray(m.reading_order)) {
            setReadingOrder(m.reading_order);
            setReconstructionStatus('done');
            stopOrderPoll();
          }
        } catch {}
      }, 3000);
      const safety = setTimeout(stopOrderPoll, 120000);
      orderPollRef.current = { interval, safety };
    } catch (e) {
      setReconstructionStatus('error');
      console.error(e);
    }
  }, [paperId, stopOrderPoll]);

  const displayTitle = meta ? paperDisplayTitle(meta) : paper.title;

  /**
   * Rename from the reader itself, not just the library: a long uploaded
   * filename turning into a real name is exactly the moment the reader is
   * looking at the book, not the shelf. Optimistic: the header updates from
   * the rename response immediately, no separate refetch needed.
   */
  const commitTitleRename = async (next: string) => {
    setRenamingTitle(false);
    const clean = next.trim();
    if (clean === displayTitle) return;
    try {
      const updated = await renamePaper(paperId, clean);
      setMeta(updated);
    } catch (e) {
      alert(`Rename failed: ${(e as Error).message}`);
    }
  };

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

  /**
   * The PDF page to open the raw view at.
   *
   * ⚠ Not simply the current chunk's page_start: that field is null unless
   * the document was extracted through content_list.json (see
   * extraction/pipeline_sync.py), and passing the null straight through is
   * what opened the raw file at page 1 from the middle of a book. Walk back
   * to the nearest chunk that does carry a page; failing that, place the
   * reader proportionally using the GLOBAL sequence (chunks here hold only
   * the loaded window of one chapter, so their index means nothing
   * document-wide, but sequence_order does).
   */
  const currentRawPage = useCallback((): number | null => {
    for (let i = currentChunkIndex; i >= 0; i--) {
      const page = chunks[i]?.page_start;
      if (page != null) return page;
    }
    const seq = chunks[currentChunkIndex]?.sequence_order;
    const pageCount = meta?.page_count ?? 0;
    if (!seq || !totalChunks || pageCount <= 0) return null;
    return Math.min(pageCount, Math.max(1, 1 + Math.floor((seq / totalChunks) * pageCount)));
  }, [chunks, currentChunkIndex, meta?.page_count, totalChunks]);

  /**
   * The chapter after the one being read, by list position rather than by
   * `index + 1`: the picker's own order is what the reader sees, and it
   * includes synthesized entries (front matter, grouped apparatus) whose
   * indices are not guaranteed to be contiguous with the outline's.
   */
  const nextChapter = useMemo(() => {
    if (!activeChapter || chapters.length === 0) return null;
    const at = chapters.findIndex((c) => c.index === activeChapter.index);
    return at >= 0 && at + 1 < chapters.length ? chapters[at + 1] : null;
  }, [chapters, activeChapter]);

  /**
   * Carry straight on into the next chapter.
   *
   * Finishing a chapter used to offer only "Choose another chapter", which
   * sends the reader back to a list to find the entry directly below the one
   * they just finished — the one place they were always going to go. Reading
   * a book front to back was a detour through the table of contents at every
   * chapter boundary.
   */
  const handleNextChapter = useCallback(() => {
    if (nextChapter) void startReading(nextChapter);
  }, [nextChapter, startReading]);

  const handleBackToChapters = useCallback(() => {
    setActiveChapter(null);
    setChunks([]);
    setRevealedUnits([]);
    setAtEnd(false);
    setLastSeq(0);
    initedKeyRef.current = null;
  }, []);

  return (
    <PageMapProvider value={pageMap}>
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg)' }}>

      {/* ── Top bar ── */}
      <header
        className="shrink-0 px-3 sm:px-6 h-13 py-2.5 flex items-center gap-2 sm:gap-4"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <button
          onClick={onBack}
          className="shrink-0 flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[12.5px]"
          style={{ color: 'var(--muted)' }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--fg)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
        >
          <IconBack className="w-3.5 h-3.5" />
          <span>Library</span>
        </button>
        <span className="shrink-0 h-4 w-px" style={{ background: 'var(--border)' }} />
        <div className="flex items-center gap-2 min-w-0">
          <IconDoc className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--muted)' }} />
          {renamingTitle ? (
            <TitleEditor
              value={displayTitle}
              onCommit={commitTitleRename}
              onCancel={() => setRenamingTitle(false)}
              className="reader-title-input"
              placeholder="Name this book…"
            />
          ) : (
            <button
              type="button"
              className="font-serif text-[14.5px] tracking-tight truncate reader-title-btn"
              style={{ color: 'var(--fg)' }}
              onClick={() => setRenamingTitle(true)}
              title="Rename this book"
            >
              {displayTitle}
            </button>
          )}
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
        <div className="ml-auto min-w-0 flex items-center gap-4 overflow-x-auto no-scrollbar hdr-scroll">
          {/* extractor pill: confirms which parser produced these chunks */}
          {meta?.extractor && (
            <ExtractorPill
              extractor={meta.extractor}
              onReextract={async () => {
                if (!(await confirm({
                  title: 'Re-extract this paper with MinerU?',
                  body: 'This will wipe the cached chunks/embeddings and re-run '
                      + 'MinerU from the original PDF. For a large book this can '
                      + 'take a while.',
                  confirmLabel: 'Re-extract',
                  tone: 'danger',
                }))) return;
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
                if (!(await confirm({
                  title: 'Re-chunk this paper from the cached extraction?',
                  body: 'Fast (seconds): re-runs only the chunker (gap-free '
                      + 'sequencing, code/JSON blocks, inline math) on the '
                      + 'already-extracted output. Embeddings regenerate in the '
                      + 'background afterward.',
                  confirmLabel: 'Re-chunk',
                }))) return;
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
          <span className="hidden sm:inline text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            chunk {chunks.length} / {total || '?'}
          </span>
          <div
            className="hidden sm:block w-32 h-[3px] rounded-full overflow-hidden"
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
          {onOpenRaw && (
            <button
              onClick={() => onOpenRaw(currentRawPage())}
              className="shrink-0 text-[11.5px] px-3 py-1.5 rounded-md"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
              title="Open the source PDF at this position"
            >
              Raw file
            </button>
          )}
          {meta && (
            <StrictScopeToggle
              paperId={paperId}
              strictScope={meta.strict_scope ?? true}
              onChange={(next) => setMeta((m) => (m ? { ...m, strict_scope: next } : m))}
            />
          )}
          <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />
          <UserMenuInline />
        </div>
      </header>

      {/* ── Split layout (drag the divider to resize the chat pane) ── */}
      <div ref={splitRef} className="flex-1 min-h-0 flex">

        {/* Left: reading pane */}
        <section
          className="relative flex flex-col min-h-0"
          style={
            narrow
              ? { width: '100%', display: mobilePane === 'read' ? 'flex' : 'none', background: 'var(--bg)' }
              : {
                  width: `calc(100% - ${chatWidthPct}% - 6px)`,
                  borderRight: '1px solid var(--border)',
                  background: 'var(--bg)',
                }
          }
        >
          <div ref={readerRef} className="flex-1 overflow-y-auto thin-scroll">
            <div className="max-w-[680px] mx-auto px-10 pt-16 pb-40">

              {/* paper meta: driven by real backend data */}
              <div className="mb-12">
                <div
                  className="text-[10.5px] font-mono uppercase tracking-[0.12em]"
                  style={{ color: 'var(--muted)' }}
                >
                  {displayPages > 0 ? `${displayPages} pages` : '– pages'}
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
                      paperId={paperId}
                    />
                  ))}
                  {loading && (
                    <div className="text-[13px] text-center py-2" style={{ color: 'var(--muted)' }}>
                      Loading…
                    </div>
                  )}
                  {loadError && (
                    <div className="text-[13px] text-center py-3" style={{ color: 'var(--danger, #b42318)' }}>
                      <div>{loadError}</div>
                      <button
                        onClick={revealNext}
                        className="mt-2 px-2.5 py-1 rounded border"
                        style={{ borderColor: 'var(--border)', color: 'var(--fg)' }}
                      >
                        Retry
                      </button>
                    </div>
                  )}
                </div>
              )}

              {!showChapterPicker && canRead && atEnd && (
                <div className="mt-12 text-[12px] font-mono" style={{ color: 'var(--muted)' }}>
                  {isBook && activeChapter ? (
                    <div className="flex items-center gap-3 flex-wrap">
                      <span>– end of “{activeChapter.title}” –</span>
                      {/* Continuing is the common case, so it leads and is the
                          accented control; the picker stays for jumping
                          somewhere else. Nothing follows the last chapter, so
                          there the picker is all that is offered. */}
                      {nextChapter && (
                        <button
                          onClick={handleNextChapter}
                          className="px-2.5 py-1 rounded-md max-w-[22rem] truncate"
                          title={`Continue into “${nextChapter.title}”`}
                          style={{ color: 'var(--bg)', border: '1px solid var(--accent)', background: 'var(--accent)' }}
                        >
                          Next: {nextChapter.title}
                        </button>
                      )}
                      <button
                        onClick={handleBackToChapters}
                        className="px-2.5 py-1 rounded-md"
                        style={{ color: 'var(--accent)', border: '1px solid var(--border)', background: 'var(--bg)' }}
                      >
                        Choose another chapter
                      </button>
                      {!nextChapter && <span>– end of the book –</span>}
                    </div>
                  ) : (
                    <span>– end of indexed chunks –</span>
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
                        // Reload from the first unit in the selected order;
                        // startReading uses exact sequence IDs in readingOrder
                        // rather than the physical "after" cursor.
                        void startReading(activeChapter, enabled);
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

          {/* keyboard reveal cue: only when there's something to reveal */}
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

        {/* Drag handle: 6px wide, full-height, becomes accent-colored on hover/drag.
            Only meaningful when both panes share the width, so it's dropped
            entirely once one pane already takes the full screen. */}
        {!narrow && (
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
            onTouchStart={() => {
              draggingRef.current = true;
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
        )}

        {/* Right: chat pane */}
        <div
          className="min-h-0 flex flex-col"
          style={
            narrow
              ? { width: '100%', display: mobilePane === 'chat' ? 'flex' : 'none' }
              : { width: `${chatWidthPct}%` }
          }
        >
          <ChatPane
            paperId={paperId}
            // Where the reader actually IS, not how far the loader has run
            // ahead. `chunks` is the prefetch buffer, so using its tail put
            // the local context window past the last revealed paragraph.
            currentSequenceOrder={maxRevealedSeq ?? (chunks.length > 0 ? chunks[chunks.length - 1].sequence_order : null)}
            revealedCount={revealedUnits.length}
            maxSequenceId={maxRevealedSeq}
          />
        </div>
      </div>

      {/* Phone/tablet: the only way in or out of the chat pane once it isn't
          sharing the screen with the reading pane. */}
      {narrow && (
        <button
          type="button"
          className="mobile-pane-toggle"
          onClick={() => setMobilePane((p) => (p === 'read' ? 'chat' : 'read'))}
        >
          {mobilePane === 'read' ? 'Ask AI' : '← Back to reading'}
        </button>
      )}
    </div>
    </PageMapProvider>
  );
}

// ── Chunk renderer for API chunks ─────────────────────────────────────────────
//
// Match ChapterPal's reading pane: no left-margin sequence labels, generous
// serif body, KaTeX inline math, real HTML tables (via remark-gfm + rehype-raw
// since MinerU may emit either pipe-tables or raw <table> HTML), centered
// figures with a tiny filename caption, and a faint heading rule.

function Md({ children }: { children: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE} components={MARKDOWN_COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}

// Inline variant of `Md` for use inside table cells, list items, etc.
// Overrides the default `<p>` wrapper to `<span>` so it doesn't add vertical
// margins that would break table cell alignment. Runs the same remark-math +
// rehype-katex pipeline so $...$ LaTeX renders inside table cells.
function InlineMd({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={MARKDOWN_REMARK}
      rehypePlugins={MARKDOWN_REHYPE}
      components={{ p: ({ children: c }) => <span>{c}</span> }}
    >
      {children}
    </ReactMarkdown>
  );
}

// A formula whose LaTeX transcription KaTeX can't parse (garbled OCR, not
// something safe to auto-repair without risking silently wrong math) falls
// back to the page crop MinerU already captured for every equation, so the
// reader sees the real notation instead of raw TeX source. Checks the
// actual rendered output (a .katex-error span) rather than a separate
// katex.renderToString probe — the app's own `katex` package and
// rehype-katex's private dependency copy are on different versions, so a
// direct import here would bundle a second full copy of the library.
// useLayoutEffect runs before paint, so a formula that does need the image
// fallback is never visible as broken raw text first.
function MathBlock({ wrapped, imageUrl }: { wrapped: string; imageUrl?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [broken, setBroken] = useState(false);

  useLayoutEffect(() => {
    setBroken(!!ref.current?.querySelector('.katex-error'));
  }, [wrapped]);

  if (broken && imageUrl) {
    return (
      <img
        src={imageUrl}
        alt="Equation as it appears on the page (its transcription could not be rendered)"
        className="max-h-[300px] max-w-full object-contain rounded"
        style={{ background: '#fff', padding: 12 }}
      />
    );
  }
  return (
    <div ref={ref}>
      <Md>{wrapped}</Md>
    </div>
  );
}

// ── NEW: Granular reveal renderer (paragraph by paragraph + clean special elements) ──

function GranularUnit({
  unit,
  isLast,
  figureDescriptions = {},
  paperId,
}: {
  unit: RevealedUnit;
  isLast: boolean;
  figureDescriptions?: Record<string, FigureDescription>;
  paperId: string;
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
        <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE} components={MARKDOWN_COMPONENTS}>
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
                      <InlineMd>{h}</InlineMd>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {json.rows.map((row: string[], ri: number) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci} className="px-4 py-2 border-b align-top" style={{ borderColor: 'var(--border)' }}>
                        <InlineMd>{cell}</InlineMd>
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

    // No tableJson for one of two reasons: MinerU found no structure, or the
    // backend deliberately withheld it because the reconciled rows disagreed
    // in width — a scrambled-but-valid table that would otherwise show
    // plausible, wrong numbers (see chunker.py's _table_rows_are_consistent).
    // Either way, prefer the page crop MinerU always makes over guessing at
    // structure — same page-crop-fallback pattern the math unit already uses.
    if (unit.imageUrl) {
      return (
        <div className={`${baseClass} ${lastClass} my-4`}>
          <div className="rounded-lg border p-4 flex justify-center overflow-x-auto" style={{ borderColor: 'var(--border)', background: '#fff' }}>
            <img src={unit.imageUrl} alt="Table, shown as an image" className="max-w-none" />
          </div>
        </div>
      );
    }

    // Fallback to markdown rendering — no structure and no crop available.
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
    // Fall back to the VLM description's image_path when chunk_assets didn't
    // link the extracted image back to this chunk. image_path is stored
    // relative to images/ (e.g. "<doc_id>/<uuid>.png") and served through
    // the authenticated paper-asset endpoint.
    const resolvedImageUrl =
      unit.imageUrl ||
      (richDesc?.image_path
        ? getDocumentAssetUrl(paperId, richDesc.image_path)
        : undefined);

    return (
      <div className={`${baseClass} ${lastClass} my-6`}>
        <div className="flex flex-col items-center rounded-lg border p-4" style={{ borderColor: 'var(--border)' }}>
          {resolvedImageUrl && (
            <img
              src={resolvedImageUrl}
              alt={unit.caption || 'figure'}
              className="max-h-[480px] object-contain rounded"
              style={{ background: '#fff', padding: 12 }}
            />
          )}
          {!resolvedImageUrl && (
            <div
              className="w-full max-w-[420px] py-8 text-center text-[12px] font-mono rounded"
              style={{ background: 'var(--bg-2)', color: 'var(--muted)', border: '1px dashed var(--border)' }}
            >
              Figure image unavailable
            </div>
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
              <ReactMarkdown remarkPlugins={MARKDOWN_REMARK} rehypePlugins={MARKDOWN_REHYPE} components={MARKDOWN_COMPONENTS}>
                {richDesc.description_markdown}
              </ReactMarkdown>
            </div>
          ) : unit.caption ? (
            <div className="mt-2 text-[13.5px] italic text-center max-w-[72ch]" style={{ color: 'var(--fg-2)' }}>
              <InlineMd>{unit.caption}</InlineMd>
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
        <MathBlock wrapped={unit.latex} imageUrl={unit.imageUrl} />
      </div>
    );
  }

  if (unit.kind === 'code') {
    // With a page crop, that IS the listing — see ArticleBlock.tsx's code
    // branch for why showing both at once just prints it twice, the second
    // time worse. Text folded away, still copyable and searchable.
    if (unit.imageUrl) {
      return (
        <div className={`${baseClass} ${lastClass} my-4 code-block`}>
          <img
            src={unit.imageUrl}
            alt="Code listing, shown as it appears on the page"
            className="w-full h-auto rounded-md border"
            style={{ borderColor: 'var(--border)', background: '#fff', cursor: 'zoom-in' }}
          />
          <details className="article-code-text">
            <summary>Extracted text</summary>
            <Md>{unit.markdown}</Md>
          </details>
        </div>
      );
    }

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
          <span className="italic"><InlineMd>{unit.text}</InlineMd></span>
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
  progress: {
    lastChapter: number | null;
    seqByChapter: Record<string, number>;
    finishedChapters: Record<string, true>;
  };
}) {
  if (chapters.length === 0) {
    return (
      <div className="text-[13px]" style={{ color: 'var(--muted)' }}>
        No chapters detected yet. They appear once processing finishes, or read it as a paper.
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
          // "finished" is recorded by the reader when it actually runs out of
          // chapter, never inferred from `saved` — see readingPosition. It
          // outranks "resume": a chapter you read to the end is done, even
          // though a position is still stored for it.
          const finished = progress.finishedChapters[String(ch.index)] === true;
          const started = !finished && typeof saved === 'number' && saved >= ch.start_sequence;
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
              <span
                className="font-serif text-[15px] flex-1 min-w-0 truncate"
                style={{
                  color: 'var(--fg)',
                  // Indent nested outline entries so a book's sub-sections read
                  // as belonging to the chapter above them, not as peers of it.
                  paddingLeft: `${Math.max(0, (ch.level ?? 1) - 1) * 14}px`,
                  opacity: (ch.level ?? 1) > 1 ? 0.8 : 1,
                }}
              >
                {ch.title}
              </span>
              {finished && (
                <span
                  className="text-[10.5px] font-mono px-1.5 py-0.5 rounded"
                  style={{ color: 'var(--ok)', border: '1px solid var(--border)' }}
                >
                  finished
                </span>
              )}
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

export function ExtractorPill({
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
    ? 'Parsed by MinerU: typed equations, footnotes, table structure.'
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
