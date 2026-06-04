import type { UploadingFile, StepState } from '../types';
import { IconDoc, IconCheck } from '../components/Icons';

/**
 * Backend-driven processing overlay. The visible step states are derived
 * directly from the document's real status (queued → extracting → chunking
 * → embedding → complete | failed). No fake timer, no fake counters.
 */

type BackendStatus =
  | 'queued'
  | 'extracting'
  | 'chunking'
  | 'embedding'
  | 'summarizing'
  | 'complete'
  | 'failed';

interface StepDef {
  id: number;
  title: string;
  sub: string;
  matches: BackendStatus[]; // statuses for which this step is "active"
}

const STEPS: StepDef[] = [
  {
    id: 1,
    title: 'Extracting structure',
    sub: 'MinerU is parsing layout, math, and figures',
    matches: ['queued', 'extracting'],
  },
  {
    id: 2,
    title: 'Chunking',
    sub: 'Splitting the document into structural units',
    matches: ['chunking'],
  },
  {
    id: 3,
    title: 'Embedding',
    sub: 'Building the local vector index for /ask',
    matches: ['embedding'],
  },
  {
    id: 4,
    title: 'Summaries & figures',
    sub: 'Section summaries + VLM figure descriptions (last phase)',
    matches: ['summarizing'],
  },
];

const STEP_ORDER: BackendStatus[] = ['queued', 'extracting', 'chunking', 'embedding', 'summarizing'];

function stateFor(step: StepDef, status: BackendStatus): StepState {
  if (status === 'complete') return 'done';
  if (status === 'failed') {
    // The step that was active when we failed shows as error.
    // Previous steps are considered done.
    const stepIdx = Math.max(...step.matches.map((s) => STEP_ORDER.indexOf(s)));
    // When failed we treat the highest step that was still "in play" as the error one.
    return stepIdx >= 0 ? 'error' : 'pending';
  }
  if (step.matches.includes(status)) return 'active';
  // Step is done if its statuses come before the current one.
  const stepIdx = Math.max(...step.matches.map((s) => STEP_ORDER.indexOf(s)));
  const curIdx = STEP_ORDER.indexOf(status);
  return curIdx > stepIdx ? 'done' : 'pending';
}

interface Props {
  file: UploadingFile;
  status: BackendStatus;
  errorMessage?: string | null;
  extractor?: string | null;   // "mineru" | "pymupdf_fallback" | null while pending
  onClose: () => void;
  onCancel: () => void;
}

function extractorLabel(ex: string | null | undefined): { label: string; tone: 'good' | 'warn' | 'pending' } {
  if (ex === 'mineru') return { label: 'MinerU (full layout + math + footnotes)', tone: 'good' };
  if (ex === 'pymupdf_fallback') return { label: 'PyMuPDF fallback (degraded — no math LaTeX, no table structure)', tone: 'warn' };
  return { label: 'Choosing extractor…', tone: 'pending' };
}

