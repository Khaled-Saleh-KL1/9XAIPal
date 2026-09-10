import { useEffect, useMemo, useRef, useState } from 'react';
import type { Paper } from '../types';
import { downloadExport, type ExportFormat } from '../api';
import { IconSearch, IconCheck } from './Icons';

/**
 * Export, as a four-step panel opened from one "Export" button:
 *
 *   select  → search + kind filter + a checklist of papers, then Next
 *   format  → pick BibTeX / Markdown / Anki / CSV, then Export
 *   running → a progress bar with a red Cancel that aborts the request
 *   done    → a tick, "Exported successfully", and Done back to the library
 *
 * Selection lives HERE, inside the panel, and nowhere else. The previous
 * version put a checkbox on every library card at all times, so exporting
 * was a permanent visual tax on the whole library for an action taken
 * occasionally — and its Export menu did nothing until papers were ticked
 * elsewhere, which read as "broken" rather than "waiting". Everything to do
 * with choosing what to export now appears only after pressing Export, and
 * disappears when the panel closes.
 *
 * Split into ExportWizard (state, effects, the request) and ExportPanel
 * (pure rendering of one step from props) so every step can be rendered
 * and inspected directly — the clickable-citations regression shipped
 * because tsc and a build both passed while the component itself threw the
 * first time it rendered. See docs/plans/library-export.md §5.
 *
 * Reuses the `.confirm-*` dialog classes (components/ConfirmDialog.tsx) so
 * this looks like the app's one modal, not a second design.
 */

export type ExportStep = 'select' | 'format' | 'running' | 'done' | 'error';

const KINDS: { key: string; label: string }[] = [
  { key: 'book', label: 'Books' },
  { key: 'paper', label: 'Research' },
  { key: 'article', label: 'Articles' },
];

export const FORMATS: { key: ExportFormat; label: string; hint: string; file: string }[] = [
  { key: 'bibtex', label: 'BibTeX', hint: 'Citable references for LaTeX and reference managers', file: 'library.bib' },
  { key: 'markdown', label: 'Markdown notes', hint: 'One file per paper, for Obsidian and note apps', file: 'notes.zip' },
  { key: 'anki', label: 'Anki flashcards', hint: 'Your Q&A notes as importable cards', file: 'flashcards.txt' },
  { key: 'csv', label: 'Library CSV', hint: 'A spreadsheet of your papers', file: 'library.csv' },
];

const KIND_LABEL: Record<string, string> = { book: 'Book', paper: 'Paper', article: 'Article' };

/** Same "empty set = unconstrained" convention the library's own kind chips use. */
export function filterPapers(papers: Paper[], query: string, kinds: Set<string>): Paper[] {
  const q = query.trim().toLowerCase();
  return papers.filter(
    (p) =>
      (kinds.size === 0 || kinds.has(p.docKind || 'paper')) &&
      (!q || p.title.toLowerCase().includes(q)),
  );
}

export interface ExportPanelProps {
  step: ExportStep;
  papers: Paper[];
  query: string;
  kinds: Set<string>;
  selected: Set<string>;
  format: ExportFormat | null;
  progress: number;
  error: string | null;
  cancelled: boolean;
  /** What the browser actually saved the file as — set only on the done
   * step, straight from the server's Content-Disposition. Never guessed
   * from the format: one paper's Markdown is `<title>.md`, several is
   * `notes.zip`, and a static label would name the wrong one. */
  savedAs: string | null;
  onQuery: (q: string) => void;
  onToggleKind: (key: string) => void;
  onTogglePaper: (id: string) => void;
  onToggleAllVisible: () => void;
  onFormat: (f: ExportFormat) => void;
  onNext: () => void;
  onBack: () => void;
  onRun: () => void;
  onCancelRun: () => void;
  onRetry: () => void;
  onClose: () => void;
}

