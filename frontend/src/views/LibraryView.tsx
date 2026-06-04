import { useState, useEffect, useMemo, type DragEvent } from 'react';
import type { Paper, LibraryLayout, SortKey } from '../types';
import { LogoMark } from '../components/LogoMark';
import {
  IconSearch, IconPlus, IconUpload, IconDoc,
  IconPin, IconSort, IconGrid, IconList,
} from '../components/Icons';
import { listPapers, deletePaper, type PaperMeta } from '../api';

interface Props {
  onOpenPaper: (p: Paper) => void;
  onUpload: () => void;
  onOpenRawFiles: () => void;
  layout: LibraryLayout;
  setLayout: (v: LibraryLayout) => void;
}

// Map fine-grained ingestion stage → fraction for the library progress bar.
// We deliberately reserve the first slice for "queued" so a fresh upload
// shows visible motion immediately rather than a flat empty bar.
const STAGE_PROGRESS: Record<string, number> = {
  queued: 0.06,
  extracting: 0.3,
  chunking: 0.55,
  embedding: 0.78,
  summarizing: 0.92,
  complete: 1,
  failed: 0,
};

function deriveProgress(m: PaperMeta): number {
  if (m.status === 'complete') return 1;
  const stage = (m.job_status || m.status || '').toLowerCase();
  return STAGE_PROGRESS[stage] ?? 0.08;
}

function metaToPaper(m: PaperMeta): Paper {
  return {
    id: m.id,
    title: m.original_filename.replace('.pdf', ''),
    authors: '',
    venue: '',
    pages: m.page_count || 0,
    added: new Date(m.created_at).toLocaleDateString(),
    progress: deriveProgress(m),
    // expose raw status so cards can show "Processing..." / "Failed" labels
    rawStatus: m.status,
    jobStatus: m.job_status ?? null,
    tags: [],
  };
}

