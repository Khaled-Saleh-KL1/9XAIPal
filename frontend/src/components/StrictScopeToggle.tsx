/**
 * Header pill for documents.strict_scope — whether this document's own
 * reading chat may answer from outside what it itself says (see the
 * column's own comment in backend/app/database/schema.sql).
 *
 * ⚠ Research papers only (2026-09-12). It used to sit on every reader; the
 * reader's call was that a book's chat and an article's chat are always
 * scoped to the document, so the switch is gone from both, and the backend
 * (`PATCH /papers/{id}/strict-scope`) refuses to change the flag for them —
 * the gate in ArticleReader is UI, the refusal is the policy. Not rendered
 * on the Desk either: that view has no single document to scope to.
 *
 * Styled with plain Tailwind + CSS-variable inline styles rather than the
 * reader's own chip class, matching this codebase's precedent for a shared
 * header control (see ExtractorPill in BookReadingView.tsx).
 */
import { useState } from 'react';
import { setStrictScope } from '../api';

export function StrictScopeToggle({
  paperId,
  strictScope,
  onChange,
}: {
  paperId: string;
  /** True (the default) = scoped to this document. */
  strictScope: boolean;
  /** Called with the new value once the server confirms it. */
  onChange: (next: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    if (busy) return;
    const next = !strictScope;
    setBusy(true);
    onChange(next); // optimistic — the common case, and reverted below on failure
    try {
      await setStrictScope(paperId, next);
    } catch {
      onChange(strictScope); // revert to what it actually was before this click
      alert('Could not change this — try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={toggle}
      disabled={busy}
      title={
        strictScope
          ? 'Scoped to this document. Click to let the assistant also answer from general knowledge and the web on its own, not only when you ask for a comparison or a web search.'
          : 'Open to outside knowledge. Click to keep the assistant scoped to only what this document itself says (a comparison or an explicit web search still works either way).'
      }
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-mono shrink-0"
      style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
        color: 'var(--muted)',
        opacity: busy ? 0.6 : 1,
        cursor: busy ? 'default' : 'pointer',
      }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: strictScope ? 'var(--ok)' : 'var(--muted)' }}
      />
      <span>{strictScope ? 'Scoped' : 'Open'}</span>
    </button>
  );
}
