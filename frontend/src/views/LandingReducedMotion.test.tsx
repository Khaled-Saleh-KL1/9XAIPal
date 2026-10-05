import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { LandingView } from './LandingView';
import { MotionRoot } from '../motion';
import { motionPrefs } from '../test/setup';
import { CHAPTERS, NAVIGATION } from '../landing/content';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return {
    ...actual,
    useReducedMotion: () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
});

function stubScrollIntoView() {
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView');
  const scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
  return {
    scrollIntoView,
    restore() {
      if (original) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', original);
      else delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
    },
  };
}

describe('Landing reduced motion', () => {
  it('uses a static chapter marker without scroll-scaled progress fills', () => {
    motionPrefs.reducedMotion = true;
    const { container } = render(
      <MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} /></MotionRoot>,
    );

    expect(container.querySelector('.journey-progress-track > span')).toBeNull();
    expect(container.querySelector('.journey-progress-mobile-fill')).toBeNull();
    expect(container.querySelector('.journey-progress-dot[aria-current="step"]')).toBeInTheDocument();
  });

  it('renders the pile in its shelf pose with reduced motion', () => {
    const originalMatchMedia = Object.getOwnPropertyDescriptor(window, 'matchMedia');
    motionPrefs.reducedMotion = true;
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: query.includes('max-width: 899px') || (query.includes('prefers-reduced-motion') && motionPrefs.reducedMotion),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });

    try {
      const { container } = render(
        <MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} /></MotionRoot>,
      );
      const cards = Array.from(container.querySelectorAll<HTMLElement>('.pile-document'));
      expect(cards).toHaveLength(7);
      expect(cards.map((card) => Number.parseFloat(card.style.left))).toEqual([0, 13, 26, 39, 52, 65, 78]);
      expect(cards.every((card) => card.style.top === '62%')).toBe(true);
    } finally {
      if (originalMatchMedia) Object.defineProperty(window, 'matchMedia', originalMatchMedia);
    }
  });

  it('uses an instant jump for chapter controls when reduced motion is requested', async () => {
    motionPrefs.reducedMotion = true;
    const scroll = stubScrollIntoView();

    try {
      render(<MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} /></MotionRoot>);
      await userEvent.click(await screen.findByRole('button', { name: CHAPTERS[CHAPTERS.length - 1].label }));

      expect(scroll.scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'center' });
    } finally {
      scroll.restore();
    }
  });

  it('uses an instant jump for landing navigation when reduced motion is requested', async () => {
    motionPrefs.reducedMotion = true;
    const scroll = stubScrollIntoView();

    try {
      render(<MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} /></MotionRoot>);
      await userEvent.click(screen.getByRole('button', { name: NAVIGATION.journey }));

      expect(scroll.scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' });
    } finally {
      scroll.restore();
    }
  });
});
