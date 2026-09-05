import { useState, useCallback, useRef, useEffect, lazy, Suspense } from 'react';
import type { Route, LibraryLayout, UploadingFile } from './types';
import type { Paper } from './types';
import { LibraryView } from './views/LibraryView';
import { ProcessingOverlay } from './views/ProcessingOverlay';
import { ReadingView } from './views/ReadingView';
import { RawFilesPanel } from './views/RawFilesPanel';
import { DeskView } from './views/DeskView';
import { AuthView } from './views/AuthView';
import { WaitingRoomView } from './views/WaitingRoomView';
import { useAuth } from './contexts/AuthContext';

// react-pdf (pdf.js) is by far the heaviest dependency. Loading it lazily
// keeps it out of the initial bundle so the library/reading views appear
// fast; the viewer chunk is fetched only when a raw PDF is actually opened.
const PdfViewer = lazy(() =>
  import('./views/PdfViewer').then((m) => ({ default: m.PdfViewer })),
);
import { RawArticleViewer } from './views/RawArticleViewer';
import { uploadPaper, importArticleUrl, getPaperProgress, listPapers, getPaper, deletePaper, pageToSequence, type PaperMeta, type DocKind } from './api';
import { IconLink } from './components/Icons';
import { displayTitle } from './lib/titles';
import { stageProgress } from './lib/progress';

function metaToPaper(m: PaperMeta): Paper {
  return {
    id: m.id,
    title: displayTitle(m),
    authors: '',
    venue: '',
    pages: m.page_count || 0,
    added: new Date(m.created_at).toLocaleDateString(),
    progress: stageProgress(m.status, m.job_status, m.job_progress_fraction),
    rawStatus: m.status,
    jobStatus: m.job_status ?? null,
    docKind: m.doc_kind ?? null,
    tags: [],
  };
}

type HashState =
  | { route: 'library' }
  | { route: 'reading'; paperId: string }
  | { route: 'pdf-viewer'; paperId: string }
  | { route: 'desk'; scope: string; page: DeskPage };

/** The desk's two pages. `notes` is the universal board. */
type DeskPage = 'study' | 'notes';

