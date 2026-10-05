import { Children, cloneElement, isValidElement } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { playful } from './springs';

type MotionTag = 'div' | 'section' | 'article' | 'h2' | 'h3' | 'li' | 'span';

export function Reveal({
  children,
  delay = 0,
  y = 18,
  className,
  as = 'div',
  id,
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
  as?: MotionTag;
  id?: string;
}) {
  const reducedMotion = useReducedMotion();
  const MotionTagComponent = m[as] as typeof m.div;
  return (
    <MotionTagComponent
      className={className}
      id={id}
      initial={reducedMotion ? false : { opacity: 0, y }}
      whileInView={reducedMotion ? undefined : { opacity: 1, y: 0 }}
      viewport={reducedMotion ? undefined : { once: true, margin: '-60px' }}
      transition={reducedMotion ? { duration: 0.15 } : { ...playful, delay }}
    >
      {children}
    </MotionTagComponent>
  );
}

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: playful },
};

export function Stagger({ children, className, gap = 0.07 }: { children: ReactNode; className?: string; gap?: number }) {
  const reducedMotion = useReducedMotion();
  const items = Children.map(children, (child, index) => {
    if (isValidElement(child) && child.type === StaggerItem) {
      return cloneElement(child as ReactElement<StaggerItemProps>, { staggered: index < 12 });
    }
    return child;
  });

  return (
    <m.div
      className={className}
      variants={reducedMotion ? undefined : { ...containerVariants, visible: { transition: { staggerChildren: gap } } }}
      initial={reducedMotion ? false : 'hidden'}
      whileInView={reducedMotion ? undefined : 'visible'}
      viewport={reducedMotion ? undefined : { once: true, margin: '-60px' }}
    >
      {items}
    </m.div>
  );
}

type StaggerItemProps = { children: ReactNode; className?: string; staggered?: boolean };
export function StaggerItem({ children, className, staggered = true }: StaggerItemProps) {
  const reducedMotion = useReducedMotion();
  return (
    <m.div
      className={className}
      variants={staggered && !reducedMotion ? itemVariants : undefined}
      initial={staggered && !reducedMotion ? undefined : false}
    >
      {children}
    </m.div>
  );
}
