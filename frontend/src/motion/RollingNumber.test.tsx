import { render, screen } from '@testing-library/react';
import { it, expect } from 'vitest';
import { MotionRoot } from './MotionRoot';
import { RollingNumber } from './RollingNumber';

it('exposes the current value to assistive technology after a rerender', () => {
  const { rerender } = render(<MotionRoot><RollingNumber value={12} /></MotionRoot>);
  expect(screen.getByRole('status')).toHaveTextContent('12');
  rerender(<MotionRoot><RollingNumber value={5} /></MotionRoot>);
  expect(screen.getByRole('status')).toHaveTextContent('5');
});
