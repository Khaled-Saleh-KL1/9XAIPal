import { useEffect, useRef, useState } from 'react';
import { downloadExport, type ExportFormat } from '../api';

interface Props {
  selectedPaperIds: string[];
  onSelectAll: () => void;
}

const ITEMS: { format: ExportFormat; label: string; hint: string }[] = [
  { format: 'bibtex', label: 'BibTeX', hint: 'Citable references (.bib)' },
  { format: 'markdown', label: 'Markdown notes', hint: 'Notes for Obsidian (.zip)' },
  { format: 'anki', label: 'Anki flashcards', hint: 'Q&A notes (.txt)' },
  { format: 'csv', label: 'Library CSV', hint: 'Spreadsheet of your papers' },
];

const LABELS: Record<ExportFormat, string> = {
  bibtex: 'BibTeX',
  markdown: 'Markdown notes',
  anki: 'Anki flashcards',
  csv: 'Library CSV',
};

interface ExportState {
  format: ExportFormat;
  count: number;
  progress: number;
  error: string | null;
}

/** Export controls for the checked papers in the library. */
export function ExportMenu({ selectedPaperIds, onSelectAll }: Props) {
  const [open, setOpen] = useState(false);
  const [exporting, setExporting] = useState<ExportState | null>(null);
  const timerRef = useRef<number | null>(null);
  const count = selectedPaperIds.length;

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, []);

  const startExport = (format: ExportFormat) => {
    if (!count) return;
    if (timerRef.current !== null) window.clearInterval(timerRef.current);

    setOpen(false);
    setExporting({ format, count, progress: 0.06, error: null });
    timerRef.current = window.setInterval(() => {
      setExporting((current) =>
        current && !current.error
          ? { ...current, progress: Math.min(0.88, current.progress + 0.035) }
          : current,
      );
    }, 220);

    downloadExport(format, selectedPaperIds, (progress) => {
      setExporting((current) => current ? { ...current, progress: Math.max(current.progress, progress) } : current);
    })
      .then(() => {
        if (timerRef.current !== null) window.clearInterval(timerRef.current);
        setExporting((current) => current ? { ...current, progress: 1 } : current);
        window.setTimeout(() => setExporting(null), 450);
      })
      .catch((error: unknown) => {
        if (timerRef.current !== null) window.clearInterval(timerRef.current);
        setExporting((current) => current ? { ...current, error: error instanceof Error ? error.message : 'Export failed' } : current);
      });
  };

  return (
    <>
      <div className="relative">
        <button
          onClick={() => setOpen((value) => !value)}
          className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
          style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
          title="Export selected papers"
        >
          <span style={{ color: 'var(--accent)', fontSize: 11 }}>⇩</span>
          Export{count ? ` · ${count}` : ''}
        </button>
        {open && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
            <div
              className="mt-1 rounded-lg overflow-hidden absolute right-0 z-30"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 8px 24px -8px rgba(0,0,0,0.18)', minWidth: 230 }}
            >
              <div className="px-4 pt-3 pb-2 text-[11px]" style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                {count ? `${count} selected paper${count === 1 ? '' : 's'}` : 'Select papers before exporting'}
              </div>
              {!count && (
                <button
                  onClick={() => { setOpen(false); onSelectAll(); }}
                  className="text-[12.5px] px-4 py-2.5 w-full text-left"
                  style={{ color: 'var(--accent)' }}
                >
                  Select all papers
                </button>
              )}
              {ITEMS.map((item) => (
                <button
                  key={item.format}
                  onClick={() => startExport(item.format)}
                  disabled={!count}
                  className="text-[12.5px] px-4 py-2.5 whitespace-nowrap w-full text-left disabled:opacity-40"
                  style={{ color: 'var(--fg)' }}
                >
                  <div>{item.label}</div>
                  <div className="text-[10.5px]" style={{ color: 'var(--faint)' }}>{item.hint}</div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {exporting && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-5" style={{ background: 'rgba(0,0,0,0.28)' }} role="dialog" aria-modal="true" aria-live="polite">
          <div className="w-full max-w-[430px] rounded-xl p-6" style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 18px 60px -24px rgba(0,0,0,0.5)' }}>
            <div className="font-serif text-[22px] tracking-tight" style={{ color: 'var(--fg)' }}>
              {exporting.error ? 'Export could not be created' : exporting.progress >= 1 ? 'Export ready' : 'Preparing your export'}
            </div>
            <p className="text-[12.5px] mt-1.5" style={{ color: 'var(--muted)' }}>
              {exporting.error || `${LABELS[exporting.format]} · ${exporting.count} paper${exporting.count === 1 ? '' : 's'}`}
            </p>
            <div className="mt-5 h-2 rounded-full overflow-hidden" style={{ background: 'var(--bg-3)' }}>
              <div className="h-full progress-stripes transition-[width] duration-200" style={{ width: `${Math.round(exporting.progress * 100)}%`, backgroundColor: exporting.error ? '#c0392b' : 'var(--accent)' }} />
            </div>
            <div className="mt-2 flex justify-between text-[10.5px] font-mono" style={{ color: 'var(--muted)' }}>
              <span>{exporting.error ? 'Please try again' : exporting.progress >= 1 ? 'Downloading…' : 'Extracting selected data…'}</span>
              <span>{Math.round(exporting.progress * 100)}%</span>
            </div>
            {exporting.error && (
              <button
                onClick={() => setExporting(null)}
                className="mt-5 px-3 py-1.5 rounded-md text-[12px]"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
              >
                Close
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