function parseHash(): HashState {
  const h = window.location.hash.replace(/^#\/?/, '');
  const [head, id] = h.split('/');
  if (head === 'paper' && id) return { route: 'reading', paperId: id };
  if (head === 'raw' && id) return { route: 'pdf-viewer', paperId: id };
  // The desk is deep-linkable: #/desk, #/desk/<studyId>, or #/desk/notes for
  // the universal board. `notes` is not a study id, so the two cannot collide.
  if (head === 'desk') {
    if (id === 'notes') return { route: 'desk', scope: 'library', page: 'notes' };
    return { route: 'desk', scope: id || 'library', page: 'study' };
  }
  return { route: 'library' };
}

/**
 * Sync the address bar to the current view.
 *
 * `mode` decides whether this navigation is something the back button can
 * undo. It matters more than it looks: this app has no router, so the
 * history stack is entirely whatever this function puts there. It used to
 * always `replaceState`, which meant the whole session occupied ONE entry —
 * opening a paper overwrote the library instead of stacking on it, so the
 * first Back left the site entirely rather than returning to the library.
 *
 * 'push' therefore for real navigation, and 'replace' for the initial sync
 * on mount (which is not a navigation and must not leave a phantom entry
 * behind the app's first screen).
 */
function writeHash(state: HashState, mode: 'push' | 'replace' = 'push') {
  let next = '#/library';
  if (state.route === 'reading') next = `#/paper/${state.paperId}`;
  else if (state.route === 'pdf-viewer') next = `#/raw/${state.paperId}`;
  else if (state.route === 'desk')
    next = state.page === 'notes' ? '#/desk/notes' : `#/desk/${state.scope}`;
  // Already there: nothing to record. This is also what keeps the back
  // button from fighting itself — when a popstate moves the hash and the
  // view follows, the sync effect below finds the hash already correct and
  // pushes nothing, so Back doesn't immediately re-push where it came from.
  if (window.location.hash === next) return;
  if (mode === 'push') window.history.pushState(null, '', next);
  else window.history.replaceState(null, '', next);
}

export function App() {
  const { user, loading: authLoading, admitted } = useAuth();
  const [route, setRoute] = useState<Route>('library');
  /** False until the hash-sync effect has run once — see that effect. */
  const historyPrimed = useRef(false);
  const [activePaper, setActivePaper] = useState<Paper | null>(null);
  const [activePaperId, setActivePaperId] = useState<string | null>(null);
  const [uploadingFile, setUploadingFile] = useState<UploadingFile | null>(null);
  const [uploadStatus, setUploadStatus] = useState<
    'queued' | 'extracting' | 'chunking' | 'embedding' | 'summarizing' | 'complete' | 'failed'
  >('queued');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadExtractor, setUploadExtractor] = useState<string | null>(null);
  // Real progress within uploadStatus (e.g. pages extracted / total while
  // extracting), null when nothing finer than the status is available.
  const [uploadProgressFraction, setUploadProgressFraction] = useState<number | null>(null);
  // 1-based position among other still-queued jobs (this box's Celery worker
  // runs one job at a time), null once extraction actually starts.
  const [uploadQueuePosition, setUploadQueuePosition] = useState<number | null>(null);
  // `kind` only changes reading navigation (chapters vs linear) and the
  // overlay's completion copy: every document runs the same backend
  // pipeline (see ProcessingOverlay's header comment).
  const [uploadKind, setUploadKind] = useState<DocKind | 'article'>('paper');
  const [layout, setLayout] = useState<LibraryLayout>('grid');
  // When set, the "Book, research paper, or article?" chooser is open:
  // article is a third option inside it, not a separate entry point (see
  // UploadKindModal below).
  const [kindPickerOpen, setKindPickerOpen] = useState(false);
  // A file handed to us by a drag-and-drop. The kind chooser still has to run
  // (a drop can't say whether it's a book or a paper), so the file waits here
  // until a kind is picked, and then skips the native file picker entirely.
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Tracks the document id of the in-flight upload so Cancel can actually
  // delete it on the backend (a ref, because Cancel may fire before the
  // uploadPaper promise resolves and state has been committed).
  const uploadIdRef = useRef<string | null>(null);
  // Which navigation is the current one. Opening a document by id has to
  // fetch its metadata first, and back/forward through the hash can start a
  // second fetch before the first has answered — whereupon whichever
  // response happens to land last wins, regardless of which the reader
  // actually asked for last. The symptom is the hash reading #/paper/B while
  // paper A is on screen. Every path that decides what is being viewed bumps
  // this, and every path that awaits before committing re-checks it.
  const navGenRef = useRef(0);

  // Raw files state
  const [rawPapers, setRawPapers] = useState<PaperMeta[]>([]);
  const [rawFilesOpen, setRawFilesOpen] = useState(false);
  const [viewingPdf, setViewingPdf] = useState<PaperMeta | null>(null);
  /** Page the raw viewer should open on — set only by the structured
   * reader's own "Raw file" button, so the two views land in step. Every
   * other path into pdf-viewer (library, raw files panel, a deep link)
   * leaves this null and opens on page 1 as before. */
  const [pdfInitialPage, setPdfInitialPage] = useState<number | null>(null);
  /** The passage the raw view should open at, and the one the reader should
   * open at, for documents with no pages to sync by (an article). Held here
   * alongside jumpTo for the same reason it is: consumed once by whichever
   * view opens next. */
  const [rawAnchors, setRawAnchors] = useState<string[]>([]);
  const [readingAnchor, setReadingAnchor] = useState<string | null>(null);

  // Which scope the desk opens on: a study id, or 'library'.
  const [deskScope, setDeskScope] = useState<string>('library');
  const [deskPage, setDeskPage] = useState<DeskPage>('study');
  /**
   * A block to scroll to once the reader mounts.
   *
   * ⚠ Held in App, not passed through the route, because it is consumed once.
   * The desk hands over "open P2 at block 41" and the reader must not re-jump
   * there every time it re-renders.
   */
  const [jumpTo, setJumpTo] = useState<number | null>(null);

  // Fetch raw papers list
  const refreshPapers = useCallback(() => {
    listPapers()
      .then((metas) => setRawPapers(metas))
      .catch(() => {});
  }, []);

  // This list only feeds the Raw Files slide-over, so don't hammer the backend
  // with a permanent poll: load once on mount, then refresh (and slow-poll)
  // only while the panel is actually open. LibraryView owns its own polling.
  useEffect(() => {
    refreshPapers();
  }, [refreshPapers]);

  useEffect(() => {
    if (!rawFilesOpen) return;
    refreshPapers();
    const id = setInterval(refreshPapers, 10000);
    return () => clearInterval(id);
  }, [rawFilesOpen, refreshPapers]);

  // Shared by every ingestion pipeline (file upload, URL import, …): poll
  // /progress until a terminal state, without auto-closing the overlay: the
  // user clicks "Back to library" themselves. The library list underneath
  // refreshes on its own poll.
  const pollUploadProgress = useCallback((paperId: string) => {
    pollRef.current = setInterval(async () => {
      try {
        const progress = await getPaperProgress(paperId);
        // Prefer the finer job_status (extracting / chunking / embedding) when available
        const effectiveStatus = (progress.job_status || progress.status) as typeof uploadStatus;
        setUploadStatus(effectiveStatus);
        setUploadProgressFraction(progress.progress_fraction ?? null);
        setUploadQueuePosition(progress.queue_position ?? null);
        if (progress.error_message) {
          setUploadError(progress.error_message);
        }
        if (progress.extractor) {
          setUploadExtractor(progress.extractor);
        }
        if (progress.status === 'complete' || progress.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          // Once complete, the document is a keeper: drop the cancel handle
          // so it can never be deleted by a later Cancel click.
          if (progress.status === 'complete') uploadIdRef.current = null;
          refreshPapers();
        }
      } catch {
        // transient error, keep polling
      }
    }, 1000);
  }, [refreshPapers]);

  // Real file upload handler
  const handleFileUpload = useCallback(async (file: File, kind: DocKind) => {
    setUploadingFile({
      name: file.name,
      size: `${(file.size / (1024 * 1024)).toFixed(1)} MB`,
      pages: 0,
    });
    setUploadStatus('queued');
    setUploadError(null);
    setUploadExtractor(null);
    setUploadKind(kind);
    setRoute('processing');

    try {
      const result = await uploadPaper(file, kind);
      const paperId = result.id;
      uploadIdRef.current = paperId;
      setActivePaperId(paperId);
      pollUploadProgress(paperId);
    } catch (err) {
      console.error('Upload failed:', err);
      setUploadStatus('failed');
      setUploadError((err as Error).message || 'Upload request failed');
    }
  }, [pollUploadProgress]);

  // Web article import handler: the third pipeline, mirroring
  // handleFileUpload exactly (same processing route + progress poll) but
  // calling importArticleUrl instead of uploadPaper. The real page title
  // isn't known until the fetch runs, so the overlay shows the URL's host
  // as a placeholder the same way the backend placeholders
  // original_filename with the URL itself until extraction finishes.
  const handleArticleImport = useCallback(async (url: string, kind: 'book' | 'paper' | null = null) => {
    let host = url;
    try { host = new URL(url).hostname; } catch { /* keep the raw url */ }

    setUploadingFile({ name: url, size: host, pages: 0 });
    setUploadStatus('queued');
    setUploadError(null);
    setUploadExtractor(null);
    // Optimistic: most links pasted through "Book"/"Research paper" do turn
    // out to be the PDF they look like, so the overlay shows that step list
    // rather than the article one. If the link isn't actually a PDF the job
    // still finishes (as an article) — this only affects which steps the
    // overlay narrates while it's in flight.
    setUploadKind(kind ?? 'article');
    setRoute('processing');

    try {
      const result = await importArticleUrl(url, kind);
      const paperId = result.id;
      uploadIdRef.current = paperId;
      setActivePaperId(paperId);
      pollUploadProgress(paperId);
    } catch (err) {
      console.error('Import failed:', err);
      setUploadStatus('failed');
      setUploadError((err as Error).message || 'Import request failed');
    }
  }, [pollUploadProgress]);

  // A URL can now be pasted through any of the three picker choices (Book,
  // Research paper, or the generic Article by URL), closes the modal and
  // kicks off the import with whichever kind was picked (null for the
  // generic one). A pending drag-dropped file is discarded: it doesn't
  // apply to a URL import.
  const submitImportUrl = useCallback((url: string, kind: 'book' | 'paper' | null) => {
    setKindPickerOpen(false);
    setPendingFile(null);
    handleArticleImport(url, kind);
  }, [handleArticleImport]);

  // Step 1 of upload: ask whether this is a book or a research paper. A drop
  // already carries the file, so it comes in as `file` and we hold onto it;
  // the button passes nothing and the file gets picked in step 2.
  const startUpload = useCallback((file?: File) => {
    setPendingFile(file ?? null);
    setKindPickerOpen(true);
  }, []);

  // Step 2: once the kind is chosen, upload the dropped file if we already have
  // one; otherwise open the native file picker.
  const pickFileWithKind = useCallback((kind: DocKind) => {
    setKindPickerOpen(false);

    if (pendingFile) {
      setPendingFile(null);
      handleFileUpload(pendingFile, kind);
      return;
    }

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf';
    input.onchange = (e) => {
      const target = e.target as HTMLInputElement;
      const file = target.files?.[0];
      if (file) handleFileUpload(file, kind);
    };
    input.click();
  }, [handleFileUpload, pendingFile]);

  const openPaper = useCallback((p: Paper) => {
    // Commits immediately, so it is the newest navigation by definition —
    // and bumping here is what stops an already-in-flight openPaperById from
    // landing on top of the paper just clicked.
    navGenRef.current++;
    setActivePaper(p);
    setActivePaperId(p.id);
    setJumpTo(null);
    setRoute('reading');
  }, []);

  /** Open a paper the desk names, optionally at one of its blocks. */
  const openPaperById = useCallback(async (documentId: string, sequenceId?: number) => {
    const gen = ++navGenRef.current;
    try {
      const meta = await getPaper(documentId);
      if (gen !== navGenRef.current) return; // a newer navigation has since won
      setActivePaper(metaToPaper(meta));
      setActivePaperId(meta.id);
      setJumpTo(sequenceId ?? null);
      setRoute('reading');
    } catch {
      // The paper is gone from under the desk; stay put rather than blanking.
    }
  }, []);

  const openDesk = useCallback((scope: string = 'library', page: DeskPage = 'study') => {
    setDeskScope(scope);
    setDeskPage(page);
    setRoute('desk');
  }, []);

  const onProcessingClose = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    uploadIdRef.current = null;
    setUploadingFile(null);
    setUploadError(null);
    refreshPapers();
    setRoute('library');
  }, [refreshPapers]);

  // Cancel actually aborts the upload: stop polling AND delete the document on
  // the backend (rows + on-disk artefacts) so it doesn't keep processing and
  // reappear in the library. Deletion is best-effort: navigation happens
  // regardless so the button always feels responsive.
  const onCancel = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    const id = uploadIdRef.current;
    uploadIdRef.current = null;
    if (id) {
      deletePaper(id)
        .catch(() => {})
        .finally(refreshPapers);
    }
    setUploadingFile(null);
    setUploadError(null);
    setRoute('library');
  }, [refreshPapers]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Restore route from URL hash on mount (e.g. after a browser refresh).
  // Also keep the hash in sync whenever route or active paper changes.
  useEffect(() => {
    const initial = parseHash();
    if (initial.route === 'library') return;
    if (initial.route === 'desk') {
      setDeskScope(initial.scope);
      setDeskPage(initial.page);
      setRoute('desk');
      return;
    }

    (async () => {
      // Guarded like the other two: this fetch runs on mount, and the reader
      // can click through to something else before a slow one answers.
      const gen = ++navGenRef.current;
      try {
        const meta = await getPaper(initial.paperId);
        if (gen !== navGenRef.current) return;
        if (initial.route === 'reading') {
          setActivePaper(metaToPaper(meta));
          setActivePaperId(meta.id);
          setRoute('reading');
        } else if (initial.route === 'pdf-viewer') {
          setPdfInitialPage(null);
          setRawAnchors([]);
          setViewingPdf(meta);
          setRoute('pdf-viewer');
        }
      } catch {
        if (gen !== navGenRef.current) return;
        // Paper no longer exists, so fall back to the library and clear the
        // hash. A replace, not a push: a dead link should not become a step
        // the back button can return to.
        writeHash({ route: 'library' }, 'replace');
      }
    })();
    // Only run on mount; further nav updates the hash via the next effect.
  }, []);

  /**
   * Follow the hash when something outside React changes it: a typed URL, a
   * bookmark, or the back/forward buttons.
   *
   * ⚠ Without this the address bar and the view can disagree: changing only the
   * fragment does not reload the page, so the SPA never learns about it and
   * sits on whatever it was already showing.
   *
   * Both events are bound on purpose. Traversing history between two
   * fragments fires `popstate` and then `hashchange`; a hash edited directly
   * in the address bar fires only `hashchange`. Handling both costs nothing —
   * the handler is idempotent, and the sync effect below writes nothing when
   * the hash already matches.
   */
  useEffect(() => {
    const onNavigate = () => {
      const next = parseHash();
      if (next.route === 'library') { setRoute('library'); return; }
      if (next.route === 'desk') {
        setDeskScope(next.scope);
        setDeskPage(next.page);
        setRoute('desk');
        return;
      }
      if (next.route === 'reading' && next.paperId !== activePaperId) {
        void openPaperById(next.paperId);
        return;
      }
      if (next.route === 'pdf-viewer' && next.paperId !== viewingPdf?.id) {
        // Going back INTO the raw viewer has to restore the paper it was
        // showing; without this the hash says #/raw/<id> while the view
        // stays wherever it was.
        void (async () => {
          const gen = ++navGenRef.current;
          try {
            const meta = await getPaper(next.paperId);
            if (gen !== navGenRef.current) return; // a newer navigation has since won
            setPdfInitialPage(null);
            setRawAnchors([]);
            setViewingPdf(meta);
            setRoute('pdf-viewer');
          } catch {
            if (gen !== navGenRef.current) return;
            setRoute('library');
          }
        })();
      }
    };
    window.addEventListener('hashchange', onNavigate);
    window.addEventListener('popstate', onNavigate);
    return () => {
      window.removeEventListener('hashchange', onNavigate);
      window.removeEventListener('popstate', onNavigate);
    };
  }, [activePaperId, openPaperById, viewingPdf?.id]);

  useEffect(() => {
    // The very first run is the app settling onto its opening screen, not a
    // navigation — replacing keeps a phantom entry from sitting behind it,
    // which would otherwise cost the user one dead Back press before they
    // actually left.
    const mode = historyPrimed.current ? 'push' : 'replace';
    historyPrimed.current = true;

    if (route === 'reading' && activePaperId) {
      writeHash({ route: 'reading', paperId: activePaperId }, mode);
    } else if (route === 'pdf-viewer' && viewingPdf) {
      writeHash({ route: 'pdf-viewer', paperId: viewingPdf.id }, mode);
    } else if (route === 'desk') {
      writeHash({ route: 'desk', scope: deskScope, page: deskPage }, mode);
    } else if (route === 'library') {
      writeHash({ route: 'library' }, mode);
    }
    // 'processing' intentionally leaves the existing hash alone so a refresh
    // mid-upload returns to the library, not a half-baked processing state.
  }, [route, activePaperId, viewingPdf, deskScope, deskPage]);

  // Gated below all hooks (not an early return above them), since every hook in
  // this component must run unconditionally on every render regardless of
  // auth state, or their call order would change between renders.
  if (authLoading) {
    return <div className="h-screen" style={{ background: 'var(--bg)' }} />;
  }
  if (!user) {
    return <AuthView />;
  }
  if (!admitted) {
    return <WaitingRoomView />;
  }

  return (
    <>
      {(route === 'library' || route === 'processing') && (
        <LibraryView
          onOpenPaper={openPaper}
          onUpload={startUpload}
          onOpenRawFiles={() => setRawFilesOpen(true)}
          onOpenDesk={() => openDesk('library')}
          layout={layout}
          setLayout={setLayout}
        />
      )}

      {route === 'reading' && activePaper && (
        <ReadingView
          paper={activePaper}
          paperId={activePaperId || activePaper.id}
          jumpToSequence={jumpTo}
          onJumped={() => setJumpTo(null)}
          onOpenDesk={openDesk}
          onBack={() => setRoute('library')}
          jumpToAnchor={readingAnchor}
          onJumpedAnchor={() => setReadingAnchor(null)}
          onOpenRaw={(meta, page, anchors) => {
            navGenRef.current++; // commits now; outranks any fetch still in flight
            setPdfInitialPage(page);
            setRawAnchors(anchors);
            setViewingPdf(meta);
            setRoute('pdf-viewer');
          }}
        />
      )}

      {route === 'desk' && (
        <DeskView
          initialScope={deskScope}
          page={deskPage}
          onPageChange={setDeskPage}
          onBack={() => setRoute('library')}
          onOpenPaper={openPaperById}
        />
      )}

      {route === 'pdf-viewer' && viewingPdf && (
        viewingPdf.doc_kind === 'article' ? (
          // No react-pdf involved for a raw HTML snapshot, so this one
          // isn't behind the same lazy Suspense boundary as PdfViewer —
          // nothing heavy to defer loading of.
          <RawArticleViewer
            paper={viewingPdf}
            anchors={rawAnchors}
            onBack={() => { setViewingPdf(null); setRoute('library'); }}
            onReadStructured={(p, anchor) => {
              setActivePaper(metaToPaper(p));
              setActivePaperId(p.id);
              setJumpTo(null);
              setReadingAnchor(anchor || null);
              setViewingPdf(null);
              setRoute('reading');
            }}
          />
        ) : (
          <Suspense
            fallback={
              <div className="h-screen flex items-center justify-center text-[13px]" style={{ color: 'var(--muted)' }}>
                Loading PDF viewer…
              </div>
            }
          >
            <PdfViewer
              paper={viewingPdf}
              initialPage={pdfInitialPage}
              onBack={() => { setViewingPdf(null); setRoute('library'); }}
              onReadStructured={(p, page) => {
                setActivePaper(metaToPaper(p));
                setActivePaperId(p.id);
                // Cleared first, same as any other paper-open: a jump left
                // over from earlier (never consumed because its reader
                // unmounted first) must not fire here before the real one
                // below resolves.
                setJumpTo(null);
                setViewingPdf(null);
                setRoute('reading');
                // Resolved against the target paper's own chunks, not the one
                // we're leaving — fire-and-forget is fine here: the reader
                // opens at the top and re-jumps the instant this resolves,
                // same as any other jumpTo (desk, bookmarks).
                pageToSequence(p.id, page)
                  .then((seq) => setJumpTo(seq))
                  .catch(() => {});
              }}
            />
          </Suspense>
        )
      )}

      {route === 'processing' && uploadingFile && (
        <ProcessingOverlay
          file={uploadingFile}
          status={uploadStatus}
          progressFraction={uploadProgressFraction}
          queuePosition={uploadQueuePosition}
          errorMessage={uploadError}
          extractor={uploadExtractor}
          kind={uploadKind}
          onClose={onProcessingClose}
          onCancel={onCancel}
        />
      )}

      {kindPickerOpen && (
        <UploadKindModal
          onChoose={pickFileWithKind}
          onImportUrl={submitImportUrl}
          onCancel={() => { setKindPickerOpen(false); setPendingFile(null); }}
        />
      )}

      {/* Raw Files slide-over panel */}
      <RawFilesPanel
        papers={rawPapers}
        open={rawFilesOpen}
        onClose={() => setRawFilesOpen(false)}
        onOpenPdf={(p) => {
          navGenRef.current++; // commits now; outranks any fetch still in flight
          setRawFilesOpen(false);
          setPdfInitialPage(null);
          setRawAnchors([]);
          setViewingPdf(p);
          setRoute('pdf-viewer');
        }}
      />
    </>
  );
}

