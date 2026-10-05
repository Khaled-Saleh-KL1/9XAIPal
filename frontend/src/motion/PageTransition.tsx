import type { ReactNode } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { gentle, reducedMotionFade } from './springs';

export function PageTransition({ routeKey, children }: { routeKey: string; children: ReactNode }) {
  const reducedMotion = useReducedMotion();
  return (
    <m.div
      key={routeKey}
      data-testid="page-transition"
      className="h-screen"
      initial={reducedMotion ? { opacity: 0 } : { opacity: 0, x: 24, rotateY: -6 }}
      animate={reducedMotion ? { opacity: 1 } : { opacity: 1, x: 0, rotateY: 0 }}
      transition={reducedMotion ? reducedMotionFade : gentle}
      style={{ transformOrigin: 'left center', perspective: 1200 }}
    >
      {children}
    </m.div>
  );
}
