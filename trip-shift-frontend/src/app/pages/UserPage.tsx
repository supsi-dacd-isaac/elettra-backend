import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import Panel from '../components/ui/Panel.tsx';

type CurrentUser = { id: string; company_id?: string; email: string; full_name: string; role: string };
type AgencyRead = { id: string; agency_name?: string | null; gtfs_agency_id?: string | null };

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
  const { token, logout, userId, setUserId } = useAuth();
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
                const agency = (await agRes.json()) as AgencyRead;
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
    <div className="space-y-4">
      <Panel>
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
      </Panel>

      <Panel>
        <h2 className="text-lg font-medium mb-2">{t('shift.selectAgencyPlaceholder')}</h2>
        <div className="text-sm">
          <p className="text-gray-600 mb-2">{t('depots.selectAgencyBackend')}</p>
          <AgencySelector token={token} selectedId={userId} onSelect={setUserId} />
        </div>
      </Panel>
    </div>
  );
}

function AgencySelector({ token, selectedId, onSelect }: { token: string; selectedId: string; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [agencies, setAgencies] = useState<AgencyRead[]>([]);
  const [query, setQuery] = useState<string>('');
  const [open, setOpen] = useState<boolean>(false);
  const [highlight, setHighlight] = useState<number>(-1);

  useEffect(() => {
    (async () => {
      if (!token || !baseUrl) return;
      try {
        const res = await fetch(joinUrl(baseUrl, '/api/v1/agency/agencies/?limit=1000'), { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = (await res.json()) as AgencyRead[];
        setAgencies(Array.isArray(data) ? data : []);
      } catch {}
    })();
  }, [token, baseUrl]);

  useEffect(() => {
    if (!selectedId) return;
    const sel = agencies.find((a) => a.id === selectedId);
    if (sel) setQuery((sel.agency_name || sel.gtfs_agency_id || '') as string);
  }, [selectedId, agencies]);

  function label(a?: AgencyRead) { return (a?.agency_name || a?.gtfs_agency_id || '') as string; }

  return (
    <div className="relative max-w-md">
      <input
        className="w-full px-3 py-2 border rounded-lg"
        placeholder={t('shift.selectAgencyPlaceholder') as string}
        value={query}
        onFocus={() => token && setOpen(true)}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlight(-1); if (selectedId) onSelect(''); }}
        onKeyDown={(e) => {
          if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return; }
          if (!open) return;
          if (e.key === 'ArrowDown') { e.preventDefault(); setHighlight((h) => Math.min((agencies.length - 1), h + 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight((h) => Math.max(-1, h - 1)); }
          else if (e.key === 'Enter') {
            e.preventDefault();
            const pick = highlight >= 0 ? agencies[highlight] : agencies[0];
            if (pick) { onSelect(pick.id); setQuery(label(pick)); setOpen(false); setHighlight(-1); }
          } else if (e.key === 'Escape') { setOpen(false); setHighlight(-1); }
        }}
        onBlur={() => { setTimeout(() => setOpen(false), 100); }}
      />
      {open && token && agencies.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full max-h-48 overflow-auto border rounded-lg bg-white shadow">
          {agencies
            .filter((a) => label(a).toLowerCase().includes(query.trim().toLowerCase()))
            .sort((a, b) => label(a).localeCompare(label(b), undefined, { sensitivity: 'base' }))
            .map((a, idx) => (
            <li
              key={a.id}
              className={"px-3 py-2 cursor-pointer text-sm " + (idx === highlight ? 'text-white' : 'hover:bg-gray-100')}
              style={idx === highlight ? {backgroundColor: '#002AA7'} : {}}
              onMouseEnter={() => setHighlight(idx)}
              onMouseDown={(e) => { e.preventDefault(); onSelect(a.id); setQuery(label(a)); setOpen(false); setHighlight(-1); }}
            >
              {label(a)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


