import { useCallback, useLayoutEffect } from 'react';
import type { KeyboardEvent, RefObject } from 'react';

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useFocusTrap({
  active,
  containerRef,
  initialFocusRef,
  returnFocusRef,
  onEscape,
  lockScroll = false,
}: {
  active: boolean;
  containerRef: RefObject<HTMLElement | null>;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  onEscape?: () => void;
  lockScroll?: boolean;
}) {
  useLayoutEffect(() => {
    if (!active) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = 'hidden';

    const initial = initialFocusRef?.current ?? containerRef.current?.querySelector<HTMLElement>(focusableSelector);
    (initial ?? containerRef.current)?.focus();

    return () => {
      if (lockScroll) document.body.style.overflow = previousOverflow;
      (returnFocusRef?.current ?? previouslyFocused)?.focus();
    };
  }, [active, containerRef, initialFocusRef, returnFocusRef, lockScroll]);

  return useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape' && onEscape) {
      event.preventDefault();
      event.stopPropagation();
      onEscape();
      return;
    }
    if (event.key !== 'Tab' || !containerRef.current) return;

    const focusable = Array.from(containerRef.current.querySelectorAll<HTMLElement>(focusableSelector))
      .filter((element) => element.getAttribute('aria-hidden') !== 'true');
    if (!focusable.length) {
      event.preventDefault();
      containerRef.current.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const activeElement = document.activeElement;
    if (event.shiftKey && (activeElement === first || !containerRef.current.contains(activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (activeElement === last || !containerRef.current.contains(activeElement))) {
      event.preventDefault();
      first.focus();
    }
  }, [containerRef, onEscape]);
}
