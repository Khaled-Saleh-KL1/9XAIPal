import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';

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

function chat(answer: string) {
  return (
    <MotionRoot>
      <StudyChat
        scopeName="Study"
        papers={[{ id: 'paper-1', title: 'Paper One', page_count: 4, status: 'complete', paper: 1 }]}
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
});