export function LibraryView({ onOpenPaper, onUpload, onOpenRawFiles, layout, setLayout }: Props) {
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('recent');
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Fetch papers from backend on mount and keep polling while the view is
  // mounted. The poll has to keep running even when nothing is in-flight,
  // otherwise a fresh upload won't appear in the list until the user reloads.
  useEffect(() => {
    let alive = true;

    const refresh = async () => {
      try {
        const metas = await listPapers();
        if (!alive) return;
        setPapers(metas.map(metaToPaper));
        setLoadError(null);
      } catch (e) {
        if (!alive) return;
        setLoadError((e as Error).message || 'Failed to load library');
      } finally {
        if (alive) setLoading(false);
      }
    };

    refresh();
    const interval = setInterval(refresh, 2000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  const filtered = useMemo(() => {
    let xs = papers.filter(
      (p) =>
        p.title.toLowerCase().includes(query.toLowerCase()) ||
        p.authors.toLowerCase().includes(query.toLowerCase()),
    );
    if (sort === 'title') xs = [...xs].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'pages') xs = [...xs].sort((a, b) => b.pages - a.pages);
    return xs;
  }, [query, sort, papers]);

  const cycleSorts: SortKey[] = ['recent', 'title', 'pages'];

  const handleDelete = async (p: Paper) => {
    const ok = window.confirm(
      `Delete "${p.title}"?\n\nThis removes the paper from the library AND deletes the raw PDF, extracted images, and MinerU output from disk. It cannot be undone.`,
    );
    if (!ok) return;
    try {
      await deletePaper(p.id);
      setPapers((prev) => prev.filter((x) => x.id !== p.id));
    } catch (e) {
      window.alert(`Delete failed: ${(e as Error).message}`);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    onUpload();
  };

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg)' }}>

      {/* ── Fixed top bar ── */}
      <header className="shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="max-w-[1240px] mx-auto px-8 h-14 flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <LogoMark />
            <span className="text-[14px] font-medium tracking-tight" style={{ color: 'var(--fg)' }}>
              9XAIPal
            </span>
            <span
              className="text-[11px] font-mono ml-1 px-1.5 py-0.5 rounded"
              style={{ color: 'var(--muted)', background: 'var(--bg-2)', border: '1px solid var(--border)' }}
            >
              local
            </span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[12px]" style={{ color: 'var(--muted)' }}>
              {papers.length} papers · local
            </span>
            <span className="mx-2 h-4 w-px" style={{ background: 'var(--border)' }} />
            <button
              onClick={onOpenRawFiles}
              className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
            >
              <IconDoc className="w-3.5 h-3.5" style={{ color: 'var(--muted)' }} />
              Raw files
            </button>
          </div>
        </div>
      </header>

      {/* ── Fixed chrome: hero + dropzone + controls ── */}
      <div className="shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="max-w-[1240px] mx-auto px-8 pt-9 pb-5">

          {/* hero */}
          <div className="flex items-baseline justify-between mb-7">
            <div>
              <h1
                className="font-serif text-[38px] leading-[1.05] tracking-[-0.018em]"
                style={{ color: 'var(--fg)' }}
              >
                Your library.
              </h1>
              <p className="text-[13.5px] mt-1 max-w-[44ch]" style={{ color: 'var(--muted)' }}>
                Every paper indexed, chunked and embedded on this machine. Nothing leaves.
              </p>
            </div>
            <div className="hidden md:flex items-center gap-1 text-[12px]" style={{ color: 'var(--muted)' }}>
              <kbd className="kbd">⌘</kbd><kbd className="kbd">K</kbd>
              <span className="ml-1">to search</span>
            </div>
          </div>

          {/* dropzone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            onClick={onUpload}
            className={`dropzone${over ? ' is-over' : ''} cursor-pointer rounded-xl px-7 py-5 flex items-center gap-6`}
            style={{ background: over ? undefined : 'var(--bg-2)' }}
          >
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center shrink-0"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
            >
              <IconUpload className="w-4 h-4" style={{ color: 'var(--fg-2)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-serif text-[18px] tracking-tight" style={{ color: 'var(--fg)' }}>
                Drop a PDF to begin.
              </div>
              <div className="text-[12px] mt-0.5" style={{ color: 'var(--muted)' }}>
                Extraction, VLM enhancement, and embedding run entirely on-device.
              </div>
            </div>
            <div className="hidden sm:flex flex-col items-end gap-1.5 shrink-0">
              <div className="text-[10.5px] font-mono" style={{ color: 'var(--muted)' }}>
                PDF · large books OK · stays on this machine
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); onUpload(); }}
                className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                <IconPlus className="w-3.5 h-3.5" /> Add paper
              </button>
            </div>
          </div>

          {/* controls row */}
          <div className="mt-4 flex items-center gap-3">
            <div className="relative flex-1 max-w-[380px]">
              <IconSearch
                className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
                style={{ color: 'var(--muted)' }}
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search title, author, tag…"
                className="w-full pl-8 pr-3 py-2 rounded-md text-[12.5px]"
                style={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--fg)',
                  outline: 'none',
                }}
              />
            </div>
            <div className="flex items-center gap-1 ml-auto">
              <button
                onClick={() => {
                  const idx = cycleSorts.indexOf(sort);
                  setSort(cycleSorts[(idx + 1) % cycleSorts.length]);
                }}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[12px]"
                style={{ color: 'var(--muted)' }}
              >
                <IconSort className="w-3.5 h-3.5" />
                Sort · {sort}
              </button>
              <div
                className="flex items-center rounded-md p-0.5 ml-1"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
              >
                {(['grid', 'list'] as LibraryLayout[]).map((v) => (
                  <button
                    key={v}
                    onClick={() => setLayout(v)}
                    className="p-1.5 rounded"
                    style={{
                      background: layout === v ? 'var(--bg)' : undefined,
                      color: layout === v ? 'var(--fg)' : 'var(--muted)',
                    }}
                  >
                    {v === 'grid' ? <IconGrid className="w-3.5 h-3.5" /> : <IconList className="w-3.5 h-3.5" />}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Scrollable papers ── */}
      <main className="flex-1 min-h-0 overflow-y-auto thin-scroll">
        <div className="max-w-[1240px] mx-auto px-8 py-5 pb-8">
          {loading ? (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              Loading your library…
            </p>
          ) : loadError ? (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              Could not reach the backend ({loadError}).
            </p>
          ) : layout === 'grid' ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map((p) => (
                <PaperCard
                  key={p.id}
                  paper={p}
                  onOpen={() => onOpenPaper(p)}
                  onDelete={() => handleDelete(p)}
                />
              ))}
            </div>
          ) : (
            <div
              className="rounded-xl overflow-hidden"
              style={{ border: '1px solid var(--border)', background: 'var(--bg-2)' }}
            >
              {filtered.map((p) => (
                <PaperRow
                  key={p.id}
                  paper={p}
                  onOpen={() => onOpenPaper(p)}
                  onDelete={() => handleDelete(p)}
                />
              ))}
            </div>
          )}
          {!loading && !loadError && filtered.length === 0 && papers.length === 0 && (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              Your library is empty. Drop a PDF above to add your first paper.
            </p>
          )}
          {!loading && !loadError && filtered.length === 0 && papers.length > 0 && (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              No papers match "{query}".
            </p>
          )}
        </div>
      </main>
    </div>
  );
}

