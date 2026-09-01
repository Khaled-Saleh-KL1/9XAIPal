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
   * true. Login/signup themselves already run one successful request before
   * this ever gets called, so no separate initial fetch is needed there. */
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

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    // login/signup succeeding means the backend already admitted this
    // session (or it wouldn't have been able to touch the DB user row at
    // all) — a fresh /me isn't needed to know that.
    setUser(u);
    setAdmitted(true);
    setQueuePosition(null);
  }, []);

  const signup = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const u = await apiSignup(email, password, displayName);
      setUser(u);
      setAdmitted(true);
      setQueuePosition(null);
    },
    [],
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
