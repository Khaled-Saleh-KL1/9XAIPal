import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LandingView } from './LandingView';
import { MotionRoot } from '../motion';
import { motionPrefs } from '../test/setup';

motionPrefs.reducedMotion = true;

describe('Landing reduced motion', () => {
  it('uses a static chapter marker without scroll-scaled progress fills', () => {
    const { container } = render(
      <MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} /></MotionRoot>,
    );

    expect(container.querySelector('.journey-progress-track > span')).toBeNull();
    expect(container.querySelector('.journey-progress-mobile-fill')).toBeNull();
    expect(container.querySelector('.journey-progress-dot[aria-current="step"]')).toBeInTheDocument();
  });
});
