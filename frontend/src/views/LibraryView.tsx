import { useState, useEffect, useMemo, useRef, type DragEvent } from 'react';
import type { Paper, LibraryLayout, SortKey } from '../types';
import { LogoMark } from '../components/LogoMark';
import {
  IconSearch, IconPlus, IconUpload, IconDoc,
  IconPin, IconSort, IconGrid, IconList, IconPencil, IconTrash,
} from '../components/Icons';
import { PaperCover } from './PaperCover';
import { UserMenuInline } from '../components/UserMenu';
import { TitleEditor } from '../components/TitleEditor';
import { useConfirm } from '../components/ConfirmDialog';
import { displayTitle } from '../lib/titles';
import { stageProgress } from '../lib/progress';
import { listPapers, deletePaper, renamePaper, type PaperMeta } from '../api';

interface Props {
  onOpenPaper: (p: Paper) => void;
  /** Called with the dropped file when the source is a drag-and-drop, and with
   *  nothing when the user clicked (the file is chosen later, in a picker). */
  onUpload: (file?: File) => void;
  onOpenRawFiles: () => void;
  onOpenDesk: () => void;
  layout: LibraryLayout;
  setLayout: (v: LibraryLayout) => void;
}

function deriveProgress(m: PaperMeta): number {
  return stageProgress(m.status, m.job_status, m.job_progress_fraction);
}

function metaToPaper(m: PaperMeta): Paper {
  return {
    id: m.id,
    title: displayTitle(m),
    authors: '',
    venue: '',
    pages: m.page_count || 0,
    added: new Date(m.created_at).toLocaleDateString(),
    progress: deriveProgress(m),
    // expose raw status so cards can show "Processing..." / "Failed" labels
    rawStatus: m.status,
    jobStatus: m.job_status ?? null,
    docKind: m.doc_kind ?? null,
    tags: [],
  };
}

