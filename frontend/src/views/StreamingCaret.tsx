import { useRef } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { usePauseWhenHidden } from '../motion';

/** A small ink caret that only pulses while its streamed answer is visible. */
export function StreamingCaret() {
  const caretRef = useRef<HTMLSpanElement>(null);
  const caretVisible = usePauseWhenHidden(caretRef);
  const reducedMotion = useReducedMotion();

  return (
    <m.span
      ref={caretRef}
      data-testid="chat-stream-caret"
      className="chat-stream-caret"
      aria-hidden="true"
      animate={!reducedMotion && caretVisible ? { opacity: [0.4, 1, 0.4] } : undefined}
      transition={{ duration: 0.9, ease: 'easeInOut', repeat: Infinity }}
    />
  );
}
