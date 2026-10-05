import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MotionRoot } from '../motion';

const mocks = vi.hoisted(() => ({
  askPaperStream: vi.fn(),
  getPaperChat: vi.fn(),
  listPaperConversations: vi.fn(),
  motionRows: new Map<string, { props: Record<string, any>; mountId: number }>(),
  nextMountId: 0,
  resolveAsk: undefined as ((value: unknown) => void) | undefined,
  resolveHistory: undefined as ((value: unknown) => void) | undefined,
}));

vi.mock('../api', () => ({
  askPaperStream: mocks.askPaperStream,
  getPaperChat: mocks.getPaperChat,
  listPaperConversations: mocks.listPaperConversations,
}));

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  const React = await import('react');
  const InspectableDiv = React.forwardRef<HTMLDivElement, Record<string, any>>((props, ref) => {
    const mountId = React.useRef<number | null>(null);
    if (mountId.current === null) mountId.current = ++mocks.nextMountId;
    const key = props['data-motion-key'];
    if (props['data-testid'] === 'chat-message-motion' && typeof key === 'string') {
      mocks.motionRows.set(key, { props, mountId: mountId.current });
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

import { ChatPane } from './ChatPane';

const history = {
  turns: [
    { id: 'old-user', conversation_id: 'conv-1', role: 'user', content: 'Old question', context_type: null, citations: null, created_at: null },
    { id: 'old-answer', conversation_id: 'conv-1', role: 'assistant', content: 'Old answer', context_type: null, citations: null, created_at: null },
  ],
  maxDepth: 3,
};

const finalResponse = {
  conversation_id: 'conv-1',
  answer: 'Final answer',
  citations: [],
  research_performed: false,
  research_summary: null,
  grounding: null,
};

describe('ChatPane message motion', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.motionRows.clear();
    mocks.nextMountId = 0;
    mocks.resolveAsk = undefined;
    mocks.resolveHistory = undefined;
    mocks.listPaperConversations.mockResolvedValue([
      { conversation_id: 'conv-1', turn_count: 2, started_at: null, last_at: null, first_user_message: 'Old question' },
    ]);
    mocks.getPaperChat.mockImplementation(() => {
      if (mocks.getPaperChat.mock.calls.length === 1) return Promise.resolve(history);
      return new Promise((resolve) => { mocks.resolveHistory = resolve; });
    });
    mocks.askPaperStream.mockImplementation((_id, _question, _sequence, _conversation, _options, handlers) => {
      handlers.onToken('Draft answer');
      return new Promise((resolve) => { mocks.resolveAsk = resolve; });
    });
  });

  it('does not animate loaded history, animates appended rows, and reuses the streaming row on commit', async () => {
    render(
      <MotionRoot>
        <ChatPane paperId="paper-1" currentSequenceOrder={1} revealedCount={1} />
      </MotionRoot>,
    );

    expect(await screen.findByText('Old answer')).toBeInTheDocument();
    const oldRows = [...mocks.motionRows.values()].filter(({ props }) => props['data-message-key']?.startsWith('history-'));
    expect(oldRows).toHaveLength(2);
    expect(oldRows.every(({ props }) => props.initial === false)).toBe(true);

    const input = screen.getByPlaceholderText(/why is τ so small here/);
    fireEvent.change(input, { target: { value: 'New question' } });
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: false });

    expect(await screen.findByText('New question')).toBeInTheDocument();
    expect(await screen.findByText('Draft answer')).toBeInTheDocument();
    const userRow = [...mocks.motionRows.values()].find(({ props }) =>
      props['data-message-role'] === 'user' && props['data-message-key']?.startsWith('local-'),
    );
    const streamingRow = [...mocks.motionRows.entries()].find(([, { props }]) => props['data-streaming'] === 'true');
    expect(userRow?.props.initial).toMatchObject({ scale: 0.6, rotate: -3, y: 12 });
    expect(streamingRow).toBeDefined();
    const [streamingKey, streamingCapture] = streamingRow!;
    expect(streamingCapture.props.initial).toMatchObject({ scale: 0.6, rotate: -3, y: 12 });
    expect(screen.getByTestId('chat-stream-caret')).toBeInTheDocument();

    await act(async () => {
      mocks.resolveAsk?.(finalResponse);
      await Promise.resolve();
    });
    expect(await screen.findByText('Final answer')).toBeInTheDocument();
    const committed = mocks.motionRows.get(streamingKey);
    expect(committed?.mountId).toBe(streamingCapture.mountId);
    expect(committed?.props['data-streaming']).toBeUndefined();

    await act(async () => {
      mocks.resolveHistory?.({
        turns: [
          ...history.turns,
          { id: 'new-user', conversation_id: 'conv-1', role: 'user', content: 'New question', context_type: null, citations: null, created_at: null },
          { id: 'new-answer', conversation_id: 'conv-1', role: 'assistant', content: 'Final answer', context_type: null, citations: null, created_at: null },
        ],
        maxDepth: 3,
      });
      await Promise.resolve();
    });
    expect(await screen.findByText('Final answer')).toBeInTheDocument();
  });
});
