import { useState } from 'react';
import type { GroundingClaim, GroundingReport, GroundingVerdict } from '../api';

/**
 * The evidence behind an answer, claim by claim.
 *
 * The trail (AgentTrail) says what the model fetched; this says whether what
 * it then wrote is actually in those passages. Every sentence of the answer is
 * listed with a verdict and, where there is one, the passage it rests on,
 * quoted inline so the reader can check without leaving the card. A sentence
 * the paper does not support is marked, not removed: the answer stays exactly
 * as the model wrote it, and the reader sees which parts to trust.
 *
 * ⚠ The check is itself a model's judgement. The panel says so in its footer,
 * and a check that could not run renders as "couldn't verify", never as a
 * clean bill of health.
 *
 * Split into a pure list (`EvidenceList`, renderable with no context or
 * state) and a thin stateful wrapper, so every state has a render test.
 */

const MARK: Record<GroundingVerdict, { glyph: string; label: string; title: string }> = {
  supported: { glyph: '✓', label: 'in the paper', title: 'The cited passage says this' },
  partial: { glyph: '◐', label: 'partly', title: 'The passage supports part of this; the rest goes beyond it' },
  unsupported: { glyph: '⚠', label: 'not in the cited passage', title: 'The cited passage does not say this' },
  uncited: { glyph: '○', label: 'not from the paper', title: 'Nothing retrieved from the paper supports this: the model’s own inference or outside knowledge' },
};

/** "6 of 7 claims verified · 1 uncited": the line that stands for the panel when it is closed. */
export function evidenceSummary(report: GroundingReport): string {
  const n = report.claims.length;
  if (report.status !== 'verified') return 'Couldn’t verify this answer';
  if (n === 0) return 'Nothing to verify';
  const s = report.summary;
  const ok = s.supported ?? 0;
  const parts = [`${ok} of ${n} claim${n === 1 ? '' : 's'} verified`];
  if (s.partial) parts.push(`${s.partial} partly`);
  if (s.unsupported) parts.push(`${s.unsupported} unsupported`);
  if (s.uncited) parts.push(`${s.uncited} not from the paper`);
  return parts.join(' · ');
}

/** Where a claim's evidence lives, for the jump button. */
function whereLabel(claim: GroundingClaim, paperLabel?: (documentId: string | null) => string | null): string {
  const ev = claim.evidence;
  if (!ev) return '';
  const paper = paperLabel?.(ev.document_id);
  const place = ev.page != null ? `p. ${ev.page}` : `¶${ev.sequence_id}`;
  return paper ? `${paper} · ${place}` : place;
}

export function EvidenceList({
  report,
  onJump,
  paperLabel,
}: {
  report: GroundingReport;
  /** Open the passage the claim rests on. Omitted where there is no reader to jump in. */
  onJump?: (documentId: string | null, sequenceId: number) => void;
  /** The desk spans papers: name which one ("P2"). Single-paper surfaces omit it. */
  paperLabel?: (documentId: string | null) => string | null;
}) {
  if (report.status !== 'verified') {
    return (
      <div className="evidence-unavailable">
        The evidence check could not run{report.reason ? ` (${report.reason})` : ''}. The answer
        above is unverified — treat its citations as the model’s own claims.
      </div>
    );
  }
  if (!report.claims.length) {
    return <div className="evidence-unavailable">This answer makes no claims to check.</div>;
  }
  return (
    <>
      <ol className="evidence-claims">
        {report.claims.map((claim, i) => {
          const mark = MARK[claim.verdict] ?? MARK.uncited;
          const ev = claim.evidence;
          return (
            <li key={i} className={`evidence-claim is-${claim.verdict}`}>
              <div className="evidence-line">
                <span className="evidence-mark" title={mark.title} aria-label={mark.label}>{mark.glyph}</span>
                <span className="evidence-text">{claim.text}</span>
              </div>
              {claim.verdict !== 'supported' && (
                <div className="evidence-verdict">
                  {mark.label}
                  {claim.note ? ` — ${claim.note}` : ''}
                </div>
              )}
              {(claim.quote || ev) && (
                <div className="evidence-quote">
                  {claim.quote && <q>{claim.quote}</q>}
                  {ev && (
                    onJump ? (
                      <button
                        type="button"
                        className="evidence-jump"
                        onClick={() => onJump(ev.document_id, ev.sequence_id)}
                        title="Open this passage"
                      >
                        {whereLabel(claim, paperLabel)}
                      </button>
                    ) : (
                      <span className="evidence-jump">{whereLabel(claim, paperLabel)}</span>
                    )
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
      <div className="evidence-foot">
        Checked by the model{report.model ? ` (${report.model})` : ''} against the passages it cited
        and read. It flags; it does not rewrite — the answer above is exactly what was generated.
      </div>
    </>
  );
}

export function EvidencePanel({
  report,
  verifying = false,
  onJump,
  paperLabel,
}: {
  /** Null or undefined: no check for this answer (older row, or the check is off). */
  report: GroundingReport | null | undefined;
  /** The answer is complete and the check is running. */
  verifying?: boolean;
  onJump?: (documentId: string | null, sequenceId: number) => void;
  paperLabel?: (documentId: string | null) => string | null;
}) {
  const [open, setOpen] = useState(false);

  if (verifying && !report) {
    return (
      <div className="evidence-panel is-verifying">
        <span className="note-dot" />
        Verifying evidence…
      </div>
    );
  }
  if (!report) return null;

  const attention = report.status === 'verified'
    && ((report.summary.unsupported ?? 0) + (report.summary.uncited ?? 0) + (report.summary.partial ?? 0)) > 0;

  return (
    <div className={`evidence-panel${open ? ' is-open' : ''}${attention ? ' has-flags' : ''}${report.status !== 'verified' ? ' is-unavailable' : ''}`}>
      <button
        type="button"
        className="evidence-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="trail-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
        <span className="evidence-summary">{evidenceSummary(report)}</span>
      </button>
      {open && <EvidenceList report={report} onJump={onJump} paperLabel={paperLabel} />}
    </div>
  );
}
