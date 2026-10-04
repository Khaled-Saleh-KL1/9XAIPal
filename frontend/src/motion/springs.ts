import type { Transition } from 'motion/react';

/** The overshooting spring used for playful interaction. */
export const playful: Transition = { type: 'spring', stiffness: 400, damping: 17, mass: 0.9 };
/** A softer spring for panels and sheets. */
export const gentle: Transition = { type: 'spring', stiffness: 260, damping: 26 };
/** A short tween for quiet transitions and reduced-motion fades. */
export const calm: Transition = { type: 'tween', duration: 0.18, ease: 'easeOut' };
/** Jelly squash played while a control is pressed. */
export const jellyPress = {
  scaleX: [1, 1.12, 0.94, 1.03, 1],
  scaleY: [1, 0.86, 1.06, 0.98, 1],
};
