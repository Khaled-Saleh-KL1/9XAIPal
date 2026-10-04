import { afterEach, describe, expect, it, vi } from 'vitest';
import { askNoteStream, askStudyStream } from './api';

afterEach(() => vi.unstubAllGlobals());

function sseResponse(events: object[]): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('agent answer stream replacement events', () => {
  it('forwards replace to note consumers between token chunks', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { type: 'created', note_id: 'note-1' },
      { type: 'token', text: 'discarded preamble' },
      { type: 'replace' },
      { type: 'token', text: 'final answer' },
      {
        type: 'done', note_id: 'note-1', answer: 'final answer', model: 'fake',
        retrieval_mode: 'agent', cited_sequence_ids: [], agent_steps: [],
      },
    ])));
    const onReplace = vi.fn();
    const onToken = vi.fn();

    await askNoteStream(
      'paper-1',
      'question',
      { kind: 'text', sequence_id: 1 },
      null,
      {
        onCreated: vi.fn(),
        onStatus: vi.fn(),
        onStep: vi.fn(),
        onToken,
        onReplace,
      },
    );

    expect(onToken.mock.calls).toEqual([['discarded preamble'], ['final answer']]);
    expect(onReplace).toHaveBeenCalledOnce();
  });

  it('forwards replace to study consumers between token chunks', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { type: 'created', turn_id: 'turn-1', conversation_id: 'conversation-1' },
      { type: 'token', text: 'discarded preamble' },
      { type: 'replace' },
      { type: 'token', text: 'final answer' },
      { type: 'done', turn_id: 'turn-1', answer: 'final answer', model: 'fake', cited: [], agent_steps: [] },
    ])));
    const onReplace = vi.fn();
    const onToken = vi.fn();

    await askStudyStream('library', 'question', {
      onStatus: vi.fn(),
      onStep: vi.fn(),
      onToken,
      onReplace,
    });

    expect(onToken.mock.calls).toEqual([['discarded preamble'], ['final answer']]);
    expect(onReplace).toHaveBeenCalledOnce();
  });
});
