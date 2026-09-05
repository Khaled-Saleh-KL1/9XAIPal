import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { User } from '../types';
import { getMe, login as apiLogin, signup as apiSignup, logout as apiLogout } from '../api';

interface AuthContextValue {
  user: User | null;
  /** True only while the initial GET /auth/me (on mount) is in flight. */
  loading: boolean;
  /** False while `user` is set but the site is at capacity and this session
   * hasn't been let in yet — see backend app/core/capacity.py. Meaningless
   * (always true) while `user` is null. */
  admitted: boolean;
  /** 1-based place in line while `admitted` is false, else null. */
  queuePosition: number | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Re-check admission — WaitingRoomView polls this until `admitted` flips
   * true. Login and signup ask the same question once on the way in, so the
   * waiting room is reached without a poll having to run first. */
  refreshAdmission: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [admitted, setAdmitted] = useState(true);
  const [queuePosition, setQueuePosition] = useState<number | null>(null);

  const applyMe = useCallback((me: { user: User | null; admitted: boolean; queuePosition: number | null }) => {
    setUser(me.user);
    setAdmitted(me.admitted);
    setQueuePosition(me.queuePosition);
  }, []);

  useEffect(() => {
    getMe()
      .then(applyMe)
      .finally(() => setLoading(false));
  }, [applyMe]);

  // The browser's back-forward cache (bfcache) can restore this exact page —
  // full JS heap, DOM, and all — from before the user ever logged in: they
  // load the site, see the sign-in screen, log in (an in-place fetch, not a
  // navigation, so it doesn't touch history), browse around, then hit the
  // physical back button. If any earlier point in this tab's history was a
  // real navigation entry captured while `user` was still null, some
  // browsers restore that INSTANTLY from bfcache — sign-in screen and all —
  // without re-running this component's mount effect, since the page never
  // actually reloaded. `pageshow`'s `persisted` flag is exactly the signal
  // for "this came from bfcache, not a fresh load"; re-running the same
  // check to re-sync `user` against the real session is the fix. Wrapped in
  // try/catch: the very first fetch right after a bfcache restore can
  // itself transiently fail on some browsers (the underlying connection was
  // torn down while the page was suspended) — one retry covers that without
  // surfacing a scary error for what's really just a stale cache read.
  useEffect(() => {
    const onPageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      setLoading(true);
      getMe()
        .catch(() => getMe())
        .then(applyMe)
        .catch(() => {})
        .finally(() => setLoading(false));
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, [applyMe]);

  // ⚠ Admission has to be ASKED FOR, not assumed from a successful login.
  // POST /auth/login and /auth/signup mint a session; neither one calls
  // capacity.touch_and_check_admission (only GET /auth/me and the
  // get_current_user dependency do). So when the site is at
  // MAX_ACTIVE_USERS, logging in still returns 200 with a valid user — and
  // taking that as "admitted" rendered the whole app for someone with no
  // slot, whose every subsequent request then came back 423. The waiting
  // room, which exists for exactly this, never got shown. One GET /auth/me
  // settles it, and it is the same call WaitingRoomView already polls.
  const admitAfter = useCallback(
    async (u: User) => {
      setUser(u);
      try {
        applyMe(await getMe());
      } catch {
        // The session is real either way — fall back to letting them in
        // rather than stranding them behind a failed capacity check.
        setAdmitted(true);
        setQueuePosition(null);
      }
    },
    [applyMe],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      await admitAfter(await apiLogin(email, password));
    },
    [admitAfter],
  );

  const signup = useCallback(
    async (email: string, password: string, displayName?: string) => {
      await admitAfter(await apiSignup(email, password, displayName));
    },
    [admitAfter],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
    setAdmitted(true);
    setQueuePosition(null);
  }, []);

  const refreshAdmission = useCallback(async () => {
    const me = await getMe();
    applyMe(me);
  }, [applyMe]);

  return (
    <AuthContext.Provider value={{ user, loading, admitted, queuePosition, login, signup, logout, refreshAdmission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
