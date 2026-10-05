import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  props: undefined as Record<string, any> | undefined,
  mountId: 0,
  nextMountId: 0,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const id = React.useRef<number | null>(null);
    if (id.current === null) id.current = ++mocks.nextMountId;
    if (props['data-testid'] === 'pending-note-stream-answer') {
      mocks.props = props;
      mocks.mountId = id.current;
    }
    return React.createElement(actual.m.div, { ...props, ref });
  });
  const inspectedM = new Proxy(actual.m, {
    get(target, key, receiver) {
      return key === 'div' ? InspectableDiv : Reflect.get(target, key, receiver);
    },
  });
  return { ...actual, m: inspectedM, useReducedMotion: () => false };
});

import { PendingNoteCard, type PendingNote } from './NoteCard';

const note = (answer: string): PendingNote => ({
  clientId: 'pending-note-1',
  noteId: null,
  anchorSequenceId: 4,
  anchorKind: 'text',
  quote: 'A quoted paragraph',
  imageUrl: null,
  question: 'Why does this matter?',
  answer,
  status: null,
  steps: [],
  error: null,
  verifying: false,
  parentNoteId: null,
  scope: 'anchor',
  marginSide: 'right',
  model: null,
});

describe('PendingNoteCard streaming motion', () => {
  beforeEach(() => {
    mocks.props = undefined;
    mocks.mountId = 0;
    mocks.nextMountId = 0;
  });

  it('pops in its pending answer once and keeps a streaming caret through token updates', () => {
    const view = render(
      <MotionRoot>
        <PendingNoteCard note={note('First token')} onRetry={vi.fn()} onDismiss={vi.fn()} />
      </MotionRoot>,
    );

    expect(screen.getByText('First token')).toBeInTheDocument();
    expect(mocks.props?.initial).toMatchObject({ scale: 0.6, rotate: -3, y: 12 });
    expect(screen.getByTestId('chat-stream-caret')).toBeInTheDocument();
    const firstMount = mocks.mountId;

    view.rerender(
      <MotionRoot>
        <PendingNoteCard note={note('First token and the next one')} onRetry={vi.fn()} onDismiss={vi.fn()} />
      </MotionRoot>,
    );

    expect(screen.getByText('First token and the next one')).toBeInTheDocument();
    expect(mocks.mountId).toBe(firstMount);
    expect(screen.getByTestId('chat-stream-caret')).toBeInTheDocument();
  });
});
