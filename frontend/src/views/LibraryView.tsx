import { useState, useEffect, useMemo, useRef, type DragEvent } from 'react';
import type { Paper, LibraryLayout, SortKey } from '../types';
import { LogoMark } from '../components/LogoMark';
import {
  IconSearch, IconPlus, IconUpload, IconDoc,
  IconPin, IconSort, IconGrid, IconList, IconPencil, IconTrash,
  IconCheck, IconFolder, IconUndo,
} from '../components/Icons';
import { doneFolders } from '../lib/shelves';
import { PaperCover } from './PaperCover';
import { UserMenuInline } from '../components/UserMenu';
import { ExportWizard } from '../components/ExportWizard';
import { TitleEditor } from '../components/TitleEditor';
import { useConfirm } from '../components/ConfirmDialog';
import { displayTitle } from '../lib/titles';
import { stageProgress } from '../lib/progress';
import { listPapers, deletePaper, renamePaper, setPaperDone, renameDoneFolder, searchPapersSemantic, type PaperMeta } from '../api';

interface Props {
  onOpenPaper: (p: Paper) => void;
  /** Called with the dropped file when the source is a drag-and-drop, and with
   *  nothing when the user clicked (the file is chosen later, in a picker). */
  onUpload: (file?: File) => void;
  onOpenRawFiles: () => void;
  onOpenDesk: () => void;
  layout: LibraryLayout;
  setLayout: (v: LibraryLayout) => void;
  /** Incremented by App when an upload is accepted or the overlay closes. */
  refreshToken: number;
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
    doneAt: m.done_at ?? null,
    doneFolder: m.done_folder ?? null,
    tags: [],
  };
}

