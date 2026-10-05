import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  listStudies: vi.fn(),
  listPapers: vi.fn(),
  listModels: vi.fn(),
  getStudy: vi.fn(),
  getStudyChat: vi.fn(),
  listStickies: vi.fn(),
  listStudyConversations: vi.fn(),
}));

vi.mock('../api', () => ({
  LIBRARY_SCOPE: 'library',
  listStudies: mocks.listStudies,
  listPapers: mocks.listPapers,
  listModels: mocks.listModels,
  getStudy: mocks.getStudy,
  getStudyChat: mocks.getStudyChat,
  listStickies: mocks.listStickies,
  listStudyConversations: mocks.listStudyConversations,
}));
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => vi.fn() }));
vi.mock('../components/UserMenu', () => ({ UserMenuInline: () => null }));
vi.mock('./NoteWall', () => ({ NoteWall: () => null }));
vi.mock('./PaperPicker', () => ({ PaperPicker: () => null }));
vi.mock('./StudyChat', () => ({ StudyChat: () => null }));

import { DeskView } from './DeskView';

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => {
      const maxWidth = query.match(/max-width:\s*(\d+)px/)?.[1];
      return {
        matches: maxWidth ? width <= Number(maxWidth) : false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      };
    },
  });
}

describe('DeskView mobile rail motion', () => {
  beforeEach(() => {
    mocks.listStudies.mockResolvedValue([]);
    mocks.listPapers.mockResolvedValue([]);
    mocks.listModels.mockResolvedValue({ models: [] });
    mocks.getStudy.mockResolvedValue({
      study: { id: 'fixture-study', name: 'Fixture study', description: null, paper_count: 0, created_at: null, updated_at: null },
      papers: [],
    });
    mocks.getStudyChat.mockResolvedValue({ conversation_id: null, turns: [] });
    mocks.listStickies.mockResolvedValue([]);
    mocks.listStudyConversations.mockResolvedValue([]);
    localStorage.removeItem('pal:deskBoardHidden');
    setViewportWidth(1440);
  });

  afterEach(() => {
    Reflect.deleteProperty(window, 'matchMedia');
  });

  it('keeps the desktop study rail interactive when the mobile drawer is closed', () => {
    const { container } = render(
      <MotionRoot>
        <DeskView page="study" initialScope="library" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const rail = container.querySelector('.rail');
    expect(rail).not.toBeNull();
    expect(rail).not.toHaveAttribute('inert');
    expect(rail).not.toHaveAttribute('aria-hidden', 'true');
  });

  it('makes a closed mobile drawer inert until the reader opens it', () => {
    setViewportWidth(360);
    const { container } = render(
      <MotionRoot>
        <DeskView page="study" initialScope="library" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const rail = container.querySelector('.rail');
    expect(rail).toHaveAttribute('inert');
    expect(rail).toHaveAttribute('aria-hidden', 'true');
  });

  it.each([360, 768, 1024])('opens the chat StickyBoard at %ipx', (width) => {
    setViewportWidth(width);
    render(
      <MotionRoot>
        <DeskView page="study" initialScope="fixture-study" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const opener = screen.getByRole('button', { name: 'Chat notes' });
    expect(opener).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(opener);

    expect(opener).toHaveAttribute('aria-expanded', 'true');
    expect(opener).toHaveAttribute('aria-controls', 'chat-notes-drawer');
    const drawer = screen.getByRole('dialog', { name: 'Chat notes' });
    expect(drawer.querySelector('.board')).toBeInTheDocument();
    expect(screen.getByTitle('New note')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide the notes' })).toBeInTheDocument();
  });

  it('closes the chat StickyBoard drawer on Escape and restores focus', () => {
    setViewportWidth(360);
    render(
      <MotionRoot>
        <DeskView page="study" initialScope="fixture-study" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    const opener = screen.getByRole('button', { name: 'Chat notes' });
    fireEvent.click(opener);
    const drawer = screen.getByRole('dialog', { name: 'Chat notes' });

    fireEvent.keyDown(drawer, { key: 'Escape' });

    expect(screen.queryByRole('dialog', { name: 'Chat notes' })).not.toBeInTheDocument();
    expect(opener).toHaveAttribute('aria-expanded', 'false');
    expect(opener).toHaveFocus();
  });

  it('closes the chat StickyBoard drawer when its backdrop is clicked', () => {
    setViewportWidth(768);
    render(
      <MotionRoot>
        <DeskView page="study" initialScope="fixture-study" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Chat notes' }));
    expect(screen.getByRole('dialog', { name: 'Chat notes' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close chat notes backdrop' }));

    expect(screen.queryByRole('dialog', { name: 'Chat notes' })).not.toBeInTheDocument();
  });

  it('keeps the StickyBoard in the desktop grid without a drawer control', () => {
    setViewportWidth(1440);
    const { container } = render(
      <MotionRoot>
        <DeskView page="study" initialScope="fixture-study" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    expect(screen.queryByRole('button', { name: 'Chat notes' })).not.toBeInTheDocument();
    expect(container.querySelector('.desk-grid > .board')).toBeInTheDocument();
  });

  it('does not show the chat notes drawer control on the universal Notes page', () => {
    setViewportWidth(360);
    render(
      <MotionRoot>
        <DeskView page="notes" initialScope="fixture-study" onPageChange={() => {}} onBack={() => {}} onOpenPaper={() => {}} />
      </MotionRoot>,
    );

    expect(screen.queryByRole('button', { name: 'Chat notes' })).not.toBeInTheDocument();
  });
});
