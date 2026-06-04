import { useState, useMemo } from 'react';
import type { PaperMeta } from '../api';
import { getRawPdfUrl } from '../api';
import { IconSearch, IconDoc } from '../components/Icons';

interface Props {
  papers: PaperMeta[];
  open: boolean;
  onClose: () => void;
  onOpenPdf: (paper: PaperMeta) => void;
}

export function RawFilesPanel({ papers, open, onClose, onOpenPdf }: Props) {
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    if (!query) return papers;
    const q = query.toLowerCase();
    return papers.filter((p) => p.original_filename.toLowerCase().includes(q));
  }, [papers, query]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      {/* backdrop */}
      <div
        className="absolute inset-0"
        style={{ background: 'rgba(0,0,0,0.2)', backdropFilter: 'blur(2px)' }}
        onClick={onClose}
      />

      {/* panel */}
      <div
        className="relative w-full max-w-[480px] h-full flex flex-col overflow-hidden"
        style={{ background: 'var(--bg)', borderLeft: '1px solid var(--border)' }}
      >
        {/* header */}
        <div
          className="px-6 py-4 flex items-center gap-3 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <IconDoc className="w-4 h-4" style={{ color: 'var(--muted)' }} />
          <span className="font-serif text-[16px] tracking-tight" style={{ color: 'var(--fg)' }}>
            Raw Files
          </span>
          <span
            className="text-[11px] font-mono px-1.5 py-0.5 rounded"
            style={{ color: 'var(--muted)', background: 'var(--bg-2)', border: '1px solid var(--border)' }}
          >
            {papers.length} / {papers.length}
          </span>
          <button
            onClick={onClose}
            className="ml-auto w-7 h-7 rounded flex items-center justify-center text-[16px]"
            style={{ color: 'var(--muted)' }}
            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--fg)')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--muted)')}
          >
            ×
          </button>
        </div>

        {/* search */}
        <div className="px-6 py-3 shrink-0">
          <div className="relative">
            <IconSearch
              className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5"
              style={{ color: 'var(--muted)' }}
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by title, author, or venue…"
              className="w-full pl-8 pr-3 py-2 rounded-md text-[12.5px]"
              style={{
                background: 'var(--bg-2)',
                border: '1px solid var(--border)',
                color: 'var(--fg)',
                outline: 'none',
              }}
            />
          </div>
        </div>

        {/* storage info */}
        <div className="px-6 pb-2 flex items-center justify-between shrink-0">
          <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            Stored at ~/.9xaipal/raw/
          </span>
          <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            {formatTotalSize(papers)}
          </span>
        </div>

        {/* file list */}
        <div className="flex-1 overflow-y-auto thin-scroll px-6 pb-6">
          <div className="space-y-3">
            {filtered.map((p) => (
              <RawFileCard key={p.id} paper={p} onOpen={() => onOpenPdf(p)} />
            ))}
          </div>
        </div>

        {/* footer */}
        <div
          className="px-6 py-3 shrink-0 flex items-center gap-2"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
            ⓘ Files are stored locally and never uploaded.
          </span>
          <span className="ml-auto text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
            <kbd className="kbd">Esc</kbd> to close.
          </span>
        </div>
      </div>
    </div>
  );
}

function RawFileCard({ paper, onOpen }: { paper: PaperMeta; onOpen: () => void }) {
  const progress = paper.status === 'complete' ? 1 : paper.status === 'processing' ? 0.5 : 0;
  const added = new Date(paper.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const sizeMB = paper.file_size_bytes ? (paper.file_size_bytes / (1024 * 1024)).toFixed(1) : '?';

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
    >
      <div className="flex gap-3">
        {/* PDF icon */}
        <div
          className="w-10 h-12 rounded flex items-center justify-center shrink-0"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}
        >
          <span className="text-[9px] font-mono font-bold" style={{ color: '#c0392b' }}>PDF</span>
        </div>

        <div className="flex-1 min-w-0">
          <div
            className="text-[13px] font-medium truncate"
            style={{ color: 'var(--fg)' }}
          >
            {paper.original_filename}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
              {sizeMB} MB
            </span>
            <span className="text-[11px]" style={{ color: 'var(--muted)' }}>·</span>
            <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
              {paper.page_count || '?'}p
            </span>
            <span className="text-[11px]" style={{ color: 'var(--muted)' }}>·</span>
            <span className="text-[11px] font-mono" style={{ color: 'var(--muted)' }}>
              {added}
            </span>
          </div>

          {/* progress bar */}
          <div className="mt-2 flex items-center gap-2">
            <div
              className="flex-1 h-[3px] rounded-full overflow-hidden"
              style={{ background: 'var(--bg)' }}
            >
              <div
                className="h-full rounded-full transition-[width] duration-300"
                style={{
                  width: `${progress * 100}%`,
                  background: progress >= 1 ? 'var(--accent)' : '#e67e22',
                }}
              />
            </div>
            {progress >= 1 && (
              <span className="text-[10px] font-mono" style={{ color: 'var(--accent)' }}>
                indexed
              </span>
            )}
            {progress > 0 && progress < 1 && (
              <span className="text-[10px] font-mono" style={{ color: '#e67e22' }}>
                {Math.round(progress * 100)}%
              </span>
            )}
          </div>

          {/* action buttons */}
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={onOpen}
              className="text-[11.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
            >
              <span>👁</span> Open
            </button>
            <a
              href={getRawPdfUrl(paper.id)}
              download={paper.original_filename}
              className="text-[11.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5 no-underline"
              style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
            >
              <span>↓</span> Download
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

function formatTotalSize(papers: PaperMeta[]): string {
  const totalBytes = papers.reduce((sum, p) => sum + (p.file_size_bytes || 0), 0);
  if (totalBytes > 1024 * 1024 * 1024) {
    return `${(totalBytes / (1024 * 1024 * 1024)).toFixed(1)} GB total`;
  }
  return `${(totalBytes / (1024 * 1024)).toFixed(0)} MB total`;
}
