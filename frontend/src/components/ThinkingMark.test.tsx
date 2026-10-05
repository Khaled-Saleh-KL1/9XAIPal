import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const motionState = vi.hoisted(() => ({
  reducedMotion: false,
  props: [] as Array<Record<string, unknown>>,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const wrappers = new Map<string, React.ComponentType<Record<string, unknown>>>();
  const inspect = (tag: string) => {
    let wrapper = wrappers.get(tag);
    if (!wrapper) {
      wrapper = React.forwardRef<HTMLElement, Record<string, unknown>>((props, ref) => {
        motionState.props.push(props);
        const { animate: _animate, transition: _transition, ...domProps } = props;
        return React.createElement(tag, { ...domProps, ref });
      });
      wrappers.set(tag, wrapper);
    }
    return wrapper;
  };
  const m = new Proxy(actual.m, {
    get(target, key, receiver) {
      if (key === 'div' || key === 'span') return inspect(key);
      return Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m, useReducedMotion: () => motionState.reducedMotion };
});

import { ThinkingMark } from './ThinkingMark';

describe('ThinkingMark', () => {
  beforeEach(() => {
    motionState.reducedMotion = false;
    motionState.props = [];
  });

  it('holds still without animation loop props when reduced motion is enabled', () => {
    motionState.reducedMotion = true;
    render(<ThinkingMark />);

    expect(screen.getByTestId('thinking-mark')).toBeInTheDocument();
    expect(motionState.props.length).toBeGreaterThan(0);
    expect(motionState.props.every((props) => props.animate === undefined && props.transition === undefined)).toBe(true);
  });
});
