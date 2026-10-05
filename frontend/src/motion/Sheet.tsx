import { useRef } from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode, RefObject, MouseEvent } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { calm, playful, reducedMotionFade } from './springs';
import { useFocusTrap } from './useFocusTrap';

export function Sheet({
  open,
  onClose,
  labelledBy,
  children,
  initialFocusRef,
  returnFocusRef,
  backdropClassName,
  panelClassName,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  backdropClassName?: string;
  panelClassName?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const backdropStarted = useRef(false);
  const reducedMotion = useReducedMotion();
  const handleKeyDown = useFocusTrap({
    active: open,
    containerRef: panelRef,
    initialFocusRef,
    returnFocusRef,
    onEscape: onClose,
    lockScroll: true,
  });

  const handleBackdropDown = (event: MouseEvent<HTMLDivElement>) => {
    backdropStarted.current = event.target === event.currentTarget;
  };

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    if (backdropStarted.current && event.target === event.currentTarget) onClose();
    backdropStarted.current = false;
  };

  const panelMotion = reducedMotion
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0, pointerEvents: 'none' }, transition: reducedMotionFade }
    : {
        initial: { y: 24, opacity: 0, scale: 0.96 },
        animate: { y: 0, opacity: 1, rotate: 0, scale: 1 },
        exit: { y: 32, opacity: 0, scale: 0.96, pointerEvents: 'none' },
        transition: playful,
      };

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence onExitComplete={() => returnFocusRef?.current?.focus()}>
      {open && (
        <m.div
          key="sheet-backdrop"
          className={['motion-sheet-backdrop', 'motion-sheet-backdrop--centered', backdropClassName].filter(Boolean).join(' ')}
          data-testid="sheet-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, pointerEvents: 'none' }}
          transition={reducedMotion ? reducedMotionFade : calm}
          onMouseDown={handleBackdropDown}
          onClick={handleBackdropClick}
        >
          <m.div
            {...panelMotion}
            ref={panelRef}
            className={['motion-sheet-panel', 'motion-sheet-panel--centered', panelClassName].filter(Boolean).join(' ')}
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