// ── PaperCard ─────────────────────────────────────────────────────────────────

function PaperCard({
  paper,
  onOpen,
  onDelete,
}: {
  paper: Paper;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onOpen}
      className="text-left rounded-xl p-5 w-full group transition-colors cursor-pointer relative"
      style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--border-strong)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
    >
      <div className="flex items-start justify-between">
        <div
          className="w-9 h-11 rounded-sm flex items-center justify-center"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
        >
          <IconDoc className="w-4 h-4" style={{ color: 'var(--muted)' }} />
        </div>
        <div className="flex items-center gap-2">
          {paper.pinned && <IconPin className="w-3.5 h-3.5" style={{ color: 'var(--muted)' }} />}
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            title="Delete paper"
            aria-label="Delete paper"
            className="opacity-0 group-hover:opacity-100 transition-opacity text-[11px] font-mono px-2 py-1 rounded"
            style={{
              color: '#c0392b',
              border: '1px solid var(--border)',
              background: 'var(--bg)',
            }}
          >
            delete
          </button>
        </div>
      </div>
      <div
        className="mt-4 font-serif text-[17px] leading-[1.25] tracking-tight"
        style={{ color: 'var(--fg)' }}
      >
        {paper.title}
      </div>
      <div className="mt-1.5 text-[12px]" style={{ color: 'var(--muted)' }}>
        {paper.authors} · {paper.venue}
      </div>
      <div className="mt-4 flex items-center gap-3 text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
        <span>{paper.pages}p</span>
        <span className="opacity-40">·</span>
        <span>{paper.added}</span>
        <span className="ml-auto flex items-center gap-1 flex-wrap">
          {paper.tags.map((t) => (
            <span
              key={t}
              className="px-1.5 py-0.5 rounded"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
            >
              {t}
            </span>
          ))}
        </span>
      </div>
      <div className="mt-4 h-px w-full" style={{ background: 'var(--border)' }} />
      <div className="mt-3 flex items-center gap-3">
        <ProgressBar paper={paper} />
        <span className="text-[10.5px] font-mono tabular-nums whitespace-nowrap" style={{ color: 'var(--muted)' }}>
          <ProgressLabel paper={paper} />
        </span>
      </div>
    </div>
  );
}

// ── Progress bar / label helpers ──────────────────────────────────────────────
//
// One source of truth for how a paper's processing state is visualised in the
// library. The bar is:
//   - green + animated stripes while extracting / chunking / embedding,
//   - solid green at 100% when complete,
//   - muted grey if the pipeline failed.

function isProcessing(p: Paper): boolean {
  return p.rawStatus !== 'complete' && p.rawStatus !== 'failed';
}

