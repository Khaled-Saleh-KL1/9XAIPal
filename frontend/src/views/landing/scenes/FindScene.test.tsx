import { render } from '@testing-library/react';
import { motionValue } from 'motion/react';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from '../../../motion';
import { FindScene } from './FindScene';

describe('FindScene', () => {
  it('keeps unmatched result text undimmed and marks it with non-text styling', () => {
    const { container } = render(
      <MotionRoot><FindScene progress={motionValue(1)} persona="student" /></MotionRoot>,
    );

    const unmatched = container.querySelector('.find-result:not(.is-match)');
    expect(unmatched).toHaveClass('is-unmatched');
    expect(unmatched).not.toHaveStyle({ opacity: '0.35' });
  });
});
