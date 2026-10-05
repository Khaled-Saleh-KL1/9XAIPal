import { useCallback, useRef } from 'react';
import { useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react';
import { gentle } from './springs';

export function useTilt<T extends HTMLElement = HTMLDivElement>(maxDeg = 8) {
  const ref = useRef<T>(null);
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const reducedMotion = useReducedMotion();
  const rotateY = useSpring(useTransform(pointerX, [-0.5, 0.5], [-maxDeg, maxDeg]), gentle);
  const rotateX = useSpring(useTransform(pointerY, [-0.5, 0.5], [maxDeg, -maxDeg]), gentle);

  const onPointerMove = useCallback((event: React.PointerEvent<T>) => {
    if (reducedMotion || event.pointerType !== 'mouse' || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    pointerX.set((event.clientX - rect.left) / rect.width - 0.5);
    pointerY.set((event.clientY - rect.top) / rect.height - 0.5);
  }, [pointerX, pointerY, reducedMotion]);

  const onPointerLeave = useCallback(() => {
    pointerX.set(0);
    pointerY.set(0);
  }, [pointerX, pointerY]);

  return {
    ref,
    style: { rotateX, rotateY, transformPerspective: 900 },
    onPointerMove,
    onPointerLeave,
  };
}