/** One step of the wizard, rendered purely from props. */
export function ExportPanel(p: ExportPanelProps) {
  const visible = useMemo(() => filterPapers(p.papers, p.query, p.kinds), [p.papers, p.query, p.kinds]);
  const allVisibleSelected = visible.length > 0 && visible.every((x) => p.selected.has(x.id));
  const chosen = FORMATS.find((f) => f.key === p.format);
  const n = p.selected.size;
  const nLabel = `${n} paper${n === 1 ? '' : 's'}`;

  return (
    <div
      className="confirm-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-title"
      // Backdrop click closes only while nothing is in flight — a stray
      // click must never silently abort a running export.
      onClick={() => { if (p.step !== 'running') p.onClose(); }}
    >
      <div className="confirm-card export-wizard" onClick={(e) => e.stopPropagation()}>

        {p.step === 'select' && (
          <>
            <h2 className="confirm-title" id="export-title">Export</h2>
            <p className="confirm-body">Choose what to export.</p>

            <div className="export-controls">
              <div className="relative flex-1 min-w-[160px]">
                <IconSearch className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5" style={{ color: 'var(--muted)' }} />
                <input
                  autoFocus
                  value={p.query}
                  onChange={(e) => p.onQuery(e.target.value)}
                  placeholder="Search by title…"
                  className="w-full pl-8 pr-3 py-2 rounded-md text-[12.5px]"
                  style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--fg)', outline: 'none' }}
                />
              </div>
              <div className="flex items-center gap-1">
                {KINDS.map(({ key, label }) => {
                  const active = p.kinds.has(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => p.onToggleKind(key)}
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
            </div>

            <div className="export-list-head">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={allVisibleSelected} onChange={p.onToggleAllVisible} disabled={visible.length === 0} />
                {allVisibleSelected ? 'Clear' : 'Select all'}{p.query || p.kinds.size ? ' shown' : ''}
              </label>
              <span>{n} selected</span>
            </div>

            <div className="export-list">
              {visible.length === 0 && (
                <div className="export-empty">{p.papers.length === 0 ? 'Your library is empty.' : 'No papers match.'}</div>
              )}
              {visible.map((x) => (
                <label key={x.id} className={`export-row${p.selected.has(x.id) ? ' is-on' : ''}`}>
                  <input type="checkbox" checked={p.selected.has(x.id)} onChange={() => p.onTogglePaper(x.id)} />
                  <span className="export-row-title">{x.title}</span>
                  <span className="export-row-kind">{KIND_LABEL[x.docKind || 'paper']}</span>
                </label>
              ))}
            </div>

            <div className="confirm-actions">
              <button type="button" className="confirm-cancel" onClick={p.onClose}>Cancel</button>
              <button type="button" className="confirm-go" disabled={n === 0} onClick={p.onNext}>Next</button>
            </div>
          </>
        )}

        {p.step === 'format' && (
          <>
            <h2 className="confirm-title" id="export-title">Export as</h2>
            <p className="confirm-body">
              {nLabel} selected.{p.cancelled && ' The previous export was cancelled.'}
            </p>
            <div className="export-formats">
              {FORMATS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => p.onFormat(f.key)}
                  className={`export-format${p.format === f.key ? ' is-on' : ''}`}
                  aria-pressed={p.format === f.key}
                >
                  <span className="export-format-label">{f.label}</span>
                  <span className="export-format-hint">{f.hint}</span>
                </button>
              ))}
            </div>
            <div className="confirm-actions">
              <button type="button" className="confirm-cancel" onClick={p.onBack}>Back</button>
              <button type="button" className="confirm-go" disabled={!p.format} onClick={p.onRun}>Export</button>
            </div>
          </>
        )}

        {p.step === 'running' && (
          <>
            <h2 className="confirm-title" id="export-title">Exporting…</h2>
            <p className="confirm-body">{chosen?.label} · {nLabel}</p>
            <div className="export-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(p.progress * 100)}>
              <div className="export-bar-fill progress-stripes" style={{ width: `${Math.round(p.progress * 100)}%` }} />
            </div>
            <div className="export-bar-meta">
              <span>{p.progress < 0.7 ? 'Preparing your file…' : 'Downloading…'}</span>
              <span>{Math.round(p.progress * 100)}%</span>
            </div>
            <div className="confirm-actions">
              <button type="button" className="export-cancel-run" onClick={p.onCancelRun}>Cancel</button>
            </div>
          </>
        )}

        {p.step === 'done' && (
          <>
            <div className="export-done">
              <span className="export-done-tick" aria-hidden="true"><IconCheck className="w-6 h-6" /></span>
              <h2 className="confirm-title" id="export-title">Exported successfully</h2>
              <p className="confirm-body">{p.savedAs ?? chosen?.file} has been saved to your downloads.</p>
            </div>
            <div className="confirm-actions">
              <button type="button" className="confirm-go" onClick={p.onClose}>Done</button>
            </div>
          </>
        )}

        {p.step === 'error' && (
          <>
            <h2 className="confirm-title" id="export-title">Export could not be created</h2>
            <p className="confirm-body">{p.error}</p>
            <div className="confirm-actions">
              <button type="button" className="confirm-cancel" onClick={p.onClose}>Close</button>
              <button type="button" className="confirm-go" onClick={p.onRetry}>Try again</button>
            </div>
          </>
        )}

      </div>
    </div>
  );
}

