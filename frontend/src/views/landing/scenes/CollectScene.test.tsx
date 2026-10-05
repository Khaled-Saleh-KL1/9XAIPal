import { render, screen } from '@testing-library/react';
import { motionValue } from 'motion/react';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from '../../../motion';
import { CollectScene } from './CollectScene';

describe('CollectScene', () => {
  it('changes the side note with the selected persona', () => {
    const progress = motionValue(1);
    const view = render(
      <MotionRoot><CollectScene progress={progress} persona="student" /></MotionRoot>,
    );
    expect(screen.getByText('Check this before the exam')).toBeInTheDocument();

    view.rerender(<MotionRoot><CollectScene progress={progress} persona="researcher" /></MotionRoot>);
    expect(screen.getByText('Check this before citing')).toBeInTheDocument();
  });
});
