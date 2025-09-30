import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import CreateDepotView from '../components/depots/CreateDepotView.tsx';
import EditDepotView from '../components/depots/EditDepotView.tsx';
import Panel from '../components/ui/Panel.tsx';

type Depot = { id: string; user_id: string; name: string; address?: string | null; features?: any; stop_id?: string | null; latitude?: number | null; longitude?: number | null };
type UserMe = { id: string; company_id?: string };

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

export default function DepotsPage() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [userId, setUserId] = useState<string>('');
  const [depots, setDepots] = useState<Depot[]>([]);
  const [depotsLoading, setDepotsLoading] = useState<boolean>(false);
  const [depotsError, setDepotsError] = useState<string>('');
  const [editingDepot, setEditingDepot] = useState<Depot | null>(null);
  const [mode, setMode] = useState<'list' | 'create' | 'edit'>('list');
  const [notice, setNotice] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    async function loadMe() {
      if (!token || !baseUrl) return;
      try {
        const meRes = await fetch(joinUrl(baseUrl, '/auth/me'), { headers: { Authorization: `Bearer ${token}` } });
        if (meRes.ok) {
          const me = (await meRes.json()) as UserMe;
          if (!cancelled && me?.id) setUserId(me.id);
        }
      } catch {}
    }
    void loadMe();
    return () => { cancelled = true; };
  }, [token, baseUrl]);

  const loadDepotsForUser = async () => {
    if (!baseUrl || !token || !userId) return;
    try {
      setDepotsError('');
      setDepotsLoading(true);
      const url = joinUrl(baseUrl, '/api/v1/user/depots/?skip=0&limit=1000');
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as Depot[];
      setDepots(Array.isArray(all) ? all.filter((d) => d.user_id === userId) : []);
    } catch (e: any) {
      setDepots([]);
      setDepotsError(e?.message || String(e));
    } finally {
      setDepotsLoading(false);
    }
  };

  useEffect(() => { void loadDepotsForUser(); }, [token, baseUrl, userId]);

  return (
    <Panel>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-medium">{t('depots.title')}</h2>
        <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#002AA7'}} disabled={!token || !userId} onClick={() => setMode('create')} title={!token ? (t('depots.loginFirst') as any) : !userId ? (t('depots.selectAgencyFirst') as any) : (t('depots.createNewHint') as any)}>
          {t('depots.createButton')}
        </button>
      </div>
      {notice && (
        <div className="mb-2 text-xs px-3 py-2 rounded" style={{backgroundColor: '#f0f9ff', color: '#74C244', borderColor: '#74C244', border: '1px solid'}}>{notice}</div>
      )}
      {mode === 'create' ? (
        <CreateDepotView token={token} userId={userId} baseUrl={baseUrl} onCancel={() => setMode('list')} onCreated={(dep?: any) => {
          setNotice(dep?.name ? t('depots.createdWithName', { name: dep.name }) as string : (t('depots.created') as string));
          setTimeout(() => setNotice(''), 3000);
          void loadDepotsForUser();
          setMode('list');
        }} />
      ) : mode === 'edit' && editingDepot ? (
        <EditDepotView token={token} userId={userId} baseUrl={baseUrl} depot={editingDepot} onCancel={() => {
          setMode('list');
          setEditingDepot(null);
        }} onUpdated={(dep?: any) => {
          setNotice(dep?.name ? t('depots.updatedWithName', { name: dep.name }) as string : (t('depots.updated') as string));
          setTimeout(() => setNotice(''), 3000);
          void loadDepotsForUser();
          setMode('list');
          setEditingDepot(null);
        }} />
      ) : (
        <div className="mt-1 space-y-2">
          <div className="text-sm text-gray-700 flex items-center justify-between">
            <span>{t('depots.listTitle')}</span>
            {depotsLoading && <span className="text-xs text-gray-500">{t('common.loading')}</span>}
          </div>
          {depotsError && <div className="text-sm text-red-600">{depotsError}</div>}
          {(!depotsLoading && depots.length === 0) ? (
            <div className="text-sm text-gray-600">{t('depots.empty')}</div>
          ) : (
            <ul className="space-y-2">
              {depots.map((d) => (
                <li key={d.id} className="border rounded-lg p-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="text-sm">
                      <div className="font-medium">{d.name}</div>
                      <div className="text-gray-600">{[d.address].filter(Boolean).join(', ')}</div>
                      <div className="text-xs text-gray-500">{typeof d.latitude === 'number' && typeof d.longitude === 'number' ? `${d.latitude.toFixed(6)}, ${d.longitude.toFixed(6)}` : ''}</div>
                    </div>
                    <div className="flex gap-2">
                      <button 
                        className="px-2 py-1 rounded text-white text-sm hover:opacity-90 relative group" 
                        style={{backgroundColor: '#002AA7'}} 
                        onClick={() => { 
                          setEditingDepot(d); 
                          setMode('edit'); 
                        }}
                        title={t('common.edit') as string}
                      >
                        <i className="fa-solid fa-pen-to-square"></i>
                        <span className="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 px-2 py-1 text-xs text-white bg-gray-800 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                          {t('common.edit')}
                        </span>
                      </button>
                      <button 
                        className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700 relative group" 
                        onClick={async () => {
                          if (!baseUrl || !token) return;
                          if (!window.confirm(t('depots.confirmDelete', { name: d.name }) as any)) return;
                          try {
                            const res = await fetch(joinUrl(baseUrl, `/api/v1/user/depots/${encodeURIComponent(d.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                            if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                            setDepots((prev) => prev.filter((x) => x.id !== d.id));
                          } catch (e: any) { alert(t('depots.deleteFailed', { error: e?.message || e }) as any); }
                        }}
                        title={t('common.delete') as string}
                      >
                        <i className="fa-solid fa-trash"></i>
                        <span className="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 px-2 py-1 text-xs text-white bg-gray-800 rounded opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                          {t('common.delete')}
                        </span>
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}


