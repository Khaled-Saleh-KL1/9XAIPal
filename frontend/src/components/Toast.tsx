import { useEffect, useRef } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { Pressable } from '../motion';
import { playful, reducedMotionFade } from '../motion/springs';

export interface ToastNotice {
  text: string;
  tone: 'info' | 'error';
}

export function Toast({ notice, onDismiss }: { notice: ToastNotice | null; onDismiss: () => void }) {
  const reducedMotion = useReducedMotion();
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => dismissRef.current(), notice.tone === 'error' ? 9000 : 7000);
    return () => clearTimeout(timer);
  }, [notice]);

  return (
    <AnimatePresence initial={false}>
      {notice && (
        <m.div
          key={`${notice.tone}:${notice.text}`}
          className={`reader-toast${notice.tone === 'error' ? ' is-error' : ''}`}
          role={notice.tone === 'error' ? 'alert' : 'status'}
          aria-live={notice.tone === 'error' ? 'assertive' : 'polite'}
          aria-atomic="true"
          initial={reducedMotion ? { opacity: 0, x: '-50%' } : { opacity: 0, x: '-50%', y: 40, scale: 0.9 }}
          animate={reducedMotion ? { opacity: 1, x: '-50%' } : { opacity: 1, x: '-50%', y: 0, scale: 1 }}
          exit={reducedMotion ? { opacity: 0, x: '-50%' } : { opacity: 0, x: '-50%', y: 28, scale: 0.92 }}
          transition={reducedMotion ? reducedMotionFade : playful}
        >
          <span>{notice.text}</span>
          <Pressable type="button" onClick={onDismiss} aria-label="Dismiss">
            ×
          </Pressable>
        </m.div>
      )}
    </AnimatePresence>
  );
}
