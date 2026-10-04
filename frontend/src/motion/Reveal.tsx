import { Children, cloneElement, isValidElement } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { m } from 'motion/react';
import { playful } from './springs';

type MotionTag = 'div' | 'section' | 'article' | 'li' | 'span';

export function Reveal({
  children,
  delay = 0,
  y = 18,
  className,
  as = 'div',
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
  as?: MotionTag;
}) {
  const MotionTagComponent = m[as] as typeof m.div;
  return (
    <MotionTagComponent
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-60px' }}
      transition={{ ...playful, delay }}
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
  const items = Children.map(children, (child, index) => {
    if (isValidElement(child) && child.type === StaggerItem) {
      return cloneElement(child as ReactElement<StaggerItemProps>, { staggered: index < 12 });
    }
    return child;
  });

  return (
    <m.div
      className={className}
      variants={{ ...containerVariants, visible: { transition: { staggerChildren: gap } } }}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: '-60px' }}
    >
      {items}
    </m.div>
  );
}

type StaggerItemProps = { children: ReactNode; className?: string; staggered?: boolean };
export function StaggerItem({ children, className, staggered = true }: StaggerItemProps) {
  return (
    <m.div
      className={className}
      variants={staggered ? itemVariants : undefined}
      initial={staggered ? undefined : false}
    >
      {children}
    </m.div>
  );
}
