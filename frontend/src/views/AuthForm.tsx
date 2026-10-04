import { useRef, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { AnimatePresence, m, useReducedMotion } from 'motion/react';
import { useAuth } from '../contexts/AuthContext';
import { Pressable, usePauseWhenHidden } from '../motion';
import { gentle } from '../motion/springs';

const inputStyle = {
  background: 'var(--bg-2)',
  border: '1px solid var(--border)',
  color: 'var(--fg)',
} as const;

export function AuthForm({
  initialMode,
  titleId,
  firstFieldRef,
}: {
  initialMode: 'login' | 'signup';
  titleId: string;
  firstFieldRef: RefObject<HTMLInputElement | null>;
}) {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<'login' | 'signup'>(initialMode);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const spinnerRef = useRef<HTMLSpanElement>(null);
  const spinnerVisible = usePauseWhenHidden(spinnerRef);
  const reducedMotion = useReducedMotion();

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError(null);
    setSubmitting(true);
    try {
      if (mode === 'login') await login(email, password);
      else await signup(email, password, displayName || undefined);
    } catch (err) {
      setError((err as Error).message || 'Something went wrong');
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <section
      className="w-full max-w-[420px] rounded-2xl overflow-hidden"
      style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)' }}
    >
      <div className="px-7 pt-7 pb-2">
        <AnimatePresence mode="wait" initial={false}>
          <m.h2
            key={mode}
            id={titleId}
            className="font-serif text-[20px] tracking-tight"
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={gentle}
            style={{ color: 'var(--fg)' }}
          >
            {mode === 'login' ? 'Welcome back' : 'Create an account'}
          </m.h2>
        </AnimatePresence>
        <AnimatePresence mode="wait" initial={false}>
          <m.p
            key={mode}
            className="text-[12.5px] mt-1"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={gentle}
            style={{ color: 'var(--muted)' }}
          >
            {mode === 'login' ? '9XAIPal: sign in to your library.' : 'Create your free account.'}
          </m.p>
        </AnimatePresence>
      </div>

      <m.form
        onSubmit={onSubmit}
        className="px-7 py-5 flex flex-col gap-3"
        animate={error ? { x: [0, -10, 9, -6, 4, 0] } : { x: 0 }}
        transition={{ duration: 0.4 }}
      >
        <input
          ref={firstFieldRef}
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
          style={inputStyle}
        />
        <input
          type="password"
          required
          minLength={mode === 'signup' ? 8 : undefined}
          placeholder="Password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
          style={inputStyle}
        />
        <AnimatePresence initial={false}>
          {mode === 'signup' && (
            <m.div
              key="display-name"
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={gentle}
            >
              <input
                dir="auto"
                type="text"
                placeholder="Display name (optional)"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
                style={inputStyle}
              />
            </m.div>
          )}
        </AnimatePresence>

        {error && <div role="alert" className="text-[12px]" style={{ color: 'var(--accent)' }}>{error}</div>}

        <Pressable
          type="submit"
          disabled={submitting}
          className="w-full min-w-[112px] rounded-md px-3 py-2.5 text-[13px] font-medium mt-1 disabled:opacity-60"
          style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
        >
          <AnimatePresence mode="wait" initial={false}>
            {submitting ? (
              <m.span key="waiting" className="inline-flex items-center justify-center gap-2" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={gentle}>
                <m.span
                  ref={spinnerRef}
                  aria-hidden="true"
                  animate={reducedMotion ? undefined : spinnerVisible ? { rotate: 360 } : { rotate: 0 }}
                  transition={reducedMotion || !spinnerVisible ? { duration: 0 } : { duration: 0.8, ease: 'linear', repeat: Infinity }}
                  style={{ width: 14, height: 14, border: '2px solid color-mix(in oklch, var(--accent-fg) 45%, transparent)', borderTopColor: 'var(--accent-fg)', borderRadius: '50%' }}
                />
                <span>Please wait…</span>
              </m.span>
            ) : (
              <m.span key="ready" className="inline-block" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={gentle}>
                {mode === 'login' ? 'Log in' : 'Sign up'}
              </m.span>
            )}
          </AnimatePresence>
        </Pressable>
      </m.form>

      <div
        className="px-7 py-3.5 flex items-center justify-center text-[12px]"
        style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)', color: 'var(--muted)' }}
      >
        {mode === 'login' ? (
          <>
            No account?{' '}
            <Pressable type="button" onClick={() => { setMode('signup'); setError(null); }} className="ml-1 underline" style={{ color: 'var(--fg)' }}>
              Sign up
            </Pressable>
          </>
        ) : (
          <>
            Already have an account?{' '}
            <Pressable type="button" onClick={() => { setMode('login'); setError(null); }} className="ml-1 underline" style={{ color: 'var(--fg)' }}>
              Log in
            </Pressable>
          </>
        )}
      </div>
    </section>
  );
}
