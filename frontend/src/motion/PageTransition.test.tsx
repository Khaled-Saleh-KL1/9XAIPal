import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MotionRoot } from './MotionRoot';

const preference = vi.hoisted(() => ({ reduced: false }));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return { ...actual, useReducedMotion: () => preference.reduced };
});

import { PageTransition } from './PageTransition';

describe('PageTransition', () => {
  it('drops the previous route synchronously when the key changes', () => {
    const view = render(
      <MotionRoot>
        <PageTransition routeKey="library"><p>Library screen</p></PageTransition>
      </MotionRoot>,
    );
    expect(screen.getByText('Library screen')).toBeInTheDocument();

    view.rerender(
      <MotionRoot>
        <PageTransition routeKey="desk:library:study"><p>Desk screen</p></PageTransition>
      </MotionRoot>,
    );

    expect(screen.queryByText('Library screen')).not.toBeInTheDocument();
    expect(screen.getByText('Desk screen')).toBeInTheDocument();
  });

  it('omits transform motion for reduced-motion users and fills the viewport', () => {
    preference.reduced = true;
    render(
      <MotionRoot>
        <PageTransition routeKey="library"><p>Library screen</p></PageTransition>
      </MotionRoot>,
    );
    const wrapper = screen.getByTestId('page-transition');
    expect(wrapper).toHaveClass('h-screen');
    expect(wrapper.style.transform).not.toMatch(/translate|rotate|scale/);
    preference.reduced = false;
  });
});
