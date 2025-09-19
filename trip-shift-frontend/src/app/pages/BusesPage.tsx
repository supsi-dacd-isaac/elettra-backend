import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';

type BusModel = { id: string; agency_id: string; name: string };
type Bus = { id: string; agency_id: string; name: string; specs?: any; bus_model_id?: string | null };
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

export default function BusesPage() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [agencyId, setAgencyId] = useState<string>('');
  const [models, setModels] = useState<BusModel[]>([]);
  const [buses, setBuses] = useState<Bus[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Partial<Bus & { specsText?: string }>>({});
  const [showCreate, setShowCreate] = useState<boolean>(false);
  const [newName, setNewName] = useState<string>('');
  const [newModelId, setNewModelId] = useState<string>('');
  const [newSpecsText, setNewSpecsText] = useState<string>('');
  const [creating, setCreating] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    async function loadMe() {
      if (!token || !baseUrl) return;
      try {
        const meRes = await fetch(joinUrl(baseUrl, '/auth/me'), { headers: { Authorization: `Bearer ${token}` } });
        if (meRes.ok) {
          const me = (await meRes.json()) as UserMe;
          if (!cancelled && me?.company_id) setAgencyId(me.company_id);
        }
      } catch {}
    }
    void loadMe();
    return () => { cancelled = true; };
  }, [token, baseUrl]);

  const loadModels = async () => {
    if (!baseUrl || !token || !agencyId) return;
    try {
      const url = joinUrl(baseUrl, '/api/v1/agency/bus-models/?skip=0&limit=1000');
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as BusModel[];
      setModels(Array.isArray(all) ? all.filter((m) => m.agency_id === agencyId) : []);
    } catch {
      setModels([]);
    }
  };

  const loadBuses = async () => {
    if (!baseUrl || !token || !agencyId) return;
    try {
      setError('');
      setLoading(true);
      const url = joinUrl(baseUrl, '/api/v1/agency/buses/?skip=0&limit=1000');
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as Bus[];
      setBuses(Array.isArray(all) ? all.filter((b) => b.agency_id === agencyId) : []);
    } catch (e: any) {
      setBuses([]);
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadModels(); void loadBuses(); }, [token, baseUrl, agencyId]);

  return (
    <div className="p-3 rounded-2xl bg-white shadow-sm border">
      <div className="flex gap-2 mb-2">
        <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#002AA7'}} disabled={!token || !agencyId} onClick={() => setShowCreate((v) => !v)} title={!token ? (t('depots.authRequired') as any) : !agencyId ? (t('depots.selectAgencyBackend') as any) : ''}>
          {t('buses.createButton', showCreate ? 'Close' : 'Create bus')}
        </button>
        <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#6b7280'}} disabled={!token || !agencyId} onClick={() => { void loadModels(); void loadBuses(); }}>
          {t('common.refresh', 'Refresh')}
        </button>
      </div>
      {showCreate && (
        <div className="mb-3 border rounded-lg p-2 space-y-2">
          <input className="w-full px-2 py-1 border rounded" placeholder={t('buses.form.name', 'Name') as string} value={newName} onChange={(e) => setNewName(e.target.value)} />
          <select className="w-full px-2 py-1 border rounded" value={newModelId} onChange={(e) => setNewModelId(e.target.value)}>
            <option value="">{t('buses.form.selectModel', 'Select model')}</option>
            {models.map((m) => (<option key={m.id} value={m.id}>{m.name}</option>))}
          </select>
          <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t('buses.form.specs', 'Specs (JSON)') as string} value={newSpecsText} onChange={(e) => setNewSpecsText(e.target.value)} />
          <div className="flex gap-2">
            <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} disabled={!token || !agencyId || creating || !newName.trim()} onClick={async () => {
              if (!baseUrl || !token || !agencyId) return;
              try {
                setCreating(true);
                let specs: any = {};
                if (newSpecsText.trim()) { try { specs = JSON.parse(newSpecsText); } catch { alert(t('buses.parseError', 'Invalid JSON in specs') as any); setCreating(false); return; } }
                const res = await fetch(joinUrl(baseUrl, '/api/v1/agency/buses/'), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ agency_id: agencyId, name: newName, bus_model_id: newModelId || null, specs }) });
                if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                const created = (await res.json()) as Bus;
                setBuses((prev) => [created, ...prev]);
                setNewName(''); setNewModelId(''); setNewSpecsText(''); setShowCreate(false);
              } catch (e: any) { alert(t('buses.createFailed', { error: e?.message || String(e) }) as any); } finally { setCreating(false); }
            }}>{t('common.create', 'Create')}</button>
            <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setShowCreate(false); setNewName(''); setNewModelId(''); setNewSpecsText(''); }}>{t('common.cancel')}</button>
          </div>
        </div>
      )}
      <div className="text-sm text-gray-700 flex items-center justify-between">
        <span>{t('buses.listTitle', 'Buses for agency')}</span>
        {loading && <span className="text-xs text-gray-500">{t('common.loading')}</span>}
      </div>
      {error && <div className="text-sm text-red-600">{error}</div>}
      {(!loading && buses.length === 0) ? (
        <div className="text-sm text-gray-600">{t('buses.empty', 'No buses')}</div>
      ) : (
        <ul className="space-y-2 mt-2">
          {buses.map((b) => (
            <li key={b.id} className="border rounded-lg p-2">
              {editingId === b.id ? (
                <div className="space-y-2">
                  <input className="w-full px-2 py-1 border rounded" placeholder={t('buses.form.name', 'Name') as string} value={(editing.name as string) ?? b.name} onChange={(e) => setEditing((prev) => ({ ...prev, name: e.target.value }))} />
                  <select className="w-full px-2 py-1 border rounded" value={(editing.bus_model_id as string) ?? (b.bus_model_id || '')} onChange={(e) => setEditing((prev) => ({ ...prev, bus_model_id: e.target.value || null }))}>
                    <option value="">{t('buses.form.selectModel', 'Select model')}</option>
                    {models.map((m) => (<option key={m.id} value={m.id}>{m.name}</option>))}
                  </select>
                  <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t('buses.form.specs', 'Specs (JSON)') as string} value={(editing.specsText as string) ?? JSON.stringify(b.specs ?? {}, null, 2)} onChange={(e) => setEditing((prev) => ({ ...prev, specsText: e.target.value }))} />
                  <div className="flex gap-2">
                    <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} onClick={async () => {
                      if (!baseUrl || !token) return;
                      try {
                        const payload: any = {};
                        if (editing.name !== undefined) payload.name = editing.name;
                        if (editing.bus_model_id !== undefined) payload.bus_model_id = editing.bus_model_id || null;
                        if (editing.specsText !== undefined) { try { payload.specs = editing.specsText ? JSON.parse(editing.specsText as string) : {}; } catch { alert(t('buses.parseError', 'Invalid JSON in specs') as any); return; } }
                        const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/buses/${encodeURIComponent(b.id)}`), { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        const updated = (await res.json()) as Bus;
                        setBuses((prev) => prev.map((x) => x.id === b.id ? updated : x));
                        setEditingId(null); setEditing({});
                      } catch (e: any) { alert(t('buses.saveFailed', { error: e?.message || String(e) }) as any); }
                    }}>{t('common.save')}</button>
                    <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setEditingId(null); setEditing({}); }}>{t('common.cancel')}</button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm">
                    <div className="font-medium">{b.name}</div>
                    <div className="text-gray-600">{models.find((m) => m.id === (b.bus_model_id || ''))?.name || t('buses.noModel', 'No model')}</div>
                  </div>
                  <div className="flex gap-2">
                    <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} onClick={() => { setEditingId(b.id); setEditing({}); }}>{t('common.edit')}</button>
                    <button className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700" onClick={async () => {
                      if (!baseUrl || !token) return;
                      if (!window.confirm(t('buses.confirmDelete', { name: b.name }) as any)) return;
                      try {
                        const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/buses/${encodeURIComponent(b.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        setBuses((prev) => prev.filter((x) => x.id !== b.id));
                      } catch (e: any) { alert(t('buses.deleteFailed', { error: e?.message || String(e) }) as any); }
                    }}>{t('common.delete')}</button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
