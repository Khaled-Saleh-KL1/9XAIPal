import { useEffect, useRef, useState } from 'react';

/**
 * Edit a title in place. Shared by the library cards and both reader headers
 * — the same rename, wherever the reader happens to be when they want it.
 *
 * Inline rather than in a modal: the reader is comparing this name against
 * its surroundings (the cover, the header it sits in), and a dialog covers
 * exactly that context. Enter commits, Escape reverts, blur commits — a click
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

  useEffect(() => {
    // ⚠ preventScroll is load-bearing here too — a header rename can open
    // pinned above a tall scroll area (the reader), and an unguarded focus()
    // jumps the browser to wherever it thinks the input "is" before layout
    // settles. Same fix as AskComposer's.
    ref.current?.focus({ preventScroll: true });
    ref.current?.select();
  }, []);

  return (
    <input
      ref={ref}
      className={className}
      value={draft}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onCommit(draft)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter') {
          e.preventDefault();
          onCommit(draft);
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
