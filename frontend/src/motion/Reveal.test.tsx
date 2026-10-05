import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MotionRoot } from './MotionRoot';
import { Reveal, Stagger, StaggerItem } from './Reveal';
import { motionPrefs } from '../test/setup';

describe('reduced-motion reveals', () => {
  it('renders reveal and staggered content without waiting for scroll animation', () => {
    motionPrefs.reducedMotion = true;
    const { container } = render(
      <MotionRoot>
        <Reveal className="motion-reveal">Visible reveal</Reveal>
        <Stagger><StaggerItem className="motion-stagger-item">Visible stagger item</StaggerItem></Stagger>
      </MotionRoot>,
    );

    expect(container.querySelector('.motion-reveal')).not.toHaveStyle({ opacity: '0' });
    expect(container.querySelector('.motion-stagger-item')).not.toHaveStyle({ opacity: '0' });
  });
});
