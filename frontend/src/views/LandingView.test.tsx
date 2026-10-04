import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { MotionRoot } from '../motion';
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

  it('has a progress rail with one control per chapter', () => {
    renderLanding();
    const rail = screen.getByRole('navigation', { name: /journey progress/i });
    expect(within(rail).getAllByRole('button')).toHaveLength(CHAPTERS.length);
  });

  it('does not touch the URL hash', () => {
    window.history.replaceState(null, '', '#/paper/abc');
    renderLanding();
    expect(window.location.hash).toBe('#/paper/abc');
  });
});
