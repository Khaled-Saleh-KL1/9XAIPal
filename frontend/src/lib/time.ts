/**
 * Short, human-friendly "X ago" for chips and card eyebrows.
 *
 * The threshold ladder is deliberately loose because nobody needs "47 seconds
 * ago": the label is small and the reader only wants to know whether the
 * thing it is attached to is fresh or stale.
 */
export function formatRelativeTime(at: number, now: number = Date.now()): string {
  const ms = now - at;
  if (ms < 0 || ms < 60_000) return 'just now';
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}