export function LibraryView({ onOpenPaper, onUpload, onOpenRawFiles, onOpenDesk, layout, setLayout, refreshToken }: Props) {
  const confirm = useConfirm();
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
  // A confirmation ("… is done — filed under X") is read once and should
  // then get out of the way: it clears itself after 3 s. Errors do not — a
  // failed rename or shelf write must stay until the reader dismisses it.
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flash = (text: string) => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
    setNotice(text);
    flashTimer.current = setTimeout(() => {
      setNotice((cur) => (cur === text ? null : cur));
      flashTimer.current = null;
    }, 3000);
  };
  useEffect(() => () => { if (flashTimer.current) clearTimeout(flashTimer.current); }, []);

  // ── The Done shelf ──────────────────────────────────────────────────
  // A finished book does not have to stay in front of the reader, and
  // deleting it is the wrong tool — the point of a library is to keep what
  // was read. "Done Reading" is a second area of the same library: the
  // reading shelf hides what is done, the Done area shows it, optionally
  // sorted into the reader's own folders ("Technical Books"), navigated like
  // a file manager (root → folder → back). Nothing else changes: a done
  // paper opens, searches and joins a Desk study exactly as before. The
  // backend of all this is two columns on the row (PaperMeta.done_at /
  // done_folder); folders are implicit — one exists while a paper names it.
  const [area, setArea] = useState<'reading' | 'done'>('reading');
  /** The folder open inside the Done area; null = its top level. */
  const [doneFolder, setDoneFolder] = useState<string | null>(null);
  /** The paper the shelf panel is open for (mark as done / move). */
  const [shelving, setShelving] = useState<Paper | null>(null);
  /** The Done-area folder whose name is being edited inline. */
  const [folderRenaming, setFolderRenaming] = useState<string | null>(null);

  // Fetch papers from backend on mount and keep polling while the view is
  // mounted (so a fresh upload appears without a reload). The poll is
  // adaptive: fast while any paper is still processing (live progress bars),
  // slow once the library is fully settled.
  //
  // App bumps refreshToken when an upload is accepted or the processing panel
  // closes. Making it an effect dependency restarts the poll immediately,
  // rather than waiting for the old settled-library 10-second timer.
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
  }, [refreshToken]);

  // Debounce the search text so each keystroke doesn't re-filter (and
  // re-render) the whole grid on large libraries.
  const [debouncedQuery, setDebouncedQuery] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 200);
    return () => clearTimeout(t);
  }, [query]);

  // Ids the semantic search found for the current debouncedQuery — unioned
  // into the keyword filter below, not a replacement for it: an exact title
  // hit must never disappear just because the semantic call is slow, failed,
  // or (for a brand-new document) hasn't backfilled an embedding yet.
  const [semanticIds, setSemanticIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    const q = debouncedQuery.trim();
    if (!q) { setSemanticIds(new Set()); return; }
    let alive = true;
    searchPapersSemantic(q)
      .then((ids) => { if (alive) setSemanticIds(new Set(ids)); })
      // Semantic search is a bonus on top of keyword matching, not a
      // requirement — a failed call (offline embedding provider, etc.)
      // should leave keyword search working, not show an error.
      .catch(() => { if (alive) setSemanticIds(new Set()); });
    return () => { alive = false; };
  }, [debouncedQuery]);

  const donePapers = useMemo(() => papers.filter((p) => p.doneAt), [papers]);
  const folders = useMemo(() => doneFolders(donePapers), [donePapers]);
  const searching = debouncedQuery.trim() !== '';

  const filtered = useMemo(() => {
    const q = debouncedQuery.toLowerCase();
    let xs = papers.filter(
      (p) =>
        (p.title.toLowerCase().includes(q) ||
          p.authors.toLowerCase().includes(q) ||
          semanticIds.has(p.id)) &&
        (kindFilters.size === 0 || kindFilters.has(p.docKind || 'paper')),
    );
    // Which area, and where in it. A search inside the Done area looks
    // through every folder at once — "where did I put that?" is the
    // question a search there answers — and the card shows the folder as a
    // tag so the answer is visible; without a search, the area is browsed
    // one level at a time like a file manager.
    if (area === 'reading') xs = xs.filter((p) => !p.doneAt);
    else if (searching) xs = xs.filter((p) => p.doneAt).map((p) => (p.doneFolder ? { ...p, tags: [p.doneFolder] } : p));
    else xs = xs.filter((p) => p.doneAt && (p.doneFolder ?? null) === doneFolder);
    if (sort === 'title') xs = [...xs].sort((a, b) => a.title.localeCompare(b.title));
    if (sort === 'pages') xs = [...xs].sort((a, b) => b.pages - a.pages);
    return xs;
  }, [debouncedQuery, sort, kindFilters, papers, semanticIds, area, doneFolder, searching]);

  /** Folders shown at the top of the Done area's root, with what they hold. */
  const folderCards = useMemo(() => {
    if (area !== 'done' || doneFolder !== null || searching) return [];
    return folders.map((name) => ({
      name,
      count: donePapers.filter(
        (p) => p.doneFolder === name && (kindFilters.size === 0 || kindFilters.has(p.docKind || 'paper')),
      ).length,
    }));
  }, [area, doneFolder, searching, folders, donePapers, kindFilters]);

  // A folder that emptied (its last paper moved out, or deleted) no longer
  // exists; do not leave the reader standing in it.
  useEffect(() => {
    if (doneFolder !== null && !folders.includes(doneFolder)) setDoneFolder(null);
  }, [folders, doneFolder]);

  // Keep the header honest about the whole library, not just the current
  // search/filter result. Older rows without doc_kind are papers by schema
  // default, so they belong in the paper count as well.
  const libraryCounts = useMemo(() => {
    const counts = { books: 0, papers: 0, articles: 0, done: 0 };
    for (const paper of papers) {
      if (paper.docKind === 'book') counts.books += 1;
      else if (paper.docKind === 'article') counts.articles += 1;
      else counts.papers += 1;
      if (paper.doneAt) counts.done += 1;
    }
    return counts;
  }, [papers]);

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

  /**
   * Shelve a paper (done, in `folder` or at the Done root) or bring it back
   * (`done=false`). Optimistic like rename: the card leaves the current view
   * immediately and comes back if the write fails.
   */
  const shelve = async (p: Paper, done: boolean, folder: string | null) => {
    setShelving(null);
    const previous = { doneAt: p.doneAt, doneFolder: p.doneFolder };
    const clean = folder?.trim() || null;
    setPapers((prev) =>
      prev.map((x) =>
        x.id === p.id
          ? { ...x, doneAt: done ? (x.doneAt ?? new Date().toISOString()) : null, doneFolder: done ? clean : null }
          : x,
      ),
    );
    try {
      const meta = await setPaperDone(p.id, done, clean);
      setPapers((prev) =>
        prev.map((x) =>
          x.id === p.id ? { ...x, doneAt: meta.done_at ?? null, doneFolder: meta.done_folder ?? null } : x,
        ),
      );
      if (done) {
        flash(
          clean
            ? `"${p.title}" is done — filed under ${clean}.`
            : `"${p.title}" is done — it is in Done Reading now.`,
        );
      }
    } catch (e) {
      setPapers((prev) => prev.map((x) => (x.id === p.id ? { ...x, ...previous } : x)));
      setNotice(`Could not update the shelf: ${(e as Error).message}`);
    }
  };

  /** Rename a Done-area folder on every paper in it, optimistically. */
  const commitFolderRename = async (from: string, next: string) => {
    setFolderRenaming(null);
    const to = next.trim();
    if (!to || to === from) return;
    if (folders.includes(to)) {
      // Merging two folders by renaming one onto the other is a real
      // outcome, so say so rather than silently doing it.
      setNotice(`There is already a folder called "${to}".`);
      return;
    }
    setPapers((prev) => prev.map((x) => (x.doneFolder === from ? { ...x, doneFolder: to } : x)));
    if (doneFolder === from) setDoneFolder(to);
    try {
      await renameDoneFolder(from, to);
    } catch (e) {
      setPapers((prev) => prev.map((x) => (x.doneFolder === to ? { ...x, doneFolder: from } : x)));
      if (doneFolder === to) setDoneFolder(from);
      setNotice(`Could not rename the folder: ${(e as Error).message}`);
    }
  };

  // ── Drag-and-drop: the whole view is the target ─────────────────────
  // The dashed card is the invitation, but a reader dragging a PDF in from
  // their file manager aims at the page, not at one 80 px strip of it — and
  // a drop that lands anywhere else is not simply ignored by the browser.
  // With no handler claiming it, Chrome and Firefox do what they do with any
  // dropped file: navigate the tab to it. The app is replaced by the
  // browser's PDF viewer, the reader assumes the upload happened, and the
  // library has nothing. So every drag carrying files over this view is
  // claimed, wherever it lands, and an overlay says so while it is over.
  //
  // `dragenter`/`dragleave` fire for every child element crossed, so a plain
  // boolean flickers off and on across the grid; the depth counter goes to
  // zero only when the drag actually leaves the window.
  const [fileOver, setFileOver] = useState(false);
  const dragDepth = useRef(0);
  const carriesFiles = (e: DragEvent) =>
    Array.from(e.dataTransfer?.types ?? []).includes('Files');

  const onDragEnter = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setFileOver(true);
  };
  const onDragOver = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    // preventDefault on dragover is what makes an element a drop target at
    // all; without it the drop event never fires.
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };
  const onDragLeave = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setFileOver(false);
  };
  const onDrop = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    dragDepth.current = 0;
    setFileOver(false);
    const files = Array.from(e.dataTransfer.files);
    const pdfs = files.filter(
      (f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'),
    );
    if (pdfs.length === 0) {
      // Say why nothing happened. Opening the file picker here (the old
      // behaviour) reads as "the drop was lost", not "that file type is not
      // accepted" — and a web page is imported by URL, not by dropping it.
      const names = files.map((f) => f.name).slice(0, 3).join(', ');
      setNotice(
        `${names || 'That'} is not a PDF — only PDF files can be dropped here. ` +
          'For a web page, choose "Add paper" and paste its address.',
      );
      return;
    }
    if (pdfs.length > 1) {
      // The book/paper question is asked per file, so a multi-file drop
      // takes the first and says so rather than silently dropping the rest.
      setNotice(
        `Dropped ${pdfs.length} PDFs — added "${pdfs[0].name}". ` +
          'Drop the others one at a time, so each can be marked as a book or a paper.',
      );
    }
    // Carry the dropped file through to the upload flow. Without this the drop
    // falls back to the click path, which asks the user to find the file again.
    onUpload(pdfs[0]);
  };

  const cardProps = (p: Paper) => ({
    paper: p,
    onOpen: () => onOpenPaper(p),
    onDelete: () => handleDelete(p),
    renaming: renaming === p.id,
    onStartRename: () => setRenaming(p.id),
    onCancelRename: () => setRenaming(null),
    onCommitRename: (next: string) => void commitRename(p, next),
    area,
    onShelve: () => setShelving(p),
    onUnshelve: () => void shelve(p, false, null),
  });

  return (
    <div
      className="h-screen flex flex-col overflow-hidden"
      style={{ background: 'var(--bg)' }}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {fileOver && (
        <div className="lib-drop-overlay" aria-hidden="true">
          <div className="lib-drop-overlay-frame">
            <IconUpload className="w-6 h-6" />
            <div className="font-serif text-[22px] tracking-tight">Drop to add to your library</div>
            <div className="text-[12.5px]" style={{ color: 'var(--muted)' }}>PDF · book or research paper — you choose next</div>
          </div>
        </div>
      )}

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
              {libraryCounts.books} Books · {libraryCounts.papers} Papers · {libraryCounts.articles} Articles
              {libraryCounts.done > 0 && ` · ${libraryCounts.done} done`}
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
            <ExportWizard papers={papers} />
            <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />
            <UserMenuInline />
          </div>
        </div>
      </header>

      {/* ── Fixed chrome: hero + dropzone + controls ── */}
      <div className="shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="max-w-[1240px] mx-auto px-4 sm:px-8 pt-4 sm:pt-6 pb-3">

          {/* hero */}
          <div className="flex items-baseline justify-between mb-3 sm:mb-5">
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
            onClick={() => onUpload()}
            className={`dropzone${fileOver ? ' is-over' : ''} cursor-pointer rounded-xl px-4 sm:px-7 py-3 sm:py-4 flex items-center gap-3 sm:gap-6`}
            style={{ background: fileOver ? undefined : 'var(--bg-2)' }}
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
          <div className="mt-3 flex items-center gap-3 flex-wrap">
            <div className="relative flex-1 min-w-[160px] max-w-[380px]">
              <IconSearch
                className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
                style={{ color: 'var(--muted)' }}
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by title, or what it's about…"
                className="w-full pl-8 pr-3 py-2 rounded-md text-[12.5px]"
                style={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--fg)',
                  outline: 'none',
                }}
              />
            </div>
            <button
              type="button"
              onClick={() => { setArea((a) => (a === 'done' ? 'reading' : 'done')); setDoneFolder(null); }}
              className="lib-done-toggle px-3 py-2 rounded-md text-[12.5px] flex items-center gap-1.5 shrink-0"
              aria-pressed={area === 'done'}
              style={{
                background: area === 'done' ? 'var(--accent)' : 'var(--bg-2)',
                color: area === 'done' ? 'var(--accent-fg)' : 'var(--fg)',
                border: '1px solid',
                borderColor: area === 'done' ? 'var(--accent)' : 'var(--border)',
              }}
              title={area === 'done' ? 'Back to the reading shelf' : 'What you have finished reading'}
            >
              <IconCheck className="w-3.5 h-3.5" />
              Done Reading
              {libraryCounts.done > 0 && (
                <span className="font-mono text-[10.5px] opacity-80">{libraryCounts.done}</span>
              )}
            </button>
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

          {area === 'done' && (
            <nav className="lib-crumbs" aria-label="Where you are in Done Reading">
              <button type="button" onClick={() => setArea('reading')}>Library</button>
              <span className="lib-crumb-sep">›</span>
              {doneFolder === null ? (
                <span className="is-here">Done Reading</span>
              ) : (
                <>
                  <button type="button" onClick={() => setDoneFolder(null)}>Done Reading</button>
                  <span className="lib-crumb-sep">›</span>
                  <span className="is-here"><IconFolder className="w-3.5 h-3.5" /> {doneFolder}</span>
                </>
              )}
            </nav>
          )}

          {folderCards.length > 0 && (
            <div className="lib-folders">
              {folderCards.map((f) => (
                <div key={f.name} className={`lib-folder${folderRenaming === f.name ? ' is-renaming' : ''}`}>
                  <button
                    type="button"
                    className="lib-folder-open"
                    onClick={() => { if (folderRenaming !== f.name) setDoneFolder(f.name); }}
                    aria-label={`Open folder ${f.name}`}
                  >
                    <IconFolder className="w-5 h-5 shrink-0" />
                    {folderRenaming === f.name ? (
                      <TitleEditor
                        value={f.name}
                        onCommit={(next) => void commitFolderRename(f.name, next)}
                        onCancel={() => setFolderRenaming(null)}
                      />
                    ) : (
                      <span className="lib-folder-name" title={f.name}>{f.name}</span>
                    )}
                    <span className="lib-folder-count">{f.count}</span>
                  </button>
                  {folderRenaming !== f.name && (
                    <div className="paper-actions">
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setFolderRenaming(f.name); }}
                        title="Rename this folder"
                        aria-label={`Rename folder ${f.name}`}
                      >
                        <IconPencil className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
              ))}
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
          {!loading && !loadError && filtered.length === 0 && papers.length > 0 && searching && (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              No papers match "{query}"{area === 'done' ? ' in Done Reading' : ''}.
            </p>
          )}
          {!loading && !loadError && filtered.length === 0 && papers.length > 0 && !searching && area === 'reading' && (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              {kindFilters.size > 0
                ? 'Nothing of that kind on the reading shelf.'
                : 'Everything here is done — it is all in Done Reading.'}
            </p>
          )}
          {!loading && !loadError && filtered.length === 0 && folderCards.length === 0 && !searching && area === 'done' && (
            <p className="text-center text-[13px] py-16" style={{ color: 'var(--muted)' }}>
              {doneFolder !== null
                ? 'This folder is empty.'
                : donePapers.length === 0
                ? 'Nothing finished yet. When you are done with a paper, hover it and press ✓ — it moves here, out of the way but never gone.'
                : 'Nothing of that kind in Done Reading.'}
            </p>
          )}

          {shelving && (
            <ShelfPanel
              paper={shelving}
              folders={folders}
              onChoose={(folder) => void shelve(shelving, true, folder)}
              onUnshelve={() => void shelve(shelving, false, null)}
              onClose={() => setShelving(null)}
            />
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
  /** Which area the card is shown in — decides which shelf action it offers. */
  area: 'reading' | 'done';
  /** Open the shelf panel: "mark as done" on the reading shelf, "move" in Done. */
  onShelve: () => void;
  /** Done area only: straight back to the reading shelf, no panel. */
  onUnshelve: () => void;
}

/** The hover-revealed rename / shelf / delete set, shared by both layouts. */
function CardActions({
  area,
  onStartRename,
  onShelve,
  onUnshelve,
  onDelete,
}: {
  area: 'reading' | 'done';
  onStartRename: () => void;
  onShelve: () => void;
  onUnshelve: () => void;
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
      {area === 'reading' ? (
        <button
          type="button"
          className="is-done"
          onClick={(e) => { e.stopPropagation(); onShelve(); }}
          title="Done reading — move it to Done Reading"
          aria-label="Mark as done reading"
        >
          <IconCheck className="w-3.5 h-3.5" />
        </button>
      ) : (
        <>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onShelve(); }}
            title="Move to a folder"
            aria-label="Move to a folder"
          >
            <IconFolder className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onUnshelve(); }}
            title="Back to the reading shelf"
            aria-label="Back to the reading shelf"
          >
            <IconUndo className="w-3.5 h-3.5" />
          </button>
        </>
      )}
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
  area,
  onShelve,
  onUnshelve,
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

      {!renaming && <CardActions area={area} onStartRename={onStartRename} onShelve={onShelve} onUnshelve={onUnshelve} onDelete={onDelete} />}
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
  area,
  onShelve,
  onUnshelve,
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
      {!renaming && <CardActions area={area} onStartRename={onStartRename} onShelve={onShelve} onUnshelve={onUnshelve} onDelete={onDelete} />}
    </div>
  );
}

