import { useEffect, useRef } from 'react';
import { m, useReducedMotion } from 'motion/react';
import { useAuth } from '../contexts/AuthContext';
import { Reveal } from '../motion/Reveal';
import { usePauseWhenHidden } from '../motion/usePauseWhenHidden';
import { calm } from '../motion/springs';
import { RollingNumber } from '../motion/RollingNumber';

/**
 * Shown when someone is logged in but the site is at its concurrent-active-
 * user cap (see backend app/core/capacity.py) and they haven't been let in
 * yet. Same full-screen-gate shape as the signed-out landing view — there's nothing behind it
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
  const paperRef = useRef<HTMLDivElement>(null);
  const canFloat = usePauseWhenHidden(paperRef);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    pollRef.current = setInterval(() => {
      // Swallowed on purpose: this polls every 6s forever, so a transient
      // network blip is expected rather than exceptional, and letting the
      // rejection escape only fills the console with noise the next tick
      // recovers from anyway.
      refreshAdmission().catch(() => {});
    }, 6000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refreshAdmission]);

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto px-4 py-8 sm:px-6"
      style={{
        background: 'radial-gradient(ellipse at 78% 7%, color-mix(in oklch, var(--accent-soft) 78%, transparent) 0%, transparent 35%), var(--bg)',
        color: 'var(--fg)',
      }}
    >
      <Reveal className="w-full max-w-[860px]" y={22}>
        <section
          aria-labelledby="waiting-title"
          className="grid w-full overflow-hidden rounded-[26px] border md:grid-cols-[1.1fr_0.9fr]"
          style={{
            background: 'color-mix(in oklch, var(--bg) 94%, var(--bg-2))',
            borderColor: 'var(--border)',
            boxShadow: '0 28px 80px -36px color-mix(in oklch, var(--fg) 22%, transparent)',
          }}
        >
          <div className="flex min-w-0 flex-col items-start px-6 py-8 sm:px-10 sm:py-11">
            <div
              className="mb-6 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: 'var(--accent)', borderColor: 'color-mix(in oklch, var(--accent) 28%, var(--border))', background: 'var(--bg-2)' }}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: 'var(--accent)' }} />
              Just a little pause
            </div>
            <h1 id="waiting-title" className="max-w-[480px] font-serif text-[34px] leading-[1.02] tracking-[-0.04em] sm:text-[44px]" style={{ color: 'var(--fg)' }}>
              You're in the queue
            </h1>
            <p className="mt-4 max-w-[420px] text-[14px] leading-7" style={{ color: 'var(--muted)' }}>
              The site is at capacity right now. You'll be let in automatically the moment a spot opens up, no need to refresh.
            </p>

            {typeof queuePosition === 'number' && queuePosition > 0 && (
              <div className="mt-7 flex items-end gap-3 rounded-2xl border px-5 py-4" style={{ background: 'var(--bg-2)', borderColor: 'var(--border)' }}>
                <div>
                  <div className="text-[9px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--muted)' }}>Your place</div>
                  <div className="mt-1 flex items-baseline gap-2 font-serif text-[32px] leading-none" style={{ color: 'var(--fg)' }}>
                    <span aria-hidden="true">#</span>
                    <RollingNumber value={queuePosition} />
                    <span className="font-sans text-[12px]" style={{ color: 'var(--muted)' }}>in line</span>
                  </div>
                </div>
              </div>
            )}

            <div className="mt-auto flex items-center gap-2.5 pt-8 text-[11px]" style={{ color: 'var(--muted)' }}>
              <span className="h-2 w-2 rounded-full" style={{ background: 'var(--accent)' }} />
              Checking your place every few seconds
            </div>
          </div>

          <div className="relative grid min-h-[230px] place-items-center overflow-hidden px-6 py-8 sm:min-h-[320px] md:min-h-[390px]" style={{ background: 'radial-gradient(ellipse at 50% 45%, color-mix(in oklch, var(--accent-soft) 70%, transparent), transparent 68%)' }}>
            <div className="absolute h-48 w-48 rounded-full border" style={{ borderColor: 'color-mix(in oklch, var(--accent) 16%, transparent)' }} />
            <m.div
              ref={paperRef}
              aria-hidden="true"
              className="relative z-10 w-[190px] rotate-[3deg] rounded-sm border p-5 sm:w-[220px]"
              style={{ background: 'var(--bg)', borderColor: 'var(--border)', boxShadow: '0 20px 46px -18px color-mix(in oklch, var(--fg) 25%, transparent)' }}
              initial={false}
              animate={canFloat && !reducedMotion ? { y: [0, -8, 0], rotate: [3, 1, 3] } : { y: 0, rotate: 3 }}
              transition={canFloat && !reducedMotion ? { duration: 4.2, repeat: Infinity, ease: 'easeInOut' } : calm}
            >
              <div className="flex items-center justify-between border-b pb-3" style={{ borderColor: 'var(--border)' }}>
                <span className="font-serif text-[14px]" style={{ color: 'var(--fg)' }}>A moment to breathe</span>
                <span className="text-[8px] font-semibold tracking-[0.1em]" style={{ color: 'var(--accent)' }}>9XAI</span>
              </div>
              <div className="mt-4 space-y-2.5">
                <div className="h-1.5 w-[92%] rounded-full" style={{ background: 'var(--border-strong)' }} />
                <div className="h-1.5 w-[78%] rounded-full" style={{ background: 'var(--border)' }} />
                <div className="h-1.5 w-[86%] rounded-full" style={{ background: 'var(--border)' }} />
                <div className="my-4 h-9 rounded-md" style={{ background: 'color-mix(in oklch, var(--accent-soft) 78%, transparent)' }} />
                <div className="h-1.5 w-[90%] rounded-full" style={{ background: 'var(--border)' }} />
                <div className="h-1.5 w-[65%] rounded-full" style={{ background: 'var(--border)' }} />
              </div>
            </m.div>
            <div className="absolute bottom-5 left-0 right-0 text-center text-[10px] tracking-[0.06em]" style={{ color: 'var(--muted)' }}>
              Your papers will be right here
            </div>
          </div>
        </section>
      </Reveal>
    </div>
  );
}