function ProgressBar({ paper }: { paper: Paper }) {
  const processing = isProcessing(paper);
  const failed = paper.rawStatus === 'failed';
  const pct = Math.max(0, Math.min(1, paper.progress)) * 100;

  return (
    <div
      className="flex-1 h-[6px] rounded-full overflow-hidden relative"
      style={{ background: 'var(--bg-3)' }}
      title={
        failed
          ? 'Processing failed'
          : processing
          ? `Processing in background · ${paper.jobStatus || paper.rawStatus || 'working'}`
          : 'Ready to read'
      }
    >
      <div
        className={`h-full transition-[width] duration-300 ease-out${processing ? ' progress-stripes' : ''}`}
        style={{
          width: `${pct}%`,
          // Use backgroundColor (not the shorthand) so .progress-stripes can
          // layer its diagonal gradient on top of the green fill.
          backgroundColor: failed ? 'var(--muted)' : 'var(--ok)',
        }}
      />
    </div>
  );
}

function stageLabel(stage: string | null | undefined): string {
  switch ((stage || '').toLowerCase()) {
    case 'queued': return 'queued';
    case 'extracting': return 'extracting';
    case 'chunking': return 'chunking';
    case 'embedding': return 'embedding';
    case 'summarizing': return 'summaries';
    case 'failed': return 'failed';
    case 'complete': return 'ready';
    default: return stage || '';
  }
}

function ProgressLabel({ paper }: { paper: Paper }) {
  if (paper.rawStatus === 'complete') return <>read</>;
  if (paper.rawStatus === 'failed') return <span style={{ color: '#ef4444' }}>failed</span>;
  const stage = stageLabel(paper.jobStatus || paper.rawStatus);
  const pct = Math.round(paper.progress * 100);
  return (
    <span style={{ color: 'var(--ok)' }}>
      {stage} · {pct}%
    </span>
  );
}

// ── PaperRow ──────────────────────────────────────────────────────────────────

function PaperRow({
  paper,
  onOpen,
  onDelete,
}: {
  paper: Paper;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onOpen}
      className="w-full text-left px-5 py-3.5 flex items-center gap-5 transition-colors cursor-pointer group"
      style={{ borderTop: '1px solid var(--border)' }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-3)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = '')}
    >
      <IconDoc className="w-4 h-4 shrink-0" style={{ color: 'var(--muted)' }} />
      <div className="flex-1 min-w-0">
        <div
          className="font-serif text-[15.5px] leading-tight tracking-tight truncate"
          style={{ color: 'var(--fg)' }}
        >
          {paper.title}
        </div>
        <div className="text-[11.5px] mt-0.5 truncate" style={{ color: 'var(--muted)' }}>
          {paper.authors} · {paper.venue}
        </div>
      </div>
      <div className="hidden md:flex items-center gap-1.5">
        {paper.tags.map((t) => (
          <span
            key={t}
            className="text-[10.5px] font-mono px-1.5 py-0.5 rounded"
            style={{
              color: 'var(--muted)',
              background: 'var(--bg-2)',
              border: '1px solid var(--border)',
            }}
          >
            {t}
          </span>
        ))}
      </div>
      <div className="text-[11px] font-mono tabular-nums w-10 text-right" style={{ color: 'var(--muted)' }}>
        {paper.pages}p
      </div>
      <div className="w-28 flex items-center gap-2">
        <ProgressBar paper={paper} />
        <span className="text-[10.5px] font-mono tabular-nums w-10 text-right whitespace-nowrap" style={{ color: 'var(--muted)' }}>
          {paper.rawStatus === 'complete'
            ? '✓'
            : paper.rawStatus === 'failed'
            ? <span style={{ color: '#ef4444' }}>!</span>
            : <span style={{ color: 'var(--ok)' }}>{Math.round(paper.progress * 100)}%</span>}
        </span>
      </div>
      <div className="text-[10.5px] font-mono w-14 text-right" style={{ color: 'var(--muted)' }}>
        {paper.added}
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="Delete paper"
        aria-label="Delete paper"
        className="opacity-0 group-hover:opacity-100 transition-opacity text-[11px] font-mono px-2 py-1 rounded ml-2"
        style={{
          color: '#c0392b',
          border: '1px solid var(--border)',
          background: 'var(--bg)',
        }}
      >
        delete
      </button>
    </div>
  );
}
