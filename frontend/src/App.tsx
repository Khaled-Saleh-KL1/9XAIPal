import { useState, useCallback, useRef, useEffect, lazy, Suspense } from 'react';
import type { Route, LibraryLayout, UploadingFile } from './types';
import type { Paper } from './types';
import { LibraryView } from './views/LibraryView';
import { ProcessingOverlay } from './views/ProcessingOverlay';
import { ReadingView } from './views/ReadingView';
import { RawFilesPanel } from './views/RawFilesPanel';

// react-pdf (pdf.js) is by far the heaviest dependency. Loading it lazily
// keeps it out of the initial bundle so the library/reading views appear
// fast; the viewer chunk is fetched only when a raw PDF is actually opened.
const PdfViewer = lazy(() =>
  import('./views/PdfViewer').then((m) => ({ default: m.PdfViewer })),
);
import { uploadPaper, getPaperProgress, listPapers, getPaper, deletePaper, type PaperMeta, type DocKind } from './api';

// Mirrors the mapping used inside LibraryView so deep-linked papers carry the
// same status/job_status fields the cards expect.
const STAGE_PROGRESS: Record<string, number> = {
  queued: 0.06,
  extracting: 0.3,
  chunking: 0.55,
  embedding: 0.78,
  summarizing: 0.92,
  complete: 1,
  failed: 0,
};

function metaToPaper(m: PaperMeta): Paper {
  const stage = (m.job_status || m.status || '').toLowerCase();
  return {
    id: m.id,
    title: m.original_filename.replace('.pdf', ''),
    authors: '',
    venue: '',
    pages: m.page_count || 0,
    added: new Date(m.created_at).toLocaleDateString(),
    progress: m.status === 'complete' ? 1 : (STAGE_PROGRESS[stage] ?? 0.08),
    rawStatus: m.status,
    jobStatus: m.job_status ?? null,
    tags: [],
  };
}

type HashState =
  | { route: 'library' }
  | { route: 'reading'; paperId: string }
  | { route: 'pdf-viewer'; paperId: string };

