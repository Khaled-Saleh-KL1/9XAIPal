import { render } from '@testing-library/react';
import { motionValue } from 'motion/react';
import { describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../../../motion';
import { motionPrefs } from '../../../test/setup';
import { PileScene } from './PileScene';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
});

const finalLefts = [0, 13, 26, 39, 52, 65, 78];

function renderPile(progress: number, small = false) {
  const { container } = render(
    <MotionRoot>
      <PileScene progress={motionValue(progress)} persona="student" small={small} />
    </MotionRoot>,
  );
  return Array.from(container.querySelectorAll<HTMLElement>('.pile-document'));
}

describe('PileScene shelf composition', () => {
  it('starts with a broad, rotated scatter across the scene', () => {
    const cards = renderPile(0);
    const lefts = cards.map((card) => Number.parseFloat(card.style.left));
    const rotations = cards.map((card) => Number.parseFloat(card.style.transform.match(/rotate\((-?[\d.]+)deg\)/)?.[1] ?? '0'));

    expect(Math.max(...lefts) - Math.min(...lefts)).toBeGreaterThanOrEqual(60);
    expect(Math.max(...rotations)).toBeGreaterThanOrEqual(12);
    expect(Math.min(...rotations)).toBeLessThanOrEqual(-12);
  });

  it('places desktop cards in an evenly spaced row on the shelf by chapter end', () => {
    const cards = renderPile(1);

    expect(cards).toHaveLength(7);
    expect(cards.map((card) => Number.parseFloat(card.style.left))).toEqual(finalLefts);
    expect(cards.every((card) => card.style.top === '65%')).toBe(true);
  });

  it('places small-screen cards in the shelf row after the one-shot gather', () => {
    const cards = renderPile(1, true);

    expect(cards).toHaveLength(7);
    expect(cards.map((card) => Number.parseFloat(card.style.left))).toEqual(finalLefts);
    expect(cards.every((card) => card.style.top === '62%')).toBe(true);
  });

  it('renders the final shelf pose for reduced motion regardless of scroll progress', () => {
    motionPrefs.reducedMotion = true;
    const cards = renderPile(0);

    expect(cards.map((card) => Number.parseFloat(card.style.left))).toEqual(finalLefts);
    expect(cards.every((card) => card.style.top === '65%')).toBe(true);
  });
});
