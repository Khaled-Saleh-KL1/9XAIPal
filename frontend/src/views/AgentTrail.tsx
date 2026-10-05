import { useEffect, useId, useRef, useState } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { citeChips, usePageMap } from '../lib/pageMap';
import type { AgentStep } from '../api';
import { calm, playful, reducedMotionFade } from '../motion';
import { ThinkingMark } from '../components/ThinkingMark';

const TOOL_GLYPH: Record<AgentStep['tool'], string> = {
  SECTION: '§',
  SEARCH: '⌕',
  READ: '¶',
  WEB: '⌘',
  NOTE: '✎',
  REMEMBER: '✦',
};

/** The domain alone: a trail row has no space for a full URL. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function StepRow({
  step,
  onJump,
}: {
  step: AgentStep;
  onJump?: (seq: number) => void;
}) {
  const running = step.state === 'running';
  const { pageFor } = usePageMap();
  const chips = citeChips(step.seqs, pageFor);
  return (
    <li className={`trail-step tool-${step.tool.toLowerCase()}${running ? ' is-running' : ''}`}>
      <div className="trail-line">
        <span className="trail-glyph" aria-hidden="true">{TOOL_GLYPH[step.tool]}</span>
        <span className="trail-label">{step.label}</span>
        {running ? (
          <span className="trail-spinner" aria-label="working" />
        ) : (
          step.result && <span className="trail-result">{step.result}</span>
        )}
      </div>

      {/* Collapse repeated citations by page before applying the chip cap. */}
      {!running && step.seqs.length > 0 && onJump && (
        <div className="trail-seqs">
          {chips.slice(0, 6).map((chip) => (
            <button
              key={chip.seq}
              type="button"
              className="trail-seq"
              onClick={() => onJump(chip.seq)}
              title={chip.title}
            >
              {chip.label}
            </button>
          ))}
          {chips.length > 6 && (
            <span className="trail-more">+{chips.length - 6}</span>
          )}
        </div>
      )}

      {!running && step.sources.length > 0 && (
        <ul className="trail-sources">
          {step.sources.map((src) => (
            <li key={src.url}>
              <a className="trail-source-link" href={src.url} target="_blank" rel="noreferrer noopener" title={src.title}>
                → {hostOf(src.url)}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function statusForStep(step: AgentStep): string {
  const arg = step.arg.trim() || step.label.trim();
  switch (step.tool) {
    case 'SECTION':
    case 'READ':
      return `Reading ${arg}…`;
    case 'SEARCH':
      return `Searching “${arg}”…`;
    case 'WEB':
      return 'Reading the web…';
    case 'NOTE':
      return 'Pinning a note…';
    case 'REMEMBER':
      return 'Remembering…';
  }
}

function StatusText({ status, reducedMotion }: { status: string; reducedMotion: boolean }) {
  return (
    <span className="trail-progress-status" aria-live="polite" aria-atomic="true">
      <AnimatePresence mode="wait" initial={false}>
        {reducedMotion ? (
          <span key={status}>{status}</span>
        ) : (
          <m.span
            key={status}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={calm}
          >
            {status}
          </m.span>
        )}
      </AnimatePresence>
    </span>
  );
}

export interface ReasoningProps {
  steps: AgentStep[];
  /** True while the request is still in flight. */
  live?: boolean;
  /** True after answer text has started streaming. */
  writing?: boolean;
  onJump?: (seq: number) => void;
}

export function Reasoning({
  steps,
  live = false,
  writing = false,
  onJump,
}: ReasoningProps) {
  const instanceId = useId();
  const reducedMotion = Boolean(useReducedMotion());
  const [expandedRounds, setExpandedRounds] = useState<Set<number>>(() => new Set());
  const [showAllRounds, setShowAllRounds] = useState(false);
  const [writingElapsed, setWritingElapsed] = useState(false);
  const wasLive = useRef(live);

  useEffect(() => {
    if (wasLive.current && !live) setExpandedRounds(new Set());
    wasLive.current = live;
  }, [live]);

  useEffect(() => {
    if (!live || !writing) {
      setWritingElapsed(false);
      return;
    }
    // Begin the status change early enough for the short crossfade to finish
    // at roughly the eight-second mark.
    const timer = window.setTimeout(() => setWritingElapsed(true), 7_800);
    return () => window.clearTimeout(timer);
  }, [live, writing]);

  const grouped = new Map<number, AgentStep[]>();
  for (const step of steps) {
    const round = grouped.get(step.n) ?? [];
    round.push(step);
    grouped.set(step.n, round);
  }
  const rounds = [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([n, roundSteps]) => ({ n, steps: roundSteps }));
  const visibleRounds = showAllRounds ? rounds : rounds.slice(0, 6);

  const web = steps.filter((step) => step.tool === 'WEB').length;
  const wrote = steps.filter((step) => step.tool === 'NOTE').length;
  const remembered = steps.filter((step) => step.tool === 'REMEMBER').length;
  const paper = steps.length - web - wrote - remembered;
  const summary = [
    paper ? `${paper} from the paper` : '',
    web ? `${web} from the web` : '',
    wrote ? `${wrote} note${wrote === 1 ? '' : 's'} pinned` : '',
    remembered ? `${remembered} remembered` : '',
  ].filter(Boolean).join(' · ');

  const runningStep = [...steps].reverse().find((step) => step.state === 'running');
  const status = writing
    ? writingElapsed ? 'Almost there…' : 'Writing the answer…'
    : runningStep
      ? statusForStep(runningStep)
      : 'Thinking…';

  if (!steps.length && !live) return null;

  return (
    <div className="agent-trail">
      {rounds.length > 0 && (
        <div className="trail-rounds">
          {visibleRounds.map((round) => {
            const label = round.steps[0]?.think?.trim() || 'See reasoning';
            const expanded = expandedRounds.has(round.n);
            const detailsId = `${instanceId}-round-${round.n}`;
            return (
              <m.div
                key={round.n}
                className="trail-round"
                initial={live && !reducedMotion ? { opacity: 0, scale: 0.94, rotate: -3, y: 6 } : false}
                animate={live && !reducedMotion ? { opacity: 1, scale: 1, rotate: 0, y: 0 } : false}
                transition={playful}
              >
                <button
                  type="button"
                  className="trail-round-toggle"
                  aria-expanded={expanded}
                  aria-controls={expanded ? detailsId : undefined}
                  title={label}
                  onClick={() => setExpandedRounds((previous) => {
                    const next = new Set(previous);
                    if (next.has(round.n)) next.delete(round.n);
                    else next.add(round.n);
                    return next;
                  })}
                >
                  <m.span
                    className={`trail-caret trail-round-caret${expanded ? ' is-expanded' : ''}`}
                    aria-hidden="true"
                    animate={{ rotate: expanded ? 90 : 0 }}
                    transition={reducedMotion ? { duration: 0 } : playful}
                  >
                    ›
                  </m.span>
                  <span className="trail-round-label">{label}</span>
                </button>
                <AnimatePresence initial={false}>
                  {expanded && (
                    <m.div
                      key="details"
                      id={detailsId}
                      className="trail-round-content"
                      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 5 }}
                      animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
                      exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: -5 }}
                      transition={reducedMotion ? reducedMotionFade : calm}
                    >
                      <ol className="trail-steps">
                        {round.steps.map((step) => (
                          <StepRow key={step.id} step={step} onJump={onJump} />
                        ))}
                      </ol>
                    </m.div>
                  )}
                </AnimatePresence>
              </m.div>
            );
          })}
          {!showAllRounds && rounds.length > 6 && (
            <button
              type="button"
              className="trail-show-more"
              onClick={() => setShowAllRounds(true)}
            >
              +{rounds.length - 6} more steps
            </button>
          )}
        </div>
      )}

      {summary && <div className="trail-summary">{summary}</div>}

      <AnimatePresence initial={false}>
        {live && (
          <m.div
            key="progress"
            className="trail-progress"
            initial={reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.72 }}
            transition={reducedMotion ? reducedMotionFade : calm}
          >
            <ThinkingMark />
            <StatusText status={status} reducedMotion={reducedMotion} />
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Backwards-compatible name for consumers that still call this an agent trail. */
export const AgentTrail = Reasoning;
