import { forwardRef } from 'react';
import type { ComponentPropsWithoutRef, Ref } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { calm, jellyPress, playful } from './springs';

type Intensity = 'playful' | 'calm';
type MotionEventCollisions = 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart';
type NativeMotionHandlers<T extends 'button' | 'a'> = Pick<ComponentPropsWithoutRef<T>, MotionEventCollisions>;
type ButtonProps = {
  as?: 'button';
  intensity?: Intensity;
} & ComponentPropsWithoutRef<'button'> & NativeMotionHandlers<'button'>;
type AnchorProps = {
  as: 'a';
  intensity?: Intensity;
} & ComponentPropsWithoutRef<'a'> & NativeMotionHandlers<'a'>;
export type PressableProps = ButtonProps | AnchorProps;

function PressableImpl(
  props: PressableProps,
  forwardedRef: Ref<HTMLButtonElement | HTMLAnchorElement>,
) {
  const reducedMotion = useReducedMotion();
  const { as = 'button', intensity = 'playful', className, ...rest } = props;
  const disabled = as === 'button' && Boolean((rest as ComponentPropsWithoutRef<'button'>).disabled);
  const canAnimate = !reducedMotion && !disabled;
  const whileHover = canAnimate
    ? intensity === 'playful' ? { y: -3, scale: 1.05, rotate: -1 } : { y: -1 }
    : undefined;
  const whileTap = canAnimate
    ? intensity === 'playful' ? jellyPress : { scale: 0.97 }
    : undefined;
  const motionClassName = ['motion-pressable', className].filter(Boolean).join(' ');

  if (as === 'a') {
    const anchorProps = rest as Omit<ComponentPropsWithoutRef<'a'>, MotionEventCollisions> & NativeMotionHandlers<'a'>;
    const { onDrag, onDragStart, onDragEnd, onAnimationStart, ...anchorRest } = anchorProps;
    return (
      <m.a
        {...anchorRest}
        ref={forwardedRef as Ref<HTMLAnchorElement>}
        className={motionClassName}
        onDragCapture={onDrag}
        onDragStartCapture={onDragStart}
        onDragEndCapture={onDragEnd}
        onAnimationStartCapture={onAnimationStart}
        whileHover={whileHover}
        whileTap={whileTap}
        transition={intensity === 'playful' ? playful : calm}
      />
    );
  }

  const buttonProps = rest as Omit<ComponentPropsWithoutRef<'button'>, MotionEventCollisions> & NativeMotionHandlers<'button'>;
  const { onDrag, onDragStart, onDragEnd, onAnimationStart, ...buttonRest } = buttonProps;
  return (
    <m.button
      {...buttonRest}
      ref={forwardedRef as Ref<HTMLButtonElement>}
      type={buttonProps.type ?? 'button'}
      className={motionClassName}
      onDragCapture={onDrag}
      onDragStartCapture={onDragStart}
      onDragEndCapture={onDragEnd}
      onAnimationStartCapture={onAnimationStart}
      whileHover={whileHover}
      whileTap={whileTap}
      transition={intensity === 'playful' ? playful : calm}
    />
  );
}

export const Pressable = forwardRef(PressableImpl);
Pressable.displayName = 'Pressable';
