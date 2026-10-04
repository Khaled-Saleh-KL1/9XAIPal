import { describe, expect, it, vi } from 'vitest';
import { createPacer } from './pacer';

describe('createPacer', () => {
  it('clears a superseded draft and continues painting replacement tokens', async () => {
    const updates: string[] = [];
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrameId = 0;
    let performanceTime = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = ++nextFrameId;
      frames.set(id, callback);
      return id;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id); });
    vi.stubGlobal('performance', { now: () => performanceTime });

    const advanceFrame = (time: number) => {
      const next = frames.entries().next().value as [number, FrameRequestCallback] | undefined;
      if (!next) throw new Error('expected a scheduled animation frame');
      frames.delete(next[0]);
      performanceTime = time;
      next[1](time);
    };

    const pacer = createPacer((text) => updates.push(text));
    pacer.push('x'.repeat(200));
    advanceFrame(1);
    advanceFrame(101);
    expect(updates[updates.length - 1]?.length).toBeGreaterThan(0);

    pacer.reset();
    expect(updates[updates.length - 1]).toBe('');

    pacer.push('replacement');
    const finished = pacer.finish();
    advanceFrame(201);
    advanceFrame(302);
    await finished;
    expect(updates[updates.length - 1]).toBe('replacement');
  });
});
