import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useTranslation } from 'react-i18next';
import Panel from '../components/ui/Panel.tsx';

export default function LoginPage() {
  const { t } = useTranslation();
  const { setToken } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showRegister, setShowRegister] = useState(false);
  const [regFullName, setRegFullName] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regAgencyId, setRegAgencyId] = useState('');
  const [regRole, setRegRole] = useState<'viewer' | 'analyst' | 'admin'>('viewer');
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState('');
  const [agencies, setAgencies] = useState<any[]>([]);
  const [agenciesLoading, setAgenciesLoading] = useState(false);
  const [agenciesError, setAgenciesError] = useState('');
  const navigate = useNavigate();

  function joinUrl(base: string, path: string): string {
    const cleanBase = (base || '').replace(/\/+$/, '');
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
  }

  // Load agencies when the register modal is opened
  useEffect(() => {
    let cancelled = false;
    async function loadAgencies() {
      if (!showRegister) return;
      try {
        setAgenciesError('');
        setAgenciesLoading(true);
        const base = getEffectiveBaseUrl();
        const url = joinUrl(base, '/api/v1/agency/agencies/?limit=1000');
        const res = await fetch(url);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) setAgencies(data);
      } catch (e) {
        if (!cancelled) {
          setAgencies([]);
          setAgenciesError('load');
        }
      } finally {
        if (!cancelled) setAgenciesLoading(false);
      }
    }
    void loadAgencies();
    return () => { cancelled = true; };
  }, [showRegister]);

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
    setNotice('');
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

  async function handleRegister() {
    setRegError('');
    setNotice('');
    // Basic validation
    if (!regFullName || !regEmail || !regPassword || !regAgencyId) {
      setRegError(t('auth.provideCredentials') as string);
      return;
    }
    try {
      setRegLoading(true);
      const base = getEffectiveBaseUrl();
      const url = joinUrl(base, '/auth/register');
      const body = {
        company_id: regAgencyId,
        email: regEmail,
        full_name: regFullName,
        password: regPassword,
        role: regRole,
      } as any;
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      // Success
      setShowRegister(false);
      setEmail(regEmail);
      setNotice(t('auth.registrationSuccess') as string);
      // reset register form state
      setRegFullName('');
      setRegEmail('');
      setRegPassword('');
      setRegAgencyId('');
      setRegRole('viewer');
    } catch (e: any) {
      setRegError(e?.message || String(e));
    } finally {
      setRegLoading(false);
    }
  }

  return (
    <div className="max-w-md mx-auto">
      <Panel>
      <h2 className="text-lg font-medium mb-3">{t('auth.title')}</h2>
      {notice && <div className="mb-2 text-sm text-green-700">{notice}</div>}
      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <input className="px-3 py-2 border rounded-lg" placeholder={t('auth.emailPlaceholder') as string} value={email} onChange={(e) => setEmail(e.target.value)} />
          <input className="px-3 py-2 border rounded-lg" type="password" placeholder={t('auth.passwordPlaceholder') as string} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="flex items-center gap-2">
          <button onClick={loginWithCredentials} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} disabled={loading}>
            {t(loading ? 'auth.loggingIn' : 'auth.login')}
          </button>
          <button onClick={() => { setShowRegister(true); setRegError(''); }} className="px-3 py-2 rounded-lg border text-sm hover:bg-neutral-50" disabled={loading}>
            {t('auth.register')}
          </button>
        </div>
      </div>
      </Panel>

      {showRegister && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <Panel className="max-w-md w-[95%]">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-base font-medium">{t('auth.registerTitle')}</h3>
              <button onClick={() => setShowRegister(false)} className="text-sm text-neutral-600 hover:text-neutral-800">{t('common.close')}</button>
            </div>
            {regError && <div className="mb-2 text-sm text-red-600">{regError}</div>}
            <div className="space-y-2">
              <input className="w-full px-3 py-2 border rounded-lg" placeholder={t('auth.fullNamePlaceholder') as string} value={regFullName} onChange={(e) => setRegFullName(e.target.value)} />
              <input className="w-full px-3 py-2 border rounded-lg" placeholder={t('auth.emailPlaceholder') as string} value={regEmail} onChange={(e) => setRegEmail(e.target.value)} />
              <input className="w-full px-3 py-2 border rounded-lg" type="password" placeholder={t('auth.passwordPlaceholder') as string} value={regPassword} onChange={(e) => setRegPassword(e.target.value)} />
              <div>
                <label className="block text-sm mb-1">{t('auth.selectAgencyLabel')}</label>
                <select className="w-full px-3 py-2 border rounded-lg" value={regAgencyId} onChange={(e) => setRegAgencyId(e.target.value)} disabled={agenciesLoading || agencies.length === 0}>
                  <option value="">{agenciesLoading ? (t('common.loading') as string) : (t('auth.selectAgencyPlaceholder') as string)}</option>
                  {agencies.map((a: any) => (
                    <option key={a.id} value={a.id}>{a.agency_name || a.gtfs_agency_id || a.id}</option>
                  ))}
                </select>
                {agenciesError && <div className="mt-1 text-xs text-neutral-500">{t('auth.couldNotLoadAgencies')}</div>}
              </div>
              <div>
                <label className="block text-sm mb-1">{t('auth.roleLabel')}</label>
                <select className="w-full px-3 py-2 border rounded-lg" value={regRole} onChange={(e) => setRegRole(e.target.value as any)}>
                  <option value="viewer">{t('auth.viewer')}</option>
                  <option value="analyst">{t('auth.analyst')}</option>
                  <option value="admin">{t('auth.admin')}</option>
                </select>
              </div>
              <div className="flex items-center gap-2 pt-1">
                <button onClick={handleRegister} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} disabled={regLoading || !regAgencyId}>
                  {t(regLoading ? 'auth.registering' : 'auth.register')}
                </button>
                <button onClick={() => setShowRegister(false)} className="px-3 py-2 rounded-lg border text-sm" disabled={regLoading}>{t('common.cancel')}</button>
              </div>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}


