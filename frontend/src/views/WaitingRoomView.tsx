import { useEffect, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';

/**
 * Shown when someone is logged in but the site is at its concurrent-active-
 * user cap (see backend app/core/capacity.py) and they haven't been let in
 * yet. Same full-screen-gate shape as AuthView — there's nothing behind it
 * to show, same as "not logged in" — polling GET /me every few seconds until
 * `admitted` flips true, at which point App.tsx's gate swaps this out for
 * the real app with no reload needed.
 *
 * The 5-8s interval matches ProcessingOverlay's polling idiom (App.tsx),
 * this codebase's established pattern for "cheap poll until a backend state
 * changes" rather than a WebSocket/SSE round trip for something this rare.
 */
export function WaitingRoomView() {
  const { queuePosition, refreshAdmission } = useAuth();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    pollRef.current = setInterval(() => {
      refreshAdmission();
    }, 6000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refreshAdmission]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6"
      style={{ background: 'var(--bg)' }}
    >
      <div
        className="w-full max-w-[420px] rounded-2xl overflow-hidden"
        style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)' }}
      >
        <div className="px-7 pt-7 pb-5">
          <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
            You're in the queue
          </div>
          <div className="text-[12.5px] mt-2 leading-relaxed" style={{ color: 'var(--muted)' }}>
            The site is at capacity right now.
            {typeof queuePosition === 'number' && queuePosition > 0 && (
              <>
                {' '}
                You're <span style={{ color: 'var(--fg)' }}>#{queuePosition}</span> in line.
              </>
            )}
            {' '}You'll be let in automatically the moment a spot opens up, no need to refresh.
          </div>
        </div>
        <div
          className="px-7 py-3.5 flex items-center gap-3"
          style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)' }}
        >
          <div
            className="w-1.5 h-1.5 rounded-full pulse-soft"
            style={{ background: 'var(--accent)' }}
          />
          <span className="text-[12px] font-mono" style={{ color: 'var(--muted)' }}>
            checking every few seconds…
          </span>
        </div>
      </div>
    </div>
  );
}
