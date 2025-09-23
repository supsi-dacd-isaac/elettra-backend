import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

type AuthContextValue = {
  token: string;
  setToken: (t: string) => void;
  userId: string;
  setUserId: (id: string) => void;
  logout: () => void;
  refreshUser: () => void;
};

const LS_TOKEN_KEY = 'elettra_jwt';

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string>(() => {
    try {
      const saved = typeof window !== 'undefined' ? window.localStorage.getItem(LS_TOKEN_KEY) : null;
      return saved || '';
    } catch {
      return '';
    }
  });
  const [userId, setUserIdState] = useState<string>('');
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);

  const setToken = useCallback((t: string) => {
    setTokenState(t);
    try {
      if (typeof window === 'undefined') return;
      if (t) window.localStorage.setItem(LS_TOKEN_KEY, t);
      else window.localStorage.removeItem(LS_TOKEN_KEY);
    } catch {}
  }, []);

  const setUserId = useCallback((id: string) => {
    setUserIdState(id || '');
  }, []);

  const refreshUser = useCallback(() => {
    setRefreshTrigger(prev => prev + 1);
  }, []);

  // When token changes or refresh is triggered, fetch /auth/me to preselect agency
  useEffect(() => {
    let cancelled = false;
    async function syncAgencyFromMe() {
      if (!token) return;
      try {
        const base = (() => {
          const VITE = (typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) || {};
          const envBase = VITE.VITE_API_BASE_URL || '';
          if (envBase) return envBase as string;
          if (typeof window !== 'undefined') {
            const host = window.location.hostname;
            if (host === 'localhost' || host === '127.0.0.1') return 'http://localhost:8002';
            if (/^10\./.test(host)) return `http://${host}:8002`;
            if (host === 'isaac-elettra.dacd.supsi.ch') return 'http://isaac-elettra.dacd.supsi.ch:8002';
          }
          return 'http://localhost:8002';
        })();
        const res = await fetch(`${base}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) return;
        const me = await res.json();
        if (!cancelled && me?.id && !userId) setUserIdState(me.id);
      } catch {}
    }
    void syncAgencyFromMe();
    return () => { cancelled = true; };
  }, [token, refreshTrigger]);

  const logout = useCallback(() => {
    setToken('');
    setUserIdState('');
  }, [setToken]);

  const value = useMemo<AuthContextValue>(() => ({ token, setToken, userId, setUserId, logout, refreshUser }), [token, setToken, userId, setUserId, logout, refreshUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}


