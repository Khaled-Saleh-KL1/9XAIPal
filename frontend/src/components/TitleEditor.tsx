import { useEffect, useRef, useState } from 'react';

/**
 * Edit a title in place. Shared by the library cards and both reader headers:
 * the same rename, wherever the reader happens to be when they want it.
 *
 * Inline rather than in a modal: the reader is comparing this name against
 * its surroundings (the cover, the header it sits in), and a dialog covers
 * exactly that context. Enter commits, Escape reverts, and blur commits: a click
 * elsewhere after typing a name means the name, not "discard it".
 */
export function TitleEditor({
  value,
  onCommit,
  onCancel,
  className = 'paper-title-input',
  placeholder = 'Name this paper…',
}: {
  value: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
  className?: string;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  const ref = useRef<HTMLInputElement>(null);
  // ⚠ Enter commits and (via the caller flipping its "renaming" state)
  // unmounts this input on the next render: removing a focused element
  // from the DOM can refire its blur synthetically, which would call
  // onCommit a second time. Both calls carry the same draft, so this was
  // never a data-corruption risk, just a wasted extra request; this stops
  // it outright rather than relying on the caller to dedupe identical PATCHes.
  const committed = useRef(false);

  useEffect(() => {
    // ⚠ preventScroll is load-bearing here too: a header rename can open
    // pinned above a tall scroll area (the reader), and an unguarded focus()
    // jumps the browser to wherever it thinks the input "is" before layout
    // settles. Same fix as AskComposer's.
    ref.current?.focus({ preventScroll: true });
    ref.current?.select();
  }, []);

  const commitOnce = (next: string) => {
    if (committed.current) return;
    committed.current = true;
    onCommit(next);
  };

  return (
    <input
      ref={ref}
      className={className}
      value={draft}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => commitOnce(draft)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') {
          e.preventDefault();
          commitOnce(draft);
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          onCancel();
        }
      }}
      placeholder={placeholder}
      aria-label="Title"
    />
  );
}