function parseHash(): HashState {
  const h = window.location.hash.replace(/^#\/?/, '');
  const [head, id] = h.split('/');
  if (head === 'paper' && id) return { route: 'reading', paperId: id };
  if (head === 'raw' && id) return { route: 'pdf-viewer', paperId: id };
  return { route: 'library' };
}

function writeHash(state: HashState) {
  let next = '#/library';
  if (state.route === 'reading') next = `#/paper/${state.paperId}`;
  else if (state.route === 'pdf-viewer') next = `#/raw/${state.paperId}`;
  if (window.location.hash !== next) window.history.replaceState(null, '', next);
}

export function App() {
  const [route, setRoute] = useState<Route>('library');
  const [activePaper, setActivePaper] = useState<Paper | null>(null);
  const [activePaperId, setActivePaperId] = useState<string | null>(null);
  const [uploadingFile, setUploadingFile] = useState<UploadingFile | null>(null);
  const [uploadStatus, setUploadStatus] = useState<
    'queued' | 'extracting' | 'chunking' | 'embedding' | 'summarizing' | 'complete' | 'failed'
  >('queued');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadExtractor, setUploadExtractor] = useState<string | null>(null);
  const [layout, setLayout] = useState<LibraryLayout>('grid');
  // When set, the "Book or Research paper?" chooser is open.
  const [kindPickerOpen, setKindPickerOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Tracks the document id of the in-flight upload so Cancel can actually
  // delete it on the backend (a ref, because Cancel may fire before the
  // uploadPaper promise resolves and state has been committed).
  const uploadIdRef = useRef<string | null>(null);

  // Raw files state
  const [rawPapers, setRawPapers] = useState<PaperMeta[]>([]);
  const [rawFilesOpen, setRawFilesOpen] = useState(false);
  const [viewingPdf, setViewingPdf] = useState<PaperMeta | null>(null);

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
    setRoute('processing');

    try {
      const result = await uploadPaper(file, kind);
      const paperId = result.id;
      uploadIdRef.current = paperId;
      setActivePaperId(paperId);

      // Poll the backend for real status. Stop polling once we hit a terminal
      // state, but DON'T auto-close the overlay — let the user click "Back to
      // library". The library list itself refreshes underneath.
      pollRef.current = setInterval(async () => {
        try {
          const progress = await getPaperProgress(paperId);
          // Prefer the finer job_status (extracting / chunking / embedding) when available
          const effectiveStatus = (progress.job_status || progress.status) as typeof uploadStatus;
          setUploadStatus(effectiveStatus);
          if (progress.error_message) {
            setUploadError(progress.error_message);
          }
          if (progress.extractor) {
            setUploadExtractor(progress.extractor);
          }
          if (progress.status === 'complete' || progress.status === 'failed') {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            // Once complete, the document is a keeper — drop the cancel handle
            // so it can never be deleted by a later Cancel click.
            if (progress.status === 'complete') uploadIdRef.current = null;
            refreshPapers();
          }
        } catch {
          // transient error — keep polling
        }
      }, 1000);
    } catch (err) {
      console.error('Upload failed:', err);
      setUploadStatus('failed');
      setUploadError((err as Error).message || 'Upload request failed');
    }
  }, [refreshPapers]);

  // Step 1 of upload: ask whether this is a book or a research paper.
  const startUpload = useCallback(() => {
    setKindPickerOpen(true);
  }, []);

  // Step 2: once the kind is chosen, open the native file picker and upload.
  const pickFileWithKind = useCallback((kind: DocKind) => {
    setKindPickerOpen(false);
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf';
    input.onchange = (e) => {
      const target = e.target as HTMLInputElement;
      const file = target.files?.[0];
      if (file) handleFileUpload(file, kind);
    };
    input.click();
  }, [handleFileUpload]);

  const openPaper = useCallback((p: Paper) => {
    setActivePaper(p);
    setActivePaperId(p.id);
    setRoute('reading');
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
  // reappear in the library. Deletion is best-effort — navigation happens
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

    (async () => {
      try {
        const meta = await getPaper(initial.paperId);
        if (initial.route === 'reading') {
          setActivePaper(metaToPaper(meta));
          setActivePaperId(meta.id);
          setRoute('reading');
        } else if (initial.route === 'pdf-viewer') {
          setViewingPdf(meta);
          setRoute('pdf-viewer');
        }
      } catch {
        // Paper no longer exists — fall back to the library and clear the hash.
        writeHash({ route: 'library' });
      }
    })();
    // Only run on mount; further nav updates the hash via the next effect.
  }, []);

  useEffect(() => {
    if (route === 'reading' && activePaperId) {
      writeHash({ route: 'reading', paperId: activePaperId });
    } else if (route === 'pdf-viewer' && viewingPdf) {
      writeHash({ route: 'pdf-viewer', paperId: viewingPdf.id });
    } else if (route === 'library') {
      writeHash({ route: 'library' });
    }
    // 'processing' intentionally leaves the existing hash alone so a refresh
    // mid-upload returns to the library, not a half-baked processing state.
  }, [route, activePaperId, viewingPdf]);

  return (
    <>
      {(route === 'library' || route === 'processing') && (
        <LibraryView
          onOpenPaper={openPaper}
          onUpload={startUpload}
          onOpenRawFiles={() => setRawFilesOpen(true)}
          layout={layout}
          setLayout={setLayout}
        />
      )}

      {route === 'reading' && activePaper && (
        <ReadingView
          paper={activePaper}
          paperId={activePaperId || activePaper.id}
          onBack={() => setRoute('library')}
        />
      )}

      {route === 'pdf-viewer' && viewingPdf && (
        <Suspense
          fallback={
            <div className="h-screen flex items-center justify-center text-[13px]" style={{ color: 'var(--muted)' }}>
              Loading PDF viewer…
            </div>
          }
        >
          <PdfViewer
            paper={viewingPdf}
            onBack={() => { setViewingPdf(null); setRoute('library'); }}
            onReadStructured={(p) => {
              setActivePaper(metaToPaper(p));
              setActivePaperId(p.id);
              setViewingPdf(null);
              setRoute('reading');
            }}
          />
        </Suspense>
      )}

      {route === 'processing' && uploadingFile && (
        <ProcessingOverlay
          file={uploadingFile}
          status={uploadStatus}
          errorMessage={uploadError}
          extractor={uploadExtractor}
          onClose={onProcessingClose}
          onCancel={onCancel}
        />
      )}

      {kindPickerOpen && (
        <UploadKindModal
          onChoose={pickFileWithKind}
          onCancel={() => setKindPickerOpen(false)}
        />
      )}

      {/* Raw Files slide-over panel */}
      <RawFilesPanel
        papers={rawPapers}
        open={rawFilesOpen}
        onClose={() => setRawFilesOpen(false)}
        onOpenPdf={(p) => {
          setRawFilesOpen(false);
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
  onCancel,
}: {
  onChoose: (kind: DocKind) => void;
  onCancel: () => void;
}) {
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
        <div className="px-7 pt-7 pb-2">
          <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
            What are you adding?
          </div>
          <div className="text-[12.5px] mt-1" style={{ color: 'var(--muted)' }}>
            This sets how you read it. You can re-process later if you pick wrong.
          </div>
        </div>
        <div className="px-7 py-5 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <button
            onClick={() => onChoose('book')}
            className="text-left rounded-xl p-4 transition-colors"
            style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
          >
            <div className="font-serif text-[16px]" style={{ color: 'var(--fg)' }}>Book</div>
            <div className="text-[12px] mt-1 leading-[1.5]" style={{ color: 'var(--muted)' }}>
              Read chapter by chapter — pick Introduction, Chapter 1, 2, 3… instead of paging the whole book at once.
            </div>
          </button>
          <button
            onClick={() => onChoose('paper')}
            className="text-left rounded-xl p-4 transition-colors"
            style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
          >
            <div className="font-serif text-[16px]" style={{ color: 'var(--fg)' }}>Research paper</div>
            <div className="text-[12px] mt-1 leading-[1.5]" style={{ color: 'var(--muted)' }}>
              Linear reading, front to back — no chapter navigation. Best for articles and papers.
            </div>
          </button>
        </div>
        <div className="px-7 py-3.5 flex items-center" style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}>
          <button onClick={onCancel} className="ml-auto text-[12px] px-3 py-1.5 rounded-md" style={{ color: 'var(--muted)', border: '1px solid var(--border)', background: 'var(--bg)' }}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

