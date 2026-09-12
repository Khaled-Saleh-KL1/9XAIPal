/**
 * Header pill for the paper reader's reveal mode: whole article, or one block
 * at a time the way the book reader works.
 *
 * Styled with plain Tailwind and a state dot rather than as a `reader-chip`,
 * this codebase's precedent for a shared header control. It is also the
 * reader's one and only "how much does the assistant see" control: Whole =
 * the whole paper, Stepped = what has been revealed (the ask ceiling), the
 * web only when a question asks for it. The Scoped/Open switch that used to
 * sit beside it was removed on 2026-09-12 as redundant with this one.
 *
 * ⚠ Papers only, deliberately. Books already read this way and have no use for
 * a toggle that turns their own behavior off; an imported article is a web
 * page whose blocks came from a snapshot rather than a paginated document, and
 * segmenting one was not what readers asked for. The gate lives at the call
 * site so the reason stays next to `doc_kind`.
 */

export function RevealModeToggle({
  on,
  onChange,
}: {
  /** True = reveal one block at a time. */
  on: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!on)}
      title={
        on
          ? 'Revealing one block at a time. Click to show the whole paper at once.'
          : 'Showing the whole paper. Click to reveal it one block at a time, the way a book reads (→ for the next block).'
      }
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-mono shrink-0"
      style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
        color: 'var(--muted)',
      }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: on ? 'var(--ok)' : 'var(--muted)' }}
      />
      <span>{on ? 'Stepped' : 'Whole'}</span>
    </button>
  );
}
