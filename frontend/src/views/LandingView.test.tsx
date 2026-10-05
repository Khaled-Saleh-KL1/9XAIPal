import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { motionPrefs } from '../test/setup';
import { LandingView } from './LandingView';
import { LINKS, BETA_NOTE, CHAPTERS, PERSONAS } from '../landing/content';

const renderLanding = (props: Partial<Parameters<typeof LandingView>[0]> = {}) =>
  render(<MotionRoot><LandingView signedIn={false} onRequestAuth={vi.fn()} onOpenLibrary={vi.fn()} {...props} /></MotionRoot>);

describe('LandingView', () => {
  it('shows the hero, all five chapters, the beta note and the author', () => {
    renderLanding();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Read deeper. Ask your library.');
    for (const chapter of CHAPTERS) expect(screen.getByRole('heading', { name: chapter.title })).toBeInTheDocument();
    expect(screen.getAllByText(BETA_NOTE).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: 'Khaled Saleh' })).toBeInTheDocument();
  });

  it('links to the repo and the portfolio in new tabs, safely', () => {
    renderLanding();
    const repoLinks = screen.getAllByRole('link').filter((link) => link.getAttribute('href') === LINKS.repo);
    const portfolioLinks = screen.getAllByRole('link').filter((link) => link.getAttribute('href') === LINKS.portfolio);
    expect(repoLinks.length).toBeGreaterThanOrEqual(2);
    expect(portfolioLinks.length).toBeGreaterThanOrEqual(1);
    for (const link of [...repoLinks, ...portfolioLinks]) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link.getAttribute('rel')).toContain('noopener');
    }
  });

  it('asks for signup from the primary CTA and login from the top bar', async () => {
    const onRequestAuth = vi.fn();
    renderLanding({ onRequestAuth });
    await userEvent.click(screen.getAllByRole('button', { name: /try the free beta/i })[0]);
    expect(onRequestAuth).toHaveBeenLastCalledWith('signup', expect.any(HTMLElement));
    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /sign in/i }));
    expect(onRequestAuth).toHaveBeenLastCalledWith('login', expect.any(HTMLElement));
  });

  it('offers "Open your library" instead of sign-in when signed in', async () => {
    const onOpenLibrary = vi.fn();
    renderLanding({ signedIn: true, onOpenLibrary });
    expect(within(screen.getByRole('banner')).queryByRole('button', { name: /sign in/i })).toBeNull();
    await userEvent.click(screen.getAllByRole('button', { name: /open your library/i })[0]);
    expect(onOpenLibrary).toHaveBeenCalled();
  });

  it('switches the example content between student and researcher', async () => {
    renderLanding();
    expect(screen.getAllByText(PERSONAS.student.question).length).toBeGreaterThan(0);
    expect(screen.getByText(PERSONAS.student.goal)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: PERSONAS.researcher.label }));
    expect((await screen.findAllByText(PERSONAS.researcher.question)).length).toBeGreaterThan(0);
    expect(await screen.findByText(PERSONAS.researcher.goal)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(PERSONAS.student.question)).toBeNull());
  });

  it('shows each persona Arabic filename in right-to-left isolates in both scenes', async () => {
    const { container } = renderLanding();
    expect(container.querySelector('.pile-document-title bdi[dir="rtl"]')).toHaveTextContent('امتحان سابق ٢٠٢٥.pdf');
    expect(container.querySelector('.find-result-title bdi[dir="rtl"]')).toHaveTextContent('امتحان سابق ٢٠٢٥.pdf');

    await userEvent.click(screen.getByRole('radio', { name: PERSONAS.researcher.label }));
    await waitFor(() => {
      expect(container.querySelector('.pile-document-title bdi[dir="rtl"]')).toHaveTextContent('التعرف الضوئي على الحروف العربية.pdf');
      expect(container.querySelector('.find-result-title bdi[dir="rtl"]')).toHaveTextContent('التعرف الضوئي على الحروف العربية.pdf');
    });
  });

  it('includes RAG in the author bio', () => {
    renderLanding();
    expect(screen.getByText(/RAG/i)).toBeInTheDocument();
  });

  it('uses the visible brand text as the brand button accessible name', () => {
    renderLanding();
    expect(screen.getByRole('button', { name: /9XAIPal/ })).toHaveAccessibleName('9 9XAIPal');
  });

  it('has a progress rail with one control per chapter after its entrance fade', async () => {
    const { container } = renderLanding();
    const rail = container.querySelector<HTMLElement>('.journey-progress')!;
    await waitFor(() => expect(rail).toHaveAttribute('aria-hidden', 'false'));
    expect(within(rail).getAllByRole('button')).toHaveLength(CHAPTERS.length);
  });

  it('keeps hidden progress buttons out of the tab order', async () => {
    class HiddenIntersectionObserver {
      constructor(private callback: IntersectionObserverCallback, _options?: IntersectionObserverInit) {}
      observe(target: Element) {
        this.callback([{ isIntersecting: false, target, intersectionRatio: 0 } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
      }
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    }

    vi.stubGlobal('IntersectionObserver', HiddenIntersectionObserver);
    try {
      const { container } = renderLanding();
      const rail = container.querySelector('.journey-progress')!;
      const railButtons = Array.from(rail.querySelectorAll('button'));
      expect(rail).toHaveAttribute('aria-hidden', 'true');
      expect(rail).toHaveAttribute('inert');
      expect(railButtons).toHaveLength(CHAPTERS.length);
      expect(railButtons.every((button) => button.tabIndex === -1)).toBe(true);

      const user = userEvent.setup();
      for (let index = 0; index < 30; index += 1) {
        await user.tab();
        expect(rail.contains(document.activeElement)).toBe(false);
      }
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('waits for the pile scene to be half visible before gathering on small screens', async () => {
    const observed: Array<{
      target: Element;
      threshold: number;
      trigger: (ratio: number) => void;
    }> = [];
    class ControlledIntersectionObserver {
      constructor(private callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        this.threshold = typeof options?.threshold === 'number' ? options.threshold : 0;
      }

      private threshold: number;

      observe(target: Element) {
        observed.push({
          target,
          threshold: this.threshold,
          trigger: (ratio) => this.callback([{
            isIntersecting: ratio > 0,
            intersectionRatio: ratio,
            target,
          } as IntersectionObserverEntry], this as unknown as IntersectionObserver),
        });
      }

      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    }

    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 899px)' || (query.includes('prefers-reduced-motion') && motionPrefs.reducedMotion),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal('IntersectionObserver', ControlledIntersectionObserver);

    try {
      const { container } = renderLanding();
      const chapter = container.querySelector('#chapter-pile')!;
      const scene = container.querySelector('.pile-scene')!;
      const chapterObserver = observed.find(({ target }) => target === chapter);
      const sceneObserver = observed.find(({ target }) => target === scene);

      expect(sceneObserver).toBeDefined();
      expect(sceneObserver?.threshold).toBe(0.5);
      expect(chapterObserver).toBeDefined();

      act(() => chapterObserver!.trigger(1));
      expect(Array.from(scene.querySelectorAll<HTMLElement>('.pile-document')).map((card) => Number.parseFloat(card.style.left)))
        .not.toEqual([0, 13, 26, 39, 52, 65, 78]);

      act(() => sceneObserver!.trigger(0.5));
      await waitFor(() => {
        expect(Array.from(scene.querySelectorAll<HTMLElement>('.pile-document')).map((card) => Number.parseFloat(card.style.left)))
          .toEqual([0, 13, 26, 39, 52, 65, 78]);
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps the progress rail hidden and inert until its entrance fade completes', async () => {
    const observers: Array<{
      target: Element | null;
      trigger: (isIntersecting: boolean) => void;
    }> = [];
    class ControlledIntersectionObserver {
      private entry: (typeof observers)[number];
      constructor(callback: IntersectionObserverCallback) {
        this.entry = {
          target: null,
          trigger: (isIntersecting) => {
            if (!this.entry.target) return;
            callback([{ isIntersecting, target: this.entry.target, intersectionRatio: isIntersecting ? 1 : 0 } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
          },
        };
        observers.push(this.entry);
      }
      observe(target: Element) { this.entry.target = target; }
      unobserve() {}
      disconnect() {}
      takeRecords() { return []; }
    }

    vi.stubGlobal('IntersectionObserver', ControlledIntersectionObserver);
    try {
      const { container } = renderLanding();
      const rail = container.querySelector('.journey-progress')!;
      const controls = Array.from(rail.querySelectorAll('button'));
      const journeyObserver = observers.find(({ target }) => target?.id === 'journey');

      expect(rail).toHaveAttribute('aria-hidden', 'true');
      expect(rail).toHaveAttribute('inert');
      expect(controls.every((button) => button.tabIndex === -1)).toBe(true);
      expect(journeyObserver).toBeDefined();

      act(() => journeyObserver!.trigger(true));

      expect(rail).toHaveAttribute('aria-hidden', 'true');
      expect(rail).toHaveAttribute('inert');
      expect(controls.every((button) => button.tabIndex === -1)).toBe(true);
      await waitFor(() => expect(rail).toHaveAttribute('aria-hidden', 'false'));
      expect(rail).not.toHaveAttribute('inert');
      expect(controls.every((button) => button.tabIndex === 0)).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('does not touch the URL hash', () => {
    window.history.replaceState(null, '', '#/paper/abc');
    renderLanding();
    expect(window.location.hash).toBe('#/paper/abc');
  });
});
