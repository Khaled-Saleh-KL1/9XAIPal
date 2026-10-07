import { describe, expect, it } from 'vitest';
import { beginBusy, isBusy, withBusy } from './busy';

describe('busy registry', () => {
  it('is busy between beginBusy and endBusy', () => {
    const a = beginBusy();
    const b = beginBusy();
    expect(isBusy()).toBe(true);
    a();
    expect(isBusy()).toBe(true);
    b();
    b(); // ending twice is harmless
    expect(isBusy()).toBe(false);
  });
  it('withBusy releases on success and on failure', async () => {
    await withBusy(Promise.resolve(1));
    expect(isBusy()).toBe(false);
    await expect(withBusy(Promise.reject(new Error('x')))).rejects.toThrow('x');
    expect(isBusy()).toBe(false);
  });
  it('withBusy is busy while pending', async () => {
    let done!: () => void;
    const p = withBusy(new Promise<void>((r) => { done = r; }));
    expect(isBusy()).toBe(true);
    done();
    await p;
    expect(isBusy()).toBe(false);
  });
});
