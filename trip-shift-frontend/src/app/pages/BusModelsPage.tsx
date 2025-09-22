import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import Panel from '../components/ui/Panel.tsx';

type BusModel = { id: string; agency_id: string; name: string; description?: string | null; specs?: any; manufacturer?: string | null };

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

export default function BusModelsPage() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [agencyId, setAgencyId] = useState<string>('');
  const [models, setModels] = useState<BusModel[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Partial<BusModel & { specsText?: string }>>({});
  const [showCreate, setShowCreate] = useState<boolean>(false);
  const [newName, setNewName] = useState<string>('');
  const [newManufacturer, setNewManufacturer] = useState<string>('');
  const [newDescription, setNewDescription] = useState<string>('');
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
      setError('');
      setLoading(true);
      const url = joinUrl(baseUrl, '/api/v1/agency/bus-models/?skip=0&limit=1000');
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as BusModel[];
      setModels(Array.isArray(all) ? all.filter((m) => m.agency_id === agencyId) : []);
    } catch (e: any) {
      setModels([]);
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadModels(); }, [token, baseUrl, agencyId]);

  return (
    <Panel>
      <div className="flex gap-2 mb-2">
        <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#002AA7'}} disabled={!token} onClick={() => setShowCreate((v) => !v)} title={!token ? (t('depots.authRequired') as any) : ''}>
          {t('busModels.createButton', showCreate ? 'Close' : 'Create model')}
        </button>
        <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#6b7280'}} disabled={!token} onClick={() => void loadModels()}>
          {t('common.refresh', 'Refresh')}
        </button>
      </div>
      {showCreate && (
        <div className="mb-3 border rounded-lg p-2 space-y-2">
          <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.name', 'Name') as string} value={newName} onChange={(e) => setNewName(e.target.value)} />
          <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.description', 'Description') as string} value={newDescription} onChange={(e) => setNewDescription(e.target.value)} />
          <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.manufacturer', 'Manufacturer') as string} value={newManufacturer} onChange={(e) => setNewManufacturer(e.target.value)} />
          <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t('busModels.form.specs', 'Specs (JSON)') as string} value={newSpecsText} onChange={(e) => setNewSpecsText(e.target.value)} />
          <div className="flex gap-2">
            <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} disabled={!token || creating || !newName.trim()} onClick={async () => {
              if (!baseUrl || !token || !agencyId) return;
              try {
                setCreating(true);
                let specs: any = {};
                if (newSpecsText.trim()) { try { specs = JSON.parse(newSpecsText); } catch { alert(t('busModels.parseError', 'Invalid JSON in specs') as any); setCreating(false); return; } }
                const res = await fetch(joinUrl(baseUrl, '/api/v1/agency/bus-models/'), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ agency_id: agencyId, name: newName, description: newDescription || null, manufacturer: newManufacturer || null, specs }) });
                if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                const created = (await res.json()) as BusModel;
                setModels((prev) => [created, ...prev]);
                setNewName(''); setNewDescription(''); setNewManufacturer(''); setNewSpecsText(''); setShowCreate(false);
              } catch (e: any) { alert(t('busModels.createFailed', { error: e?.message || String(e) }) as any); } finally { setCreating(false); }
            }}>{t('common.create', 'Create')}</button>
            <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setShowCreate(false); setNewName(''); setNewDescription(''); setNewManufacturer(''); setNewSpecsText(''); }}>{t('common.cancel')}</button>
          </div>
        </div>
      )}
      <div className="text-sm text-gray-700 flex items-center justify-between">
        <span>{t('busModels.listTitle', 'Models')}</span>
        {loading && <span className="text-xs text-gray-500">{t('common.loading')}</span>}
      </div>
      {error && <div className="text-sm text-red-600">{error}</div>}
      {(!loading && models.length === 0) ? (
        <div className="text-sm text-gray-600">{t('busModels.empty', 'No models')}</div>
      ) : (
        <ul className="space-y-2 mt-2">
          {models.map((m) => (
            <li key={m.id} className="border rounded-lg p-2">
              {editingId === m.id ? (
                <div className="space-y-2">
                  <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.name', 'Name') as string} value={(editing.name as string) ?? m.name} onChange={(e) => setEditing((prev) => ({ ...prev, name: e.target.value }))} />
                  <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.description', 'Description') as string} value={(editing.description as string) ?? (m.description || '')} onChange={(e) => setEditing((prev) => ({ ...prev, description: e.target.value }))} />
                  <input className="w-full px-2 py-1 border rounded" placeholder={t('busModels.form.manufacturer', 'Manufacturer') as string} value={(editing.manufacturer as string) ?? (m.manufacturer || '')} onChange={(e) => setEditing((prev) => ({ ...prev, manufacturer: e.target.value }))} />
                  <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t('busModels.form.specs', 'Specs (JSON)') as string} value={(editing.specsText as string) ?? JSON.stringify(m.specs ?? {}, null, 2)} onChange={(e) => setEditing((prev) => ({ ...prev, specsText: e.target.value }))} />
                  <div className="flex gap-2">
                    <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} onClick={async () => {
                      if (!baseUrl || !token) return;
                      try {
                        const payload: any = { agency_id: agencyId };
                        if (editing.name !== undefined) payload.name = editing.name;
                        if (editing.description !== undefined) payload.description = editing.description;
                        if (editing.manufacturer !== undefined) payload.manufacturer = editing.manufacturer;
                        if (editing.specsText !== undefined) { try { payload.specs = editing.specsText ? JSON.parse(editing.specsText as string) : {}; } catch { alert(t('busModels.parseError', 'Invalid JSON in specs') as any); return; } }
                        const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/bus-models/${encodeURIComponent(m.id)}`), { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload) });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        const updated = (await res.json()) as BusModel;
                        setModels((prev) => prev.map((x) => x.id === m.id ? updated : x));
                        setEditingId(null); setEditing({});
                      } catch (e: any) { alert(t('busModels.saveFailed', { error: e?.message || String(e) }) as any); }
                    }}>{t('common.save')}</button>
                    <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setEditingId(null); setEditing({}); }}>{t('common.cancel')}</button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm">
                    <div className="font-medium">{m.name}</div>
                    <div className="text-gray-600">{m.description || ''}</div>
                    <div className="text-gray-600">{m.manufacturer || ''}</div>
                  </div>
                  <div className="flex gap-2">
                    <button 
                      className="px-2 py-1 rounded text-white text-sm hover:opacity-90 relative group" 
                      style={{backgroundColor: '#002AA7'}} 
                      onClick={() => { setEditingId(m.id); setEditing({}); }}
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
                        if (!window.confirm(t('busModels.confirmDelete', { name: m.name }) as any)) return;
                        try {
                          const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/bus-models/${encodeURIComponent(m.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                          if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                          setModels((prev) => prev.filter((x) => x.id !== m.id));
                        } catch (e: any) { alert(t('busModels.deleteFailed', { error: e?.message || String(e) }) as any); }
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
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
