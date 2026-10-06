import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    expect(screen.queryByRole('button', { name: /replay/i })).toBeNull();

    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.storm); });
    expect(phase()).toBe('rescue');
    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.rescue); });
    expect(phase()).toBe('relief');
    act(() => { vi.advanceTimersByTime(INTRO_TIMINGS.relief); });
    expect(phase()).toBe('idle');

    expect(window.sessionStorage.getItem(INTRO_PLAYED_KEY)).toBe('1');
    expect(screen.getByRole('button', { name: /replay/i })).toBeInTheDocument();
  });

  it('skips straight to the calm final frame on a second visit in the same tab', () => {
    window.sessionStorage.setItem(INTRO_PLAYED_KEY, '1');
    renderIntro();
    expect(phase()).toBe('idle');
  });

  it('replays the whole film on request', async () => {
    window.sessionStorage.setItem(INTRO_PLAYED_KEY, '1');
    renderIntro();
    await userEvent.click(screen.getByRole('button', { name: /replay/i }));
    expect(phase()).toBe('storm');
  });

  it('shows only the calm final frame, with no replay, under reduced motion', () => {
    motionState.reducedMotion = true;
    renderIntro();
    expect(phase()).toBe('idle');
    expect(screen.queryByRole('button', { name: /replay/i })).toBeNull();
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
