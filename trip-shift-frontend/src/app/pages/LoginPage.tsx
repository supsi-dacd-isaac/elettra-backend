import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useTranslation } from 'react-i18next';

export default function LoginPage() {
  const { t } = useTranslation();
  const { setToken } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  function joinUrl(base: string, path: string): string {
    const cleanBase = (base || '').replace(/\/+$/, '');
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
  }

  function getEffectiveBaseUrl(): string {
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
  }

  async function loginWithCredentials() {
    setError('');
    if (!email || !password) { setError(t('auth.provideCredentials') as string); return; }
    try {
      setLoading(true);
      const base = getEffectiveBaseUrl();
      const url = joinUrl(base, '/auth/login');
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      const tok = data?.access_token;
      if (!tok) throw new Error(t('auth.errors.noToken') as string);
      setToken(tok);
      navigate('/planner', { replace: true });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-md mx-auto bg-white border rounded-2xl p-4">
      <h2 className="text-lg font-medium mb-3">{t('auth.title')}</h2>
      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <input className="px-3 py-2 border rounded-lg" placeholder={t('auth.emailPlaceholder') as string} value={email} onChange={(e) => setEmail(e.target.value)} />
          <input className="px-3 py-2 border rounded-lg" type="password" placeholder={t('auth.passwordPlaceholder') as string} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button onClick={loginWithCredentials} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} disabled={loading}>
          {t(loading ? 'auth.loggingIn' : 'auth.login')}
        </button>
      </div>
    </div>
  );
}


