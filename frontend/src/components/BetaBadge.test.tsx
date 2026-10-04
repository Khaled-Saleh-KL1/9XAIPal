import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { it, expect } from 'vitest';
import { MotionRoot } from '../motion';
import { BETA_NOTE } from '../landing/content';
import { BetaBadge } from './BetaBadge';

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
