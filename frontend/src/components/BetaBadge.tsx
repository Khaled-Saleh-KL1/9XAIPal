import { useId, useState } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { BETA_LABEL, BETA_NOTE } from '../landing/content';
import { playful, reducedMotionFade } from '../motion/springs';

export function BetaBadge() {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  const reducedMotion = useReducedMotion();
  const enter = reducedMotion ? { opacity: 1 } : { opacity: 1, scale: 1 };
  const initial = reducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.7 };

  return (
    <span
      className="beta-badge-wrap"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <m.button
        className="beta-badge"
        type="button"
        aria-describedby={tooltipId}
        initial={initial}
        animate={enter}
        transition={reducedMotion ? reducedMotionFade : playful}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        {BETA_LABEL}
      </m.button>
      <AnimatePresence>
        {open && (
          <m.span
            id={tooltipId}
            className="beta-tooltip"
            role="tooltip"
            initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
            animate={reducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={reducedMotion ? reducedMotionFade : playful}
          >
            {BETA_NOTE}
          </m.span>
        )}
      </AnimatePresence>
    </span>
  );
}
