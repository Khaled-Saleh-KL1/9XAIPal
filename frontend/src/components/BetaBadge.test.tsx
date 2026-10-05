import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { reducedMotionFade } from '../motion/springs';
import { motionPrefs } from '../test/setup';
import { BETA_NOTE } from '../landing/content';
import { BetaBadge } from './BetaBadge';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
});

it('shows the beta note on hover and keyboard focus, and hides on Escape', async () => {
  render(<MotionRoot><BetaBadge /></MotionRoot>);
  const badge = screen.getByRole('button', { name: /beta/i });
  await userEvent.hover(badge);
  expect(await screen.findByRole('tooltip')).toHaveTextContent(BETA_NOTE);
  await userEvent.unhover(badge);
  badge.focus();
  expect(await screen.findByRole('tooltip')).toBeInTheDocument();
  await userEvent.keyboard('{Escape}');
  await expect.poll(() => screen.queryByRole('tooltip')).toBeNull();
});

it('caps reduced-motion badge and tooltip fades at 150 ms', async () => {
  motionPrefs.reducedMotion = true;
  render(<MotionRoot><BetaBadge /></MotionRoot>);
  const badge = screen.getByRole('button', { name: /beta/i });
  await userEvent.hover(badge);
  const tooltip = await screen.findByRole('tooltip');
  expect(tooltip).toBeInTheDocument();
  expect(badge.style.transform).not.toMatch(/scale/);
  expect(tooltip.style.transform).not.toMatch(/translateY/);
  expect(reducedMotionFade.duration).toBeLessThanOrEqual(0.15);
});
