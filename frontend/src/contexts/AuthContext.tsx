import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { User } from '../types';
import { getMe, login as apiLogin, signup as apiSignup, logout as apiLogout } from '../api';

interface AuthContextValue {
  user: User | null;
  /** True only while the initial GET /auth/me (on mount) is in flight. */
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, inviteCode: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then(setUser)
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await apiLogin(email, password);
    setUser(u);
  }, []);

  const signup = useCallback(
    async (email: string, password: string, inviteCode: string, displayName?: string) => {
      const u = await apiSignup(email, password, inviteCode, displayName);
      setUser(u);
    },
    [],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
