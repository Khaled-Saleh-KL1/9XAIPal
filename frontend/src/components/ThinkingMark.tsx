import { useRef } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { usePauseWhenHidden } from '../motion';

export function ThinkingMark() {
  const ref = useRef<HTMLDivElement>(null);
  const visible = usePauseWhenHidden(ref);
  const reducedMotion = Boolean(useReducedMotion());
  const animate = visible && !reducedMotion;

  return (
    <div ref={ref} className="thinking-mark" data-testid="thinking-mark" aria-hidden="true">
      <m.div
        className="thinking-mark-box"
        style={{ background: 'var(--fg)', color: 'var(--bg)' }}
        {...(animate ? {
          animate: { scale: [1, 1.06, 1.03, 1], rotate: [0, 4, -4, 0] },
          transition: { duration: 3.2, repeat: Infinity, ease: 'easeInOut' },
        } : {})}
      >
        <m.span
          className="thinking-mark-nine"
          style={{ textShadow: '0 0 5px color-mix(in srgb, var(--accent) 60%, transparent)' }}
          {...(animate ? {
            animate: { opacity: [0.86, 1, 0.86] },
            transition: { duration: 2, repeat: Infinity, ease: 'easeInOut' },
          } : {})}
        >
          9
        </m.span>
      </m.div>
      <m.span
        className="thinking-mark-dot"
        {...(animate ? {
          animate: {
            x: [12, 8, 0, -8, -12, -8, 0, 8, 12],
            y: [0, 8, 12, 8, 0, -8, -12, -8, 0],
          },
          transition: { duration: 3.6, repeat: Infinity, ease: 'linear' },
        } : {})}
      />
    </div>
  );
}
