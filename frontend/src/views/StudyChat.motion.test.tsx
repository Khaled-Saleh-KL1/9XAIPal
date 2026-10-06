import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { StudyPaper, StudyTurn } from '../api';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  rows: new Map<string, { props: Record<string, any>; mountId: number }>(),
  nextMountId: 0,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const mountId = React.useRef<number | null>(null);
    if (mountId.current === null) mountId.current = ++mocks.nextMountId;
    const key = props['data-study-message-key'];
    if (props['data-testid'] === 'study-message-motion' && typeof key === 'string') {
      mocks.rows.set(key, { props, mountId: mountId.current });
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

import { StudyChat, type PendingTurn } from './StudyChat';

const papers: StudyPaper[] = [
  { id: 'paper-1', title: 'Paper One', page_count: 4, status: 'complete', paper: 1 },
];

const turn = (id: string, role: StudyTurn['role'], content: string): StudyTurn => ({
  id,
  role,
  content,
  model: null,
  cited: [],
  agent_steps: [],
  grounding: null,
  created_at: null,
});

const pending = (answer: string, verifying = false, notice?: string): PendingTurn => ({
  clientId: 'pending-1',
  question: 'New question',
  answer,
  status: null,
  steps: [],
  error: null,
  notice,
  verifying,
});

function chat(turns: StudyTurn[], pendingTurn: PendingTurn | null) {
  return (
    <MotionRoot>
      <StudyChat
        scopeName="Study"
        papers={papers}
        turns={turns}
        pending={pendingTurn}
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

describe('StudyChat message motion', () => {
  beforeEach(() => {
    mocks.rows.clear();
    mocks.nextMountId = 0;
  });

  it('pops pending user and assistant bubbles once, keeps the caret while streaming, and does not replay history or committed turns', () => {
    const history = [turn('history-user', 'user', 'Old question'), turn('history-answer', 'assistant', 'Old answer')];
    const view = render(chat(history, null));
    expect(mocks.rows.get('history-user')?.props.initial).toBe(false);
    expect(mocks.rows.get('history-answer')?.props.initial).toBe(false);

    view.rerender(chat(history, pending('')));
    const pendingUser = mocks.rows.get('pending-1-user');
    const pendingAnswer = mocks.rows.get('pending-1-assistant');
    expect(pendingUser?.props.initial).toMatchObject({ scale: 0.6, rotate: -3, y: 12 });
    expect(pendingAnswer?.props.initial).toMatchObject({ scale: 0.6, rotate: -3, y: 12 });
    const answerMount = pendingAnswer?.mountId;

    view.rerender(chat(history, pending('Draft answer')));
    expect(mocks.rows.get('pending-1-assistant')?.mountId).toBe(answerMount);
    expect(screen.getByTestId('chat-stream-caret')).toBeInTheDocument();

    view.rerender(chat(history, pending('Complete answer', true)));
    expect(screen.queryByTestId('chat-stream-caret')).not.toBeInTheDocument();

    const committed = [...history, turn('new-user', 'user', 'New question'), turn('new-answer', 'assistant', 'Complete answer')];
    view.rerender(chat(committed, null));
    expect(mocks.rows.get('new-user')?.props.initial).toBe(false);
    expect(mocks.rows.get('new-answer')?.props.initial).toBe(false);
    expect(screen.queryByTestId('chat-stream-caret')).not.toBeInTheDocument();
  });

  it('shows a fallback notice alongside a pending study answer', () => {
    const message = "GLM 5.3 Flash isn't available on the current plan, so Gemma 4 31B answered instead.";
    render(chat([], pending('Answer in progress', false, message)));

    expect(screen.getByRole('status')).toHaveTextContent(message);
  });
});