// ── ShelfPanel ────────────────────────────────────────────────────────────────
//
// "Where does this go?" — opened by ✓ on the reading shelf (mark as done) and
// by the folder button in the Done area (move). One panel for both: the
// choice is the same either way — the top of Done Reading, one of the
// existing folders, or a new one typed here. Built on the confirm dialog's
// classes so it reads as the app's one modal.

function ShelfPanel({
  paper,
  folders,
  onChoose,
  onUnshelve,
  onClose,
}: {
  paper: Paper;
  folders: string[];
  /** null = the top of Done Reading, otherwise the folder name. */
  onChoose: (folder: string | null) => void;
  onUnshelve: () => void;
  onClose: () => void;
}) {
  const [newName, setNewName] = useState('');
  const moving = Boolean(paper.doneAt);
  const clean = newName.trim();
  const exists = folders.some((f) => f.toLowerCase() === clean.toLowerCase());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="confirm-backdrop" role="dialog" aria-modal="true" aria-labelledby="shelf-title" onClick={onClose}>
      <div className="confirm-card shelf-panel" onClick={(e) => e.stopPropagation()}>
        <h2 className="confirm-title" id="shelf-title">
          {moving ? 'Move' : 'Done reading'}
        </h2>
        <p className="confirm-body">
          <span className="shelf-panel-paper">{paper.title}</span>
          {moving
            ? ' — where should it go?'
            : ' — it leaves the reading shelf but stays in your library, your notes and the Desk. Where should it go?'}
        </p>

        <div className="shelf-options">
          <button
            type="button"
            className={`shelf-option${moving && !paper.doneFolder ? ' is-current' : ''}`}
            onClick={() => onChoose(null)}
          >
            <IconCheck className="w-4 h-4" />
            <span>Done Reading</span>
            <span className="shelf-option-hint">no folder</span>
          </button>
          {folders.map((f) => (
            <button
              key={f}
              type="button"
              className={`shelf-option${paper.doneFolder === f ? ' is-current' : ''}`}
              onClick={() => onChoose(f)}
            >
              <IconFolder className="w-4 h-4" />
              <span>{f}</span>
              {paper.doneFolder === f && <span className="shelf-option-hint">here now</span>}
            </button>
          ))}
        </div>

        <form
          className="shelf-new"
          onSubmit={(e) => { e.preventDefault(); if (clean && !exists) onChoose(clean); }}
        >
          <IconFolder className="w-4 h-4 shrink-0" style={{ color: 'var(--muted)' }} />
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New folder, e.g. Technical Books"
            maxLength={80}
            aria-label="New folder name"
            autoFocus
          />
          <button type="submit" className="confirm-go" disabled={!clean || exists}>
            {exists ? 'Exists' : 'Create & move'}
          </button>
        </form>

        <div className="confirm-actions">
          {moving && (
            <button type="button" className="confirm-cancel shelf-unshelve" onClick={onUnshelve}>
              <IconUndo className="w-3.5 h-3.5" /> Back to reading
            </button>
          )}
          <button type="button" className="confirm-cancel" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