export function ProcessingOverlay({ file, status, errorMessage, extractor, onClose, onCancel }: Props) {
  const complete = status === 'complete';
  const failed = status === 'failed';
  const order: BackendStatus[] = ['queued', 'extracting', 'chunking', 'embedding', 'summarizing', 'complete'];
  const overall = complete
    ? 1
    : failed
    ? 0
    : Math.max(0, order.indexOf(status)) / (order.length - 1);

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center px-6"
      style={{ background: 'color-mix(in oklch, var(--bg), transparent 8%)', backdropFilter: 'blur(6px)' }}
    >
      <div
        className="w-full max-w-[680px] rounded-2xl overflow-hidden"
        style={{
          background: 'var(--bg)',
          border: '1px solid var(--border)',
          boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)',
        }}
      >
        {/* header */}
        <div className="px-7 pt-7 pb-5 flex items-start gap-5">
          <div
            className="w-11 h-12 rounded flex items-center justify-center shrink-0"
            style={{ background: 'var(--bg-2)', border: '1px solid var(--border)' }}
          >
            <IconDoc className="w-5 h-5" style={{ color: 'var(--muted)' }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-mono uppercase tracking-wider" style={{ color: 'var(--muted)' }}>
              {complete ? 'Indexed · ready' : failed ? 'Failed' : 'Processing'}
            </div>
            <div className="mt-1 font-serif text-[20px] tracking-tight truncate" style={{ color: 'var(--fg)' }}>
              {file.name}
            </div>
            <div className="text-[12px] mt-1" style={{ color: 'var(--muted)' }}>
              {file.size} · runs locally
            </div>
          </div>
          {!complete && !failed && (
            <button
              onClick={onCancel}
              className="text-[12px] px-2 py-1 rounded"
              style={{ color: 'var(--muted)' }}
            >
              Cancel
            </button>
          )}
        </div>

        {/* overall progress bar */}
        <div className="px-7 pb-6">
          <div className="flex items-baseline justify-between mb-2">
            <span
              className="text-[11px] font-mono uppercase tracking-wider"
              style={{ color: 'var(--muted)' }}
            >
              {complete ? 'complete' : failed ? 'failed' : `status · ${status}`}
            </span>
            <span className="text-[11px] font-mono tabular-nums" style={{ color: 'var(--muted)' }}>
              {Math.round(overall * 100)}%
            </span>
          </div>
          <div className="h-[3px] rounded-full overflow-hidden" style={{ background: 'var(--bg-3)' }}>
            <div
              className="h-full transition-[width] duration-200 ease-linear"
              style={{
                width: `${overall * 100}%`,
                background: failed ? 'var(--muted)' : complete ? 'var(--ok)' : 'var(--accent)',
              }}
            />
          </div>
        </div>

        {/* extractor badge — tells the user whether MinerU or the fallback ran */}
        {(() => {
          const ex = extractorLabel(extractor);
          const dotColor =
            ex.tone === 'good' ? 'var(--ok)' :
            ex.tone === 'warn' ? '#f59e0b' :
            'var(--muted)';
          return (
            <div
              className="mx-7 mb-5 px-3 py-2 rounded-md flex items-center gap-2 text-[11.5px] font-mono"
              style={{ background: 'var(--bg-2)', border: '1px solid var(--border)', color: 'var(--muted)' }}
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: dotColor }} />
              <span>extractor · {ex.label}</span>
            </div>
          );
        })()}

        {/* step list */}
        <div className="px-7 pb-7" style={{ borderTop: '1px solid var(--border)' }}>
          {STEPS.map((step) => (
            <StepRow key={step.id} step={step} state={stateFor(step, status)} />
          ))}
        </div>

        {/* footer */}
        <div
          className="px-7 py-3.5 flex items-center gap-3"
          style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}
        >
          <div
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: failed ? 'var(--muted)' : complete ? 'var(--ok)' : 'var(--accent)' }}
          />
          <span className="text-[12px] font-mono" style={{ color: 'var(--muted)' }}>
            {complete
              ? 'indexing complete — back to library'
              : failed
              ? (errorMessage || 'pipeline failed').slice(0, 120)
              : 'safe to leave — processing continues in the background'}
          </span>
          <button
            onClick={onClose}
            className="ml-auto text-[12px] px-3 py-1.5 rounded-md"
            style={{
              background: complete ? 'var(--accent)' : 'var(--bg)',
              color: complete ? 'var(--accent-fg)' : 'var(--muted)',
              border: '1px solid var(--border)',
            }}
          >
            Back to library
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Individual step row ───────────────────────────────────────────────────────

function StepRow({ step, state }: { step: StepDef; state: StepState }) {
  return (
    <div className="py-5 flex gap-5">
      <StepIndicator id={step.id} state={state} />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-3">
          <h3
            className={`font-serif text-[17px] tracking-tight${state === 'active' ? ' pulse-soft' : ''}`}
            style={{ color: state === 'error' ? '#ef4444' : state === 'pending' ? 'var(--muted)' : 'var(--fg)' }}
          >
            {step.title}
          </h3>
          {state === 'done' && (
            <span className="text-[11px] font-mono" style={{ color: 'var(--ok)' }}>done</span>
          )}
          {state === 'active' && (
            <span className="text-[11px] font-mono" style={{ color: 'var(--accent)' }}>running…</span>
          )}
          {state === 'error' && (
            <span className="text-[11px] font-mono" style={{ color: '#ef4444' }}>failed</span>
          )}
        </div>
        <div
          className="text-[12.5px] mt-1"
          style={{ color: state === 'pending' ? 'var(--faint)' : 'var(--muted)' }}
        >
          {step.sub}
        </div>
      </div>
    </div>
  );
}

// ── Ring indicator ────────────────────────────────────────────────────────────

function StepIndicator({ id, state }: { id: number; state: StepState }) {
  const r = 11;
  const circ = 2 * Math.PI * r;

  return (
    <div className="shrink-0 w-7 flex flex-col items-center">
      <div className="relative w-7 h-7">
        {state === 'done' ? (
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center"
            style={{ background: 'var(--ok)', color: 'var(--bg)' }}
          >
            <IconCheck className="w-3.5 h-3.5" />
          </div>
        ) : state === 'active' ? (
          // Indeterminate spinner
          <svg viewBox="0 0 28 28" className="w-7 h-7 ring-anim -rotate-90 spin-slow">
            <circle cx="14" cy="14" r={r} stroke="var(--border)" strokeWidth="2" fill="none" />
            <circle
              cx="14" cy="14" r={r}
              stroke="var(--accent)"
              strokeWidth="2"
              fill="none"
              strokeLinecap="round"
              strokeDasharray={circ}
              strokeDashoffset={circ * 0.7}
            />
          </svg>
        ) : state === 'error' ? (
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center text-[14px] font-bold"
            style={{ background: '#ef4444', color: 'white' }}
          >
            ✕
          </div>
        ) : (
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-mono"
            style={{ border: '1px solid var(--border)', color: 'var(--muted)' }}
          >
            {id}
          </div>
        )}
      </div>
      <div
        className="flex-1 w-px mt-1"
        style={{
          background:
            state === 'done' ? 'var(--ok)' :
            state === 'error' ? '#ef4444' :
            'var(--border)',
          minHeight: '20px',
        }}
      />
    </div>
  );
}
