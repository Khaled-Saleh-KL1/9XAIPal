import { useLayoutEffect, type RefObject } from 'react';

/**
 * Grows a textarea to fit what's typed instead of a fixed number of rows
 * that silently scrolls earlier lines out of view once the text runs past
 * them — indistinguishable, while typing, from the text having vanished.
 *
 * Runs on every change to `value`, not just keystrokes, so clearing the
 * field after sending shrinks it back down too. Past `maxHeight` it scrolls
 * internally like a normal fixed-size textarea.
 */
export function useAutoGrowTextarea(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxHeight: number,
) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, [ref, value, maxHeight]);
}