/** The Export button plus the wizard's state and the request it drives. */
export function ExportWizard({ papers }: { papers: Paper[] }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<ExportStep>('select');
  const [query, setQuery] = useState('');
  const [kinds, setKinds] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [format, setFormat] = useState<ExportFormat | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const creepRef = useRef<number | null>(null);

  const stopCreep = () => {
    if (creepRef.current !== null) window.clearInterval(creepRef.current);
    creepRef.current = null;
  };
  useEffect(() => () => { stopCreep(); abortRef.current?.abort(); }, []);

  const reset = () => {
    stopCreep();
    abortRef.current?.abort();
    abortRef.current = null;
    setOpen(false);
    setStep('select');
    setQuery('');
    setKinds(new Set());
    setSelected(new Set());
    setFormat(null);
    setProgress(0);
    setError(null);
    setCancelled(false);
    setSavedAs(null);
  };

  const toggleIn = (setter: (fn: (prev: Set<string>) => Set<string>) => void, key: string) =>
    setter((prev) => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  const toggleAllVisible = () => {
    const visible = filterPapers(papers, query, kinds);
    const all = visible.length > 0 && visible.every((x) => selected.has(x.id));
    setSelected((prev) => {
      const n = new Set(prev);
      visible.forEach((x) => { if (all) n.delete(x.id); else n.add(x.id); });
      return n;
    });
  };

  const run = () => {
    if (!format || selected.size === 0) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setCancelled(false);
    setError(null);
    setProgress(0.04);
    setStep('running');

    // The server builds the whole file before answering, so there is no
    // real progress to show until the response starts streaming; a slow
    // creep up to 70% says "working" without ever claiming to be done. Real
    // byte progress from the download takes over above that.
    stopCreep();
    creepRef.current = window.setInterval(() => setProgress((x) => Math.min(0.7, x + 0.02)), 200);

    downloadExport(format, [...selected], (x) => setProgress((cur) => Math.max(cur, x)), controller.signal)
      .then((filename) => { stopCreep(); setProgress(1); setSavedAs(filename); setStep('done'); })
      .catch((e: unknown) => {
        stopCreep();
        if (e instanceof DOMException && e.name === 'AbortError') {
          // Cancelled by the reader: back to the format step with the
          // selection intact, not to an error screen — nothing went wrong.
          setCancelled(true);
          setStep('format');
          return;
        }
        setError(e instanceof Error ? e.message : 'Export failed');
        setStep('error');
      });
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="text-[12.5px] px-3 py-1.5 rounded-md flex items-center gap-1.5"
        style={{ border: '1px solid var(--border)', color: 'var(--fg)', background: 'var(--bg)' }}
        title="Export papers and notes"
      >
        <span style={{ color: 'var(--accent)', fontSize: 11 }}>⇩</span>
        Export
      </button>

      {open && (
        <ExportPanel
          step={step}
          papers={papers}
          query={query}
          kinds={kinds}
          selected={selected}
          format={format}
          progress={progress}
          error={error}
          cancelled={cancelled}
          savedAs={savedAs}
          onQuery={setQuery}
          onToggleKind={(k) => toggleIn(setKinds, k)}
          onTogglePaper={(id) => toggleIn(setSelected, id)}
          onToggleAllVisible={toggleAllVisible}
          onFormat={setFormat}
          onNext={() => setStep('format')}
          onBack={() => setStep('select')}
          onRun={run}
          onCancelRun={() => abortRef.current?.abort()}
          onRetry={() => { setError(null); setStep('format'); }}
          onClose={reset}
        />
      )}
    </>
  );
}
