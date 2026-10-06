import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StudyPaper, StudyTurn } from '../api';
import { MotionRoot } from '../motion';

const citationMocks = vi.hoisted(() => ({ getChunk: vi.fn() }));

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  getChunk: citationMocks.getChunk,
}));

vi.mock('motion/react', async (importOriginal) => ({
  ...await importOriginal<typeof import('motion/react')>(),
  useReducedMotion: () => false,
}));

import { StudyChat, type PendingTurn } from './StudyChat';

const papers: StudyPaper[] = [
  { id: 'paper-1', title: 'Paper One', page_count: 4, status: 'complete', paper: 1 },
];

const turn = (content: string): StudyTurn => ({
  id: 'answer-1',
  role: 'assistant',
  content,
  model: null,
  cited: [],
  agent_steps: [],
  grounding: null,
  created_at: null,
});

const pending = (answer: string): PendingTurn => ({
  clientId: 'pending-1',
  question: 'What does the paper say?',
  answer,
  status: null,
  steps: [],
  error: null,
  verifying: false,
});

function chat(answer: string, streaming = false) {
  return (
    <MotionRoot>
      <StudyChat
        scopeName="Study"
        papers={papers}
        turns={streaming ? [] : [turn(answer)]}
        pending={streaming ? pending(answer) : null}
        onAsk={vi.fn()}
        onRetry={vi.fn()}
        onClear={vi.fn()}
        conversations={[]}
        conversationId="conversation-1"
        onSelectConversation={vi.fn()}
        onNewChat={vi.fn()}
        onOpenPaper={vi.fn()}
        catalog={null}
        model="local"
        onModelChange={vi.fn()}
      />
    </MotionRoot>
  );
}

function setAnswerHeight(height: number) {
  const shell = screen.getByTestId('answer-viewport');
  const content = shell.querySelector<HTMLElement>('[data-answer-content]');
  const scroll = shell.querySelector<HTMLElement>('[data-answer-scroll]');
  if (!content || !scroll) throw new Error('answer viewport is missing its content or scroll region');
  Object.defineProperty(content, 'scrollHeight', { configurable: true, value: height });
  Object.defineProperty(scroll, 'scrollHeight', { configurable: true, value: height });
  Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 300 });
  fireEvent.resize(window);
  return scroll;
}

afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  citationMocks.getChunk.mockResolvedValue({ content_markdown: 'Quoted passage.' });
});

describe('StudyChat answer scrolling', () => {
  it('leaves a short answer without the scroll treatment', async () => {
    render(chat('A short answer.'));

    setAnswerHeight(120);

    await waitFor(() => {
      expect(screen.getByTestId('answer-viewport').querySelector('[data-answer-scroll]'))
        .not.toHaveClass('is-scrollable');
    });
    expect(screen.queryByRole('button', { name: 'Expand' })).not.toBeInTheDocument();
  });

  it('contains a long answer and expands it without the height cap', async () => {
    render(chat('A long answer about the research.'));

    const scroll = setAnswerHeight(1000);
    await waitFor(() => expect(scroll).toHaveClass('is-scrollable'));

    const expand = screen.getByRole('button', { name: 'Expand' });
    const fadeFrame = scroll.closest('.answer-scroll-frame');
    expect(fadeFrame).not.toBeNull();
    expect(fadeFrame).toContainElement(scroll);
    expect(fadeFrame).not.toContainElement(expand);
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);

    const collapse = screen.getByRole('button', { name: 'Collapse' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    expect(scroll).not.toHaveClass('is-scrollable');
  });

  it('keeps long-answer citation controls outside the scroll region and linked to their mention', async () => {
    render(chat('A claim [[P1:41]].'));

    const scroll = setAnswerHeight(1000);
    await waitFor(() => expect(scroll).toHaveClass('is-scrollable'));

    const chip = screen.getByRole('button', { name: 'P1:41' });
    expect(scroll).not.toContainElement(chip);

    const marker = scroll.querySelector<HTMLElement>('[data-answer-citation-marker="P1:41"]');
    expect(marker).toHaveTextContent('P1:41');
    Object.defineProperty(scroll, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ top: 100 }),
    });
    Object.defineProperty(marker, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ top: 500 }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Show P1:41 in answer' }));
    expect(scroll.scrollTop).toBe(388);

    fireEvent.click(chip);
    expect(await screen.findByText('Quoted passage.')).toBeInTheDocument();
    expect(scroll).not.toContainElement(screen.getByText('Quoted passage.').closest('.cite-peek'));
  });

  it('keeps an opened citation preview from making a short answer overflow', async () => {
    render(chat('A short claim [[P1:41]].'));

    const shell = screen.getByTestId('answer-viewport');
    const content = shell.querySelector<HTMLElement>('[data-answer-content]');
    const scroll = shell.querySelector<HTMLElement>('[data-answer-scroll]');
    if (!content || !scroll) throw new Error('answer viewport is missing its content or scroll region');
    const measuredHeight = () => {
      const preview = content.querySelector<HTMLElement>('.cite-peek');
      return preview && preview.style.display !== 'none' ? 1000 : 120;
    };
    Object.defineProperty(content, 'scrollHeight', { configurable: true, get: measuredHeight });
    Object.defineProperty(scroll, 'scrollHeight', { configurable: true, get: measuredHeight });
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 300 });
    fireEvent.resize(window);

    const chip = screen.getByRole('button', { name: 'P1:41' });
    fireEvent.click(chip);
    expect(await screen.findByText('Quoted passage.')).toBeInTheDocument();

    fireEvent.resize(window);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'P1:41' })).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByText('Quoted passage.')).toBeInTheDocument();
    });
    expect(scroll).not.toHaveClass('is-scrollable');
    expect(screen.queryByRole('button', { name: 'Expand' })).not.toBeInTheDocument();
    expect(citationMocks.getChunk).toHaveBeenCalledTimes(1);
  });

  it('keeps an Arabic answer in right-to-left direction', () => {
    render(chat('توضح النتائج أن النموذج تحسن مع زيادة البيانات.'));

    expect(screen.getByTestId('answer-viewport').querySelector('[data-answer-scroll]'))
      .toHaveAttribute('dir', 'rtl');
  });

  it('follows streamed text until the reader scrolls up, then offers Jump to latest', async () => {
    const view = render(chat('First part of a long answer.', true));
    const scroll = setAnswerHeight(1000);
    await waitFor(() => expect(scroll).toHaveClass('is-scrollable'));

    act(() => {
      scroll.scrollTop = 700;
      fireEvent.scroll(scroll);
    });
    view.rerender(chat('First part of a long answer.\n\nMore streamed detail.', true));
    setAnswerHeight(1200);
    await waitFor(() => expect(scroll.scrollTop).toBe(1200));

    act(() => {
      scroll.scrollTop = 80;
      fireEvent.scroll(scroll);
    });
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeInTheDocument();

    view.rerender(chat('First part of a long answer.\n\nMore streamed detail.\n\nNewest detail.', true));
    setAnswerHeight(1400);
    await waitFor(() => expect(scroll.scrollTop).toBe(80));
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }));
    expect(scroll.scrollTop).toBe(1400);
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument();
  });
});
