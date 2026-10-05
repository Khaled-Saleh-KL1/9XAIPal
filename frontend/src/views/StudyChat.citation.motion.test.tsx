import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import type { StudyPaper } from '../api';

const mocks = vi.hoisted(() => ({
  getChunk: vi.fn(),
}));

vi.mock('../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api')>(),
  getChunk: mocks.getChunk,
}));

import { StudyChat, type PendingTurn } from './StudyChat';

const pending = (answer: string): PendingTurn => ({
  clientId: 'pending-1',
  question: 'What does the paper say?',
  answer,
  status: null,
  steps: [],
  error: null,
  verifying: false,
});

function chat(answer: string, papers: StudyPaper[] = [{ id: 'paper-1', title: 'Paper One', page_count: 4, status: 'complete', paper: 1 }]) {
  return (
    <MotionRoot>
      <StudyChat
        scopeName="Study"
        papers={papers}
        turns={[]}
        pending={pending(answer)}
        onAsk={vi.fn()}
        onRetry={vi.fn()}
        onClear={vi.fn()}
        conversations={[]}
        conversationId={null}
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

describe('StudyChat citation streaming', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getChunk.mockResolvedValue({ content_markdown: 'The cited passage.' });
  });

  it('keeps an expanded CitationRef open when another answer token arrives', async () => {
    const view = render(chat('A claim [[P1:7]]'));
    const chip = screen.getByRole('button', { name: 'P1:7' });

    fireEvent.click(chip);
    expect(await screen.findByText('The cited passage.')).toBeInTheDocument();

    view.rerender(chat('A claim [[P1:7]] with more detail.'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'P1:7' })).toHaveAttribute('aria-expanded', 'true');
      expect(screen.getByText('The cited passage.')).toBeInTheDocument();
    });
    expect(mocks.getChunk).toHaveBeenCalledTimes(1);
  });

  it('fetches a new passage when the same citation position points to a reordered paper', async () => {
    mocks.getChunk.mockImplementation(async (documentId: string) => ({
      content_markdown: `Passage from ${documentId}`,
    }));
    const view = render(chat('A claim [[P1:7]]'));
    fireEvent.click(screen.getByRole('button', { name: 'P1:7' }));
    expect(await screen.findByText('Passage from paper-1')).toBeInTheDocument();

    view.rerender(chat('A claim [[P1:7]]', [
      { id: 'paper-2', title: 'Paper Two', page_count: 4, status: 'complete', paper: 1 },
    ]));

    const reboundChip = screen.getByRole('button', { name: 'P1:7' });
    expect(reboundChip).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Passage from paper-1')).not.toBeInTheDocument();
    fireEvent.click(reboundChip);
    expect(await screen.findByText('Passage from paper-2')).toBeInTheDocument();
    expect(mocks.getChunk).toHaveBeenLastCalledWith('paper-2', 7);
  });
});
