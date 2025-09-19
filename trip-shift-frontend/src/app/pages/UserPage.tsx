import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';

type CurrentUser = { id: string; company_id?: string; email: string; full_name: string; role: string };
type Agency = { id: string; agency_name: string };

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

export default function UserPage() {
  const { t } = useTranslation();
  const { token, logout } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [agencyName, setAgencyName] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!token) { setMe(null); setAgencyName(''); return; }
      try {
        setLoading(true);
        const meRes = await fetch(joinUrl(baseUrl, '/auth/me'), { headers: { Authorization: `Bearer ${token}` } });
        if (meRes.ok) {
          const user = (await meRes.json()) as CurrentUser;
          if (cancelled) return;
          setMe(user);
          if (user?.company_id) {
            try {
              const agRes = await fetch(joinUrl(baseUrl, `/api/v1/agency/agencies/${user.company_id}`), { headers: { Authorization: `Bearer ${token}` } });
              if (agRes.ok) {
                const agency = (await agRes.json()) as Agency;
                if (!cancelled) setAgencyName(agency?.agency_name || '');
              }
            } catch {}
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [token, baseUrl]);

  return (
    <div className="p-3 rounded-2xl bg-white shadow-sm border">
      <h2 className="text-lg font-medium mb-2">{t('auth.userInfoTitle')}</h2>
      {loading && <div className="text-sm text-gray-600">{t('common.loading')}</div>}
      {!loading && (
        <div className="text-sm">
          <div className="mb-1"><span className="font-semibold">{t('auth.userName')}:</span> {me?.full_name || '—'}</div>
          <div className="mb-1"><span className="font-semibold">{t('auth.userEmail')}:</span> {me?.email || '—'}</div>
          <div className="mb-1"><span className="font-semibold">{t('auth.agencyName')}:</span> {agencyName || '—'}</div>
          <div className="mb-3"><span className="font-semibold">{t('auth.userRole')}:</span> {me?.role || '—'}</div>
          <button onClick={logout} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>{t('auth.logout')}</button>
        </div>
      )}
    </div>
  );
}


