import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';
import { PendingNoteCard, type PendingNote } from './NoteCard';

const note: PendingNote = {
  clientId: 'pending-note-1',
  noteId: null,
  anchorSequenceId: 1,
  anchorKind: 'paragraph',
  quote: 'The paper describes the method.',
  imageUrl: null,
  question: 'What does this mean?',
  answer: 'The method compares two models.',
  status: null,
  steps: [],
  error: null,
  verifying: false,
  parentNoteId: null,
  scope: 'anchor',
  marginSide: 'right',
  model: null,
};

describe('NoteCard answer scrolling', () => {
  it('keeps the streamed answer body inside its viewport and leaves the caret outside', () => {
    render(
      <MotionRoot>
        <PendingNoteCard note={note} onRetry={vi.fn()} onDismiss={vi.fn()} />
      </MotionRoot>,
    );

    const viewport = screen.getByTestId('answer-viewport');
    expect(viewport).toHaveTextContent('The method compares two models.');
    expect(viewport).not.toContainElement(screen.getByTestId('chat-stream-caret'));
  });

  it('does not offer stream-follow controls while evidence verification is running', async () => {
    render(
      <MotionRoot>
        <PendingNoteCard
          note={{ ...note, answer: 'A completed long answer awaiting its evidence check.', verifying: true }}
          onRetry={vi.fn()}
          onDismiss={vi.fn()}
        />
      </MotionRoot>,
    );

    const viewport = screen.getByTestId('answer-viewport');
    const content = viewport.querySelector<HTMLElement>('[data-answer-content]');
    const scroll = viewport.querySelector<HTMLElement>('[data-answer-scroll]');
    if (!content || !scroll) throw new Error('answer viewport is missing its content or scroll region');
    Object.defineProperty(content, 'scrollHeight', { configurable: true, value: 900 });
    Object.defineProperty(scroll, 'scrollHeight', { configurable: true, value: 900 });
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 300 });
    fireEvent.resize(window);

    await waitFor(() => expect(scroll).toHaveClass('is-scrollable'));
    act(() => {
      scroll.scrollTop = 40;
      fireEvent.scroll(scroll);
    });

    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument();
  });
});
