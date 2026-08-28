import { useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '../contexts/AuthContext';

const inputStyle = {
  background: 'var(--bg-2)',
  border: '1px solid var(--border)',
  color: 'var(--fg)',
} as const;

/**
 * The full-screen gate shown when no one is logged in. Not a modal over
 * content — there's nothing behind it to show. Styled per the same tokens
 * and dialog shape as UploadKindModal (App.tsx), the closest existing
 * precedent; this codebase has no form library, so inputs are plain
 * controlled <input>s matching LibraryView's search-box styling.
 */
export function AuthView() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === 'login') {
        await login(email, password);
      } else {
        await signup(email, password, inviteCode, displayName || undefined);
      }
    } catch (err) {
      setError((err as Error).message || 'Something went wrong');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-6"
      style={{ background: 'var(--bg)' }}
    >
      <div
        className="w-full max-w-[420px] rounded-2xl overflow-hidden"
        style={{ background: 'var(--bg)', border: '1px solid var(--border)', boxShadow: '0 20px 60px -20px rgba(0,0,0,0.18)' }}
      >
        <div className="px-7 pt-7 pb-2">
          <div className="font-serif text-[20px] tracking-tight" style={{ color: 'var(--fg)' }}>
            {mode === 'login' ? 'Welcome back' : 'Create an account'}
          </div>
          <div className="text-[12.5px] mt-1" style={{ color: 'var(--muted)' }}>
            {mode === 'login' ? '9XAIPal: sign in to your library.' : 'You need an invite code to sign up.'}
          </div>
        </div>

        <form onSubmit={onSubmit} className="px-7 py-5 flex flex-col gap-3">
          <input
            type="email"
            required
            autoFocus
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
            style={inputStyle}
          />
          <input
            type="password"
            required
            minLength={mode === 'signup' ? 8 : undefined}
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
            style={inputStyle}
          />
          {mode === 'signup' && (
            <>
              <input
                type="text"
                required
                placeholder="Invite code"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
                style={inputStyle}
              />
              <input
                type="text"
                placeholder="Display name (optional)"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full rounded-md px-3 py-2 text-[13px] outline-none"
                style={inputStyle}
              />
            </>
          )}

          {error && (
            <div className="text-[12px]" style={{ color: 'var(--accent)' }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md px-3 py-2.5 text-[13px] font-medium mt-1 disabled:opacity-60"
            style={{ background: 'var(--accent)', color: 'var(--accent-fg)' }}
          >
            {submitting ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Sign up'}
          </button>
        </form>

        <div
          className="px-7 py-3.5 flex items-center justify-center text-[12px]"
          style={{ background: 'var(--bg-2)', borderTop: '1px solid var(--border)', color: 'var(--muted)' }}
        >
          {mode === 'login' ? (
            <>
              No account?{' '}
              <button
                type="button"
                onClick={() => { setMode('signup'); setError(null); }}
                className="ml-1 underline"
                style={{ color: 'var(--fg)' }}
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              Already have an account?{' '}
              <button
                type="button"
                onClick={() => { setMode('login'); setError(null); }}
                className="ml-1 underline"
                style={{ color: 'var(--fg)' }}
              >
                Log in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