// ── Upload kind chooser ─────────────────────────────────────────────────────
// Asks whether the PDF is a book (chapter-by-chapter reading navigation) or a
// research paper (linear reading), then opens the file picker.

function UploadKindModal({
  onChoose,
  onImportUrl,
  onCancel,
}: {
  onChoose: (kind: DocKind) => void;
  onImportUrl: (url: string, kind: 'book' | 'paper' | null) => void;
  onCancel: () => void;
}) {
  // A URL can be pasted for any of the three choices, not just the generic
  // one — picking one swaps this modal's body for a URL field in place,
  // instead of opening anything new. urlKind records which button opened
  // it: 'book'/'paper' only take effect on the backend if the link turns
  // out to be a PDF (a non-PDF link always becomes a plain article, same as
  // pasting it into "Article by URL" directly); null is that generic path.
  const [mode, setMode] = useState<'choose' | 'url'>('choose');
  const [urlKind, setUrlKind] = useState<'book' | 'paper' | null>(null);
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);

  const openUrlMode = (kind: 'book' | 'paper' | null) => {
    setUrlKind(kind);
    // Cleared on every entry, not just the first: the modal only unmounts on
    // Cancel or submit, so without this a rejected link (and its red error)
    // survives ← Back and reappears under the next tile's heading, before the
    // reader has typed anything into it.
    setUrl('');
    setError(null);
    setMode('url');
  };

  useEffect(() => {
    if (mode === 'url') urlInputRef.current?.focus();
  }, [mode]);

  const submitUrl = () => {
    const trimmed = url.trim();
    if (!trimmed) {
      setError('Paste a link first.');
      return;
    }
    if (!/^https?:\/\//i.test(trimmed)) {
      setError('Only http:// and https:// links can be imported.');
      return;
    }
    onImportUrl(trimmed, urlKind);
  };

  const urlCopy = urlKind === 'book'
    ? {
        title: 'Book by URL',
        subtitle: "Paste a link to a PDF: it reads chapter by chapter, just like an uploaded book. A link that isn't a PDF is added as an article instead.",
        placeholder: 'https://example.com/a-book.pdf',
      }
    : urlKind === 'paper'
    ? {
        title: 'Research paper by URL',
        subtitle: "Paste a link to a PDF: it reads front to back, just like an uploaded paper. A link that isn't a PDF is added as an article instead.",
        placeholder: 'https://arxiv.org/pdf/1706.03762',
      }
    : {
        title: 'Article by URL',
        subtitle: 'Paste a link: it reads exactly like a paper, with margin notes, search, and the AI panel.',
        placeholder: 'https://example.com/an-article',
      };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center px-6"
      style={{ background: 'color-mix(in oklch, var(--bg), transparent 8%)', backdropFilter: 'blur(6px)' }}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-[560px] rounded-2xl overflow-hidden"
        style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {mode === 'choose' ? (
          <>
            <div className="px-7 pt-7 pb-2">
              <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
                What are you adding?
              </div>
              <div className="text-[12.5px] mt-1" style={{ color: 'var(--muted)' }}>
                This sets how you read it. You can re-process later if you pick wrong.
              </div>
            </div>
            <div className="px-7 py-5 grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* The card's padding belongs to the buttons, not the wrapper.
                  This whole card used to be one <button>, so every pixel of it
                  picked the kind; holding the padding out here would leave a
                  dead ring around the text that silently does nothing. */}
              <div
                className="rounded-xl transition-colors flex flex-col"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
              >
                <button onClick={() => onChoose('book')} className="text-left w-full flex-1 px-4 pt-4">
                  <div className="font-serif text-[16px]" style={{ color: 'var(--fg)' }}>Book</div>
                  <div className="text-[12px] mt-1 leading-[1.5]" style={{ color: 'var(--muted)' }}>
                    Read chapter by chapter: pick Introduction, Chapter 1, 2, 3… instead of paging the whole book at once.
                  </div>
                </button>
                <button
                  onClick={() => openUrlMode('book')}
                  className="text-[11.5px] mt-2.5 mb-4 mx-4 self-start inline-flex items-center gap-1"
                  style={{ color: 'var(--muted)' }}
                >
                  <IconLink className="w-3 h-3" /> or paste a link
                </button>
              </div>
              <div
                className="rounded-xl transition-colors flex flex-col"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
              >
                <button onClick={() => onChoose('paper')} className="text-left w-full flex-1 px-4 pt-4">
                  <div className="font-serif text-[16px]" style={{ color: 'var(--fg)' }}>Research paper</div>
                  <div className="text-[12px] mt-1 leading-[1.5]" style={{ color: 'var(--muted)' }}>
                    Linear reading, front to back, no chapter navigation. Best for articles and papers.
                  </div>
                </button>
                <button
                  onClick={() => openUrlMode('paper')}
                  className="text-[11.5px] mt-2.5 mb-4 mx-4 self-start inline-flex items-center gap-1"
                  style={{ color: 'var(--muted)' }}
                >
                  <IconLink className="w-3 h-3" /> or paste a link
                </button>
              </div>
              <button
                onClick={() => openUrlMode(null)}
                className="sm:col-span-2 text-left rounded-xl p-4 flex items-center gap-3 transition-colors"
                style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
              >
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center shrink-0"
                  style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
                >
                  <IconLink className="w-3.5 h-3.5" style={{ color: 'var(--fg-2)' }} />
                </div>
                <div className="min-w-0">
                  <div className="font-serif text-[16px]" style={{ color: 'var(--fg)' }}>Article by URL</div>
                  <div className="text-[12px] mt-0.5 leading-[1.5]" style={{ color: 'var(--muted)' }}>
                    Paste a link: reads exactly like a paper, with margin notes, search, and the AI panel.
                  </div>
                </div>
              </button>
            </div>
            <div className="px-7 py-3.5 flex items-center" style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}>
              <button onClick={onCancel} className="ml-auto text-[12px] px-3 py-1.5 rounded-md" style={{ color: 'var(--muted)', border: '1px solid var(--border)', background: 'var(--bg)' }}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="px-7 pt-7 pb-2">
              <button
                onClick={() => { setMode('choose'); setUrlKind(null); }}
                className="text-[12px] mb-2"
                style={{ color: 'var(--muted)' }}
              >
                ← Back
              </button>
              <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
                {urlCopy.title}
              </div>
              <div className="text-[12.5px] mt-1" style={{ color: 'var(--muted)' }}>
                {urlCopy.subtitle}
              </div>
            </div>
            <div className="px-7 py-5">
              <input
                ref={urlInputRef}
                value={url}
                onChange={(e) => { setUrl(e.target.value); setError(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') submitUrl(); }}
                placeholder={urlCopy.placeholder}
                className="w-full px-3 py-2.5 rounded-md text-[13px]"
                style={{
                  background: 'var(--bg-2)',
                  border: `1px solid ${error ? '#ef4444' : 'var(--border)'}`,
                  color: 'var(--fg)',
                  outline: 'none',
                }}
              />
              {error && (
                <div className="text-[12px] mt-2" style={{ color: '#ef4444' }}>{error}</div>
              )}
            </div>
            <div className="px-7 py-3.5 flex items-center gap-3" style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}>
              <button onClick={onCancel} className="text-[12px] px-3 py-1.5 rounded-md" style={{ color: 'var(--muted)', border: '1px solid var(--border)', background: 'var(--bg)' }}>
                Cancel
              </button>
              <button
                onClick={submitUrl}
                className="ml-auto text-[12.5px] px-3 py-1.5 rounded-md"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                Import
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

