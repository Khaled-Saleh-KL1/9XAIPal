import { useState } from 'react';
import type { AgentStep } from '../api';

/**
 * What the agent did to answer, shown to the reader.
 *
 * The agent can spend six rounds fetching sections, searching the paper, and
 * reading the web before it writes a word. Without this that time is a spinner
 * and the answer arrives as an assertion: indistinguishable, from the
 * reader's side, from a model that made it up. The trail turns the answer into
 * something checkable: these sections, that search, this source.
 *
 * ⚠ Open while it works, collapsed once it is done. Live, the trail IS the
 * progress indicator and hiding it leaves a blank card. Afterwards the answer
 * is what the reader came for, and a permanently expanded audit log pushes it
 * off the bottom of a margin card.
 */

const TOOL_GLYPH: Record<AgentStep['tool'], string> = {
  SECTION: '§',
  SEARCH: '⌕',
  READ: '¶',
  WEB: '⌘',
  // Writes, not fetches: the only steps that change something.
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
  return (
    <li className={`trail-step tool-${step.tool.toLowerCase()}${running ? ' is-running' : ''}`}>
      {step.think && <div className="trail-think">{step.think}</div>}
      <div className="trail-line">
        <span className="trail-glyph" aria-hidden="true">{TOOL_GLYPH[step.tool]}</span>
        <span className="trail-label">{step.label}</span>
        {running ? (
          <span className="trail-spinner" aria-label="working" />
        ) : (
          step.result && <span className="trail-result">{step.result}</span>
        )}
      </div>

      {/* Blocks the call pulled in. Capped: a SECTION over a long chapter
          returns forty, and forty chips are a wall, not a navigation aid. */}
      {!running && step.seqs.length > 0 && onJump && (
        <div className="trail-seqs">
          {step.seqs.slice(0, 6).map((seq) => (
            <button key={seq} type="button" className="trail-seq" onClick={() => onJump(seq)}>
              ¶{seq}
            </button>
          ))}
          {step.seqs.length > 6 && (
            <span className="trail-more">+{step.seqs.length - 6}</span>
          )}
        </div>
      )}

      {!running && step.sources.length > 0 && (
        <ul className="trail-sources">
          {step.sources.map((src) => (
            <li key={src.url}>
              <a href={src.url} target="_blank" rel="noreferrer noopener" title={src.title}>
                {hostOf(src.url)}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function AgentTrail({
  steps,
  live = false,
  onJump,
}: {
  steps: AgentStep[];
  /** True while the answer is still generating: keeps the trail expanded. */
  live?: boolean;
  onJump?: (seq: number) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;

  const expanded = live || open;
  const web = steps.filter((s) => s.tool === 'WEB').length;
  const wrote = steps.filter((s) => s.tool === 'NOTE').length;
  const remembered = steps.filter((s) => s.tool === 'REMEMBER').length;
  const paper = steps.length - web - wrote - remembered;

  // "3 fetches" says nothing about where the answer came from; naming the kinds
  // separately does, because "1 from the web" is the part a reader may want to
  // weigh differently from the paper's own text, and a note it pinned, or
  // something it remembered, is something they will find later whether or not
  // they read the trail now.
  const summary = [
    paper ? `${paper} from the paper` : '',
    web ? `${web} from the web` : '',
    wrote ? `${wrote} note${wrote === 1 ? '' : 's'} pinned` : '',
    remembered ? `${remembered} remembered` : '',
  ].filter(Boolean).join(' · ');

  return (
    <div className={`agent-trail${expanded ? ' is-open' : ''}`}>
      {!live && (
        <button
          type="button"
          className="trail-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={expanded}
        >
          <span className="trail-caret" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          How this was answered
          <span className="trail-count">{summary}</span>
        </button>
      )}
      {expanded && (
        <ol className="trail-steps">
          {steps.map((step) => (
            <StepRow key={step.id} step={step} onJump={onJump} />
          ))}
        </ol>
      )}
    </div>
  );
}
