/**
 * Tiny registry of long-running user work (uploads, answer streams). The
 * service worker update reload (register.ts) never fires while this is busy.
 */
let count = 0;

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
  };
}

export function withBusy<T>(promise: Promise<T>): Promise<T> {
  const end = beginBusy();
  return promise.finally(end);
}
