import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode, RefObject, MouseEvent, KeyboardEvent } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { calm, playful } from './springs';

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function Sheet({
  open,
  onClose,
  labelledBy,
  children,
  initialFocusRef,
  returnFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const backdropStarted = useRef(false);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const frame = requestAnimationFrame(() => {
      const initial = initialFocusRef?.current ?? panelRef.current?.querySelector<HTMLElement>(focusableSelector);
      (initial ?? panelRef.current)?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, initialFocusRef]);

  const handleBackdropDown = (event: MouseEvent<HTMLDivElement>) => {
    backdropStarted.current = event.target === event.currentTarget;
  };

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    if (backdropStarted.current && event.target === event.currentTarget) onClose();
    backdropStarted.current = false;
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab' || !panelRef.current) return;

    const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>(focusableSelector))
      .filter((element) => element.getAttribute('aria-hidden') !== 'true');
    if (!focusable.length) {
      event.preventDefault();
      panelRef.current.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !panelRef.current.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !panelRef.current.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };

  const panelMotion = reducedMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 }, transition: calm }
    : {
        initial: { y: 40, opacity: 0, rotate: -1, scale: 0.96 },
        animate: { y: 0, opacity: 1, rotate: 0, scale: 1 },
        exit: { y: 24, opacity: 0, scale: 0.96 },
        transition: playful,
      };

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence onExitComplete={() => returnFocusRef?.current?.focus()}>
      {open && (
        <m.div
          key="sheet-backdrop"
          className="motion-sheet-backdrop"
          data-testid="sheet-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={calm}
          onMouseDown={handleBackdropDown}
          onClick={handleBackdropClick}
        >
          <m.div
            {...panelMotion}
            ref={panelRef}
            className="motion-sheet-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby={labelledBy}
            tabIndex={-1}
            onKeyDown={handleKeyDown}
            onClick={(event) => event.stopPropagation()}
          >
            {children}
          </m.div>
        </m.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
