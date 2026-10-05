import { render } from '@testing-library/react';
import { motionValue } from 'motion/react';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from '../../../motion';
import { PileScene } from './PileScene';

describe('PileScene', () => {
  it('starts with layered cards at varied angles', () => {
    const { container } = render(
      <MotionRoot><PileScene progress={motionValue(0)} persona="student" /></MotionRoot>,
    );
    const cards = Array.from(container.querySelectorAll<HTMLElement>('.pile-document'));
    const depthValues = cards.map((card) => card.style.zIndex);
    const rotations = cards.map((card) => Number(card.style.transform.match(/rotate\((-?[\d.]+)deg\)/)?.[1]));

    expect(cards).toHaveLength(7);
    expect(new Set(depthValues).size).toBe(cards.length);
    expect(rotations.every(Number.isFinite)).toBe(true);
    expect(Math.max(...rotations) - Math.min(...rotations)).toBeGreaterThanOrEqual(14);
  });
});