export function LibraryView({ onOpenPaper, onUpload, onOpenRawFiles, onOpenDesk, layout, setLayout }: Props) {
  const confirm = useConfirm();
  const [over, setOver] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('recent');
  // Which doc_kind chips are active. Empty = no filter, show everything —
  // filters are additive constraints, so "none selected" reads as
  // "unconstrained" rather than "show nothing", the more useful default.
  const [kindFilters, setKindFilters] = useState<Set<string>>(new Set());
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  /** The paper whose title is being edited inline, if any. */
  const [renaming, setRenaming] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Fetch papers from backend on mount and keep polling while the view is
  // mounted (so a fresh upload appears without a reload). The poll is
  // adaptive: fast while any paper is still processing (live progress bars),
  // slow once the library is fully settled.
  //
  // ⚠ The poll is paused while a rename is open. It replaces the whole paper
  // list every tick, and a tick landing mid-edit would blow away the input the
  // reader is typing in.
  const renamingRef = useRef<string | null>(null);
  renamingRef.current = renaming;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      let anyProcessing = false;
      if (!renamingRef.current) {
        try {
          const metas = await listPapers();
          if (!alive) return;
          setPapers(metas.map(metaToPaper));
          setLoadError(null);
          anyProcessing = metas.some((m) => m.status !== 'complete' && m.status !== 'failed');
        } catch (e) {
          if (!alive) return;
          setLoadError((e as Error).message || 'Failed to load library');
        } finally {
          if (alive) setLoading(false);
        }
      }
      if (!alive) return;
      timer = setTimeout(tick, anyProcessing ? 2500 : 10000);
    };

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Debounce the search text so each keystroke doesn't re-filter (and
  // re-render) the whole grid on large libraries.
  const [debouncedQuery, setDebouncedQuery] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 200);
    return () => clearTimeout(t);
  }, [query]);

  const filtered = useMemo(() => {
    const q = debouncedQuery.toLowerCase();
    let xs = papers.filter(
      (p) =>
        (p.title.toLowerCase().includes(q) ||
          p.authors.toLowerCase().includes(q)) &&
        (kindFilters.size === 0 || kindFilters.has(p.docKind || 'paper')),
    );
    if (sort === 'title') xs = [...xs].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'pages') xs = [...xs].sort((a, b) => b.pages - a.pages);
    return xs;
  }, [debouncedQuery, sort, kindFilters, papers]);

  const cycleSorts: SortKey[] = ['recent', 'title', 'pages'];

  const KIND_FILTERS: { key: string; label: string }[] = [
    { key: 'book', label: 'Books' },
    { key: 'paper', label: 'Research' },
    { key: 'article', label: 'Articles' },
  ];

  const toggleKindFilter = (key: string) => {
    setKindFilters((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleDelete = async (p: Paper) => {
    const ok = await confirm({
      title: `Delete "${p.title}"?`,
      body:
        'This removes the paper from the library and deletes the raw PDF, ' +
        'extracted images, and MinerU output from disk. It cannot be undone.',
      confirmLabel: 'Delete',
      tone: 'danger',
    });
    if (!ok) return;
    try {
      await deletePaper(p.id);
      setPapers((prev) => prev.filter((x) => x.id !== p.id));
    } catch (e) {
      window.alert(`Delete failed: ${(e as Error).message}`);
    }
  };

  /**
   * Commit a rename optimistically.
   *
   * The card shows the new name immediately and rolls back if the write
   * fails. Renaming is a low-stakes correction the reader will often do
   * several of in a row, and a spinner per keystroke-and-enter would make a
   * two-second job feel like a form submission.
   */
  const commitRename = async (p: Paper, next: string) => {
    setRenaming(null);
    const clean = next.trim();
    if (clean === p.title) return;
    const previous = p.title;
    setPapers((prev) => prev.map((x) => (x.id === p.id ? { ...x, title: clean || previous } : x)));
    try {
      const meta = await renamePaper(p.id, clean);
      setPapers((prev) =>
        prev.map((x) => (x.id === p.id ? { ...x, title: displayTitle(meta) } : x)),
      );
    } catch (e) {
      setPapers((prev) => prev.map((x) => (x.id === p.id ? { ...x, title: previous } : x)));
      setNotice(`Could not rename: ${(e as Error).message}`);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    // Carry the dropped file through to the upload flow. Without this the drop
    // falls back to the click path, which asks the user to find the file again.
    const file = Array.from(e.dataTransfer?.files ?? []).find(
      (f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'),
    );
    onUpload(file);
  };

  const cardProps = (p: Paper) => ({
    paper: p,
    onOpen: () => onOpenPaper(p),
    onDelete: () => handleDelete(p),
    renaming: renaming === p.id,
    onStartRename: () => setRenaming(p.id),
    onCancelRename: () => setRenaming(null),
    onCommitRename: (next: string) => void commitRename(p, next),
  });

  return (
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: 'var(--bg)' }}>

      {/* ── Fixed top bar ── */}
      <header className="shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="max-w-[1240px] mx-auto px-4 sm:px-8 h-14 flex items-center gap-3 sm:gap-6">
          <div className="flex items-center gap-2.5 shrink-0">
            <LogoMark />
            <span className="text-[14px] font-medium tracking-tight" style={{ color: 'var(--fg)' }}>
              9XAIPal
            </span>
          </div>
          {/*
            This group can be wider than a phone screen (paper count, two
            dividers, two labelled buttons, the user menu). min-w-0 lets it
            actually shrink below that content width instead of pushing the
            header wider; overflow-x-auto then makes the overflow a swipe
            instead of a silent clip that leaves buttons unreachable, and
            shrink-0 on every child stops them being individually crushed.
          */}
          <div className="ml-auto min-w-0 flex items-center gap-2 overflow-x-auto no-scrollbar hdr-scroll">
            <span className="hidden sm:inline text-[12px]" style={{ color: 'var(--muted)' }}>
              {papers.length} papers · local
            </span>
            <span className="hidden sm:inline-block mx-2 h-4 w-px" style={{ background: 'var(--border)' }} />
            <button
              onClick={onOpenDesk}
              className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
              title="Ask across your papers without opening them"
            >
              <span style={{ color: 'var(--accent)', fontSize: 11 }}>◈</span>
              Desk
            </button>
            <button
              onClick={onOpenRawFiles}
              className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
            >
              <IconDoc className="w-3.5 h-3.5" style={{ color: 'var(--muted)' }} />
              Raw files
            </button>
            <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />
            <UserMenuInline />
          </div>
        </div>
      </header>

      {/* ── Fixed chrome: hero + dropzone + controls ── */}
      <div className="shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="max-w-[1240px] mx-auto px-4 sm:px-8 pt-6 sm:pt-9 pb-5">

          {/* hero */}
          <div className="flex items-baseline justify-between mb-5 sm:mb-7">
            <div>
              <h1
                className="font-serif text-[28px] sm:text-[38px] leading-[1.05] tracking-[-0.018em]"
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
            onClick={() => onUpload()}
            className={`dropzone${over ? ' is-over' : ''} cursor-pointer rounded-xl px-4 sm:px-7 py-4 sm:py-5 flex items-center gap-3 sm:gap-6`}
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
          <div className="mt-4 flex items-center gap-3 flex-wrap">
            <div className="relative flex-1 min-w-[160px] max-w-[380px]">
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
              {/* Kind filter chips: each toggles independently, so "Books" +
                  "Articles" together (papers hidden) is a valid combination.
                  None active = unconstrained, matching kindFilters' own
                  "empty set = show everything" convention above. */}
              <div className="flex items-center gap-1 mr-1">
                {KIND_FILTERS.map(({ key, label }) => {
                  const active = kindFilters.has(key);
                  return (
                    <button
                      key={key}
                      onClick={() => toggleKindFilter(key)}
                      className="px-2.5 py-1.5 rounded-md text-[12px]"
                      style={{
                        background: active ? 'var(--accent)' : 'var(--bg-2)',
                        color: active ? 'var(--accent-fg)' : 'var(--muted)',
                        border: '1px solid',
                        borderColor: active ? 'var(--accent)' : 'var(--border)',
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
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
        <div className="max-w-[1240px] mx-auto px-8 py-6 pb-10">
          {notice && (
            <div className="lib-notice">
              <span>{notice}</span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
            </div>
          )}

          {loading ? (
            <div className="lib-grid">
              {/* Skeletons in the real card shape. A centred "Loading…" line
                  makes the grid jump into existence; placeholders that are
                  already the right size do not. */}
              {[0, 1, 2].map((i) => (
                <div key={i} className="paper-card is-skeleton" aria-hidden="true">
                  <div className="paper-cover is-blank" />
                  <div className="paper-body">
                    <div className="skeleton-line" style={{ width: '80%' }} />
                    <div className="skeleton-line" style={{ width: '45%' }} />
                  </div>
                </div>
              ))}
            </div>
          ) : loadError ? (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              Could not reach the backend ({loadError}).
            </p>
          ) : layout === 'grid' ? (
            <div className="lib-grid">
              {filtered.map((p) => (
                <PaperCard key={p.id} {...cardProps(p)} />
              ))}
            </div>
          ) : (
            <div className="lib-rows">
              {filtered.map((p) => (
                <PaperRow key={p.id} {...cardProps(p)} />
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

interface CardProps {
  paper: Paper;
  onOpen: () => void;
  onDelete: () => void;
  renaming: boolean;
  onStartRename: () => void;
  onCancelRename: () => void;
  onCommitRename: (next: string) => void;
}

/** The hover-revealed rename / delete pair, shared by both layouts. */
function CardActions({
  onStartRename,
  onDelete,
}: {
  onStartRename: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="paper-actions">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onStartRename(); }}
        title="Rename this paper"
        aria-label="Rename this paper"
      >
        <IconPencil className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        className="is-danger"
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="Delete this paper"
        aria-label="Delete this paper"
      >
        <IconTrash className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// ── PaperCard ─────────────────────────────────────────────────────────────────

function PaperCard({
  paper,
  onOpen,
  onDelete,
  renaming,
  onStartRename,
  onCancelRename,
  onCommitRename,
}: CardProps) {
  const processing = isProcessing(paper);
  return (
    <article className={`paper-card${renaming ? ' is-renaming' : ''}`}>
      {/*
        ⚠ The "open" target is this inner element, not the <article>.
        Rename and delete are real buttons, and nesting a button inside
        something that is itself role="button" is invalid: assistive tech
        announces the card as one control whose name is every label inside it
        ("… 17p · read Rename this paper Delete this paper"). Keeping the
        actions as siblings of the open target leaves three separate,
        correctly-named controls.

        A card being renamed is not an open target at all: a stray click
        inside the editor would otherwise open the reader mid-edit.
      */}
      <div
        className="paper-open"
        onClick={renaming ? undefined : onOpen}
        role={renaming ? undefined : 'button'}
        tabIndex={renaming ? undefined : 0}
        aria-label={renaming ? undefined : `Open ${paper.title}`}
        onKeyDown={(e) => {
          if (renaming) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onOpen();
          }
        }}
      >
        <PaperCover paperId={paper.id} title={paper.title} ready={!processing} showTitle />

        <div className="paper-body">
          <div className="paper-head">
            {renaming ? (
              <TitleEditor value={paper.title} onCommit={onCommitRename} onCancel={onCancelRename} />
            ) : (
              <h3 className="paper-title" title={paper.title}>{paper.title}</h3>
            )}
            {paper.pinned && <IconPin className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--muted)' }} />}
          </div>

          <div className="paper-meta">
            <span>{paper.pages ? `${paper.pages}p` : '–'}</span>
            <span className="paper-dot">·</span>
            <span>{paper.added}</span>
            {paper.tags.map((t) => (
              <span key={t} className="paper-tag">{t}</span>
            ))}
          </div>

          <div className="paper-foot">
            <ProgressBar paper={paper} />
            <span className="paper-status">
              <ProgressLabel paper={paper} />
            </span>
          </div>
        </div>
      </div>

      {!renaming && <CardActions onStartRename={onStartRename} onDelete={onDelete} />}
    </article>
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
      className="flex-1 h-[5px] rounded-full overflow-hidden relative"
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
  renaming,
  onStartRename,
  onCancelRename,
  onCommitRename,
}: CardProps) {
  const processing = isProcessing(paper);
  return (
    <div
      onClick={renaming ? undefined : onOpen}
      className={`paper-row${renaming ? ' is-renaming' : ''}`}
    >
      <PaperCover
        paperId={paper.id}
        title={paper.title}
        ready={!processing}
        className="is-thumb"
      />
      <div className="flex-1 min-w-0">
        {renaming ? (
          <TitleEditor value={paper.title} onCommit={onCommitRename} onCancel={onCancelRename} />
        ) : (
          <div className="paper-row-title" title={paper.title}>{paper.title}</div>
        )}
        <div className="paper-meta">
          <span>{paper.pages ? `${paper.pages}p` : '–'}</span>
          <span className="paper-dot">·</span>
          <span>{paper.added}</span>
          {paper.tags.map((t) => (
            <span key={t} className="paper-tag">{t}</span>
          ))}
        </div>
      </div>
      <div className="w-28 hidden sm:flex items-center gap-2">
        <ProgressBar paper={paper} />
        <span className="text-[10.5px] font-mono tabular-nums w-10 text-right whitespace-nowrap" style={{ color: 'var(--muted)' }}>
          {paper.rawStatus === 'complete'
            ? '✓'
            : paper.rawStatus === 'failed'
            ? <span style={{ color: '#ef4444' }}>!</span>
            : <span style={{ color: 'var(--ok)' }}>{Math.round(paper.progress * 100)}%</span>}
        </span>
      </div>
      {!renaming && <CardActions onStartRename={onStartRename} onDelete={onDelete} />}
    </div>
  );
}
