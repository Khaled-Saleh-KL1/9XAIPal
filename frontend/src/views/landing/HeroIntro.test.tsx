import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../../motion';

// motion caches the OS preference after its first read, so the tests drive
// useReducedMotion directly, the same way ThinkingMark.test does.
const motionState = vi.hoisted(() => ({ reducedMotion: false }));
vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return { ...actual, useReducedMotion: () => motionState.reducedMotion };
});
import { HERO } from '../../landing/content';
import { HeroIntro, INTRO_PLAYED_KEY, INTRO_TIMINGS } from './HeroIntro';

const renderIntro = () => render(<MotionRoot><HeroIntro /></MotionRoot>);
const phase = () => document.querySelector('.hero-intro')?.getAttribute('data-phase');

beforeEach(() => {
  window.sessionStorage.clear();
  motionState.reducedMotion = false;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('HeroIntro', () => {
  it('describes the film for screen readers', () => {
    renderIntro();
    expect(screen.getByRole('img', { name: HERO.introDescription })).toBeInTheDocument();
  });

  it('plays storm, rescue and relief once, then settles into the idle loop', () => {
    vi.useFakeTimers();
    renderIntro();
    expect(phase()).toBe('storm');

    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.storm); });
    expect(phase()).toBe('rescue');
    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.rescue); });
    expect(phase()).toBe('relief');
    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.relief); });
    expect(phase()).toBe('idle');

    expect(window.sessionStorage.getItem(INTRO_PLAYED_KEY)).toBe('1');
    // Nothing appears when the film ends: a late control shifted the centred scene.
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('skips straight to the calm final frame on a second visit in the same tab', () => {
    window.sessionStorage.setItem(INTRO_PLAYED_KEY, '1');
    renderIntro();
    expect(phase()).toBe('idle');
  });

  it('shows only the calm final frame under reduced motion', () => {
    motionState.reducedMotion = true;
    renderIntro();
    expect(phase()).toBe('idle');
  });

  it('still plays when session storage is unavailable', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderIntro();
    expect(phase()).toBe('storm');
    getItem.mockRestore();
  });
});
