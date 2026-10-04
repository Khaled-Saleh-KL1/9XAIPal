import { LazyMotion, MotionConfig } from 'motion/react';
import type { ReactNode } from 'react';
import './motion.css';

// Drag and layout features load asynchronously, keeping the initial bundle lean.
const loadFeatures = () => import('motion/react').then((mod) => mod.domMax);

export function MotionRoot({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
