/**
 * Tiny registry of long-running user work (uploads, answer streams). The
 * service worker update reload (register.ts) never fires while this is busy.
 */
let count = 0;
const idleListeners = new Set<() => void>();

export function isBusy(): boolean {
  return count > 0;
}

/** Returns an idempotent `end` function. */
export function beginBusy(): () => void {
  count += 1;
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    count -= 1;
    if (count === 0) idleListeners.forEach((cb) => cb());
  };
}

export function withBusy<T>(promise: Promise<T>): Promise<T> {
  const end = beginBusy();
  return promise.finally(end);
}

export function subscribeIdle(cb: () => void): () => void {
  idleListeners.add(cb);
  return () => idleListeners.delete(cb);
}
