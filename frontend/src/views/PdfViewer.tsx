import { useState, useCallback } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import { getStaticPdfUrl } from '../api';
import type { PaperMeta } from '../api';
import { IconBack } from '../components/Icons';

// Set up PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

interface Props {
  paper: PaperMeta;
  onBack: () => void;
  onReadStructured: (paper: PaperMeta) => void;
}

export function PdfViewer({ paper, onBack, onReadStructured }: Props) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.0);
  const [pageInputValue, setPageInputValue] = useState('1');

  const onDocumentLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
  }, []);

  const goToPage = (page: number) => {
    const p = Math.max(1, Math.min(page, numPages));
    setCurrentPage(p);
    setPageInputValue(String(p));
  };

  const handlePageInput = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      const val = parseInt(pageInputValue, 10);
      if (!isNaN(val)) goToPage(val);
    }
  };

  const zoomIn = () => setScale((s) => Math.min(s + 0.25, 3.0));
  const zoomOut = () => setScale((s) => Math.max(s - 0.25, 0.5));

  const pdfUrl = getStaticPdfUrl(paper.id);

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
        <span
          className="font-serif text-[14px] tracking-tight truncate"
          style={{ color: 'var(--fg)' }}
        >
          {paper.original_filename}
        </span>
        {paper.page_count && (
          <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            {paper.page_count}p
          </span>
        )}

        {/* page navigation */}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage <= 1}
            className="w-7 h-7 rounded flex items-center justify-center text-[14px]"
            style={{ color: currentPage <= 1 ? 'var(--bg-3)' : 'var(--muted)', border: '1px solid var(--border)' }}
          >
            ‹
          </button>
          <input
            value={pageInputValue}
            onChange={(e) => setPageInputValue(e.target.value)}
            onKeyDown={handlePageInput}
            onBlur={() => {
              const val = parseInt(pageInputValue, 10);
              if (!isNaN(val)) goToPage(val);
            }}
            className="w-8 text-center text-[12px] font-mono py-1 rounded"
            style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--fg)', outline: 'none' }}
          />
          <span className="text-[12px] font-mono" style={{ color: 'var(--muted)' }}>
            / {numPages || paper.page_count || '?'}
          </span>
          <button
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= numPages}
            className="w-7 h-7 rounded flex items-center justify-center text-[14px]"
            style={{ color: currentPage >= numPages ? 'var(--bg-3)' : 'var(--muted)', border: '1px solid var(--border)' }}
          >
            ›
          </button>

          <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />

          {/* zoom */}
          <button
            onClick={zoomOut}
            className="w-7 h-7 rounded flex items-center justify-center text-[14px]"
            style={{ color: 'var(--muted)', border: '1px solid var(--border)' }}
          >
            −
          </button>
          <span className="text-[11px] font-mono w-10 text-center" style={{ color: 'var(--muted)' }}>
            {Math.round(scale * 100)}%
          </span>
          <button
            onClick={zoomIn}
            className="w-7 h-7 rounded flex items-center justify-center text-[14px]"
            style={{ color: 'var(--muted)', border: '1px solid var(--border)' }}
          >
            +
          </button>

          <span className="mx-1 h-4 w-px" style={{ background: 'var(--border)' }} />

          {/* Read structured */}
          <button
            onClick={() => onReadStructured(paper)}
            className="text-[12.5px] px-3.5 py-1.5 rounded-md flex items-center gap-1.5"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            <span className="text-[11px]">☐</span> Read structured
          </button>
        </div>
      </header>

      {/* ── PDF content ── */}
      <div className="flex-1 overflow-auto flex justify-center" style={{ background: '#f0ede8' }}>
        <div className="py-8">
          <Document
            file={pdfUrl}
            onLoadSuccess={onDocumentLoadSuccess}
            loading={
              <div className="flex items-center justify-center h-96">
                <span className="text-[13px] font-mono" style={{ color: 'var(--muted)' }}>
                  Loading PDF…
                </span>
              </div>
            }
            error={
              <div className="flex items-center justify-center h-96">
                <span className="text-[13px] font-mono" style={{ color: '#c0392b' }}>
                  Failed to load PDF.
                </span>
              </div>
            }
          >
            <Page
              pageNumber={currentPage}
              scale={scale}
              className="shadow-lg"
            />
          </Document>
        </div>
      </div>

      {/* ── Bottom navigation bar ── */}
      <div
        className="shrink-0 px-6 py-2.5 flex items-center justify-center gap-3"
        style={{ borderTop: '1px solid var(--border)' }}
      >
        {/* page dots */}
        <div className="flex items-center gap-1.5">
          {Array.from({ length: Math.min(numPages, 12) }, (_, i) => (
            <button
              key={i}
              onClick={() => goToPage(i + 1)}
              className="w-2 h-2 rounded-full transition-colors"
              style={{
                background: i + 1 === currentPage ? 'var(--accent)' : 'var(--bg-3)',
              }}
            />
          ))}
          {numPages > 12 && (
            <span className="text-[10px] font-mono ml-1" style={{ color: 'var(--muted)' }}>
              +{numPages - 12} more
            </span>
          )}
        </div>
        <span className="mx-2 h-4 w-px" style={{ background: 'var(--border)' }} />
        <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
          ← → navigate · + − zoom
        </span>
      </div>
    </div>
  );
}
