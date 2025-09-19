import { createContext, useCallback, useContext, useMemo, useState } from 'react';

type AuthContextValue = {
  token: string;
  setToken: (t: string) => void;
  logout: () => void;
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

  const setToken = useCallback((t: string) => {
    setTokenState(t);
    try {
      if (typeof window === 'undefined') return;
      if (t) window.localStorage.setItem(LS_TOKEN_KEY, t);
      else window.localStorage.removeItem(LS_TOKEN_KEY);
    } catch {}
  }, []);

  const logout = useCallback(() => {
    setToken('');
  }, [setToken]);

  const value = useMemo<AuthContextValue>(() => ({ token, setToken, logout }), [token, setToken, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}


