import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import TripShiftPlanner from '../../TripShiftPlanner.tsx';

type ShiftStructureItem = { id: string; trip_id: string; shift_id: string; sequence_number: number };
type ShiftRead = { id: string; name: string; bus_id?: string | null; structure: ShiftStructureItem[] };
type TripStop = { id: string; stop_name: string; arrival_time?: string | null; departure_time?: string | null };
// removed unused UserMe

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

export default function ShiftsPage() {
  const { t } = useTranslation();
  const { token, agencyId } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [shifts, setShifts] = useState<ShiftRead[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [shiftEdges, setShiftEdges] = useState<Record<string, { fromStop: string; fromTime: string; toStop: string; toTime: string }>>({});
  const [shiftEdgesLoading, setShiftEdgesLoading] = useState<Record<string, boolean>>({});
  const [refreshNonce, setRefreshNonce] = useState<number>(0);

  async function fetchShiftEdges(shift: ShiftRead) {
    try {
      if (!shift || !shift.structure || shift.structure.length === 0) return;
      const firstTripId = shift.structure[0].trip_id;
      const lastTripId = shift.structure[shift.structure.length - 1].trip_id;
      setShiftEdgesLoading((prev) => ({ ...prev, [shift.id]: true }));
      const urlFirst = joinUrl(baseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(firstTripId)}`);
      const urlLast = joinUrl(baseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(lastTripId)}`);
      const [resFirst, resLast] = await Promise.all([
        fetch(urlFirst, { headers: token ? { Authorization: `Bearer ${token}` } : undefined }),
        fetch(urlLast, { headers: token ? { Authorization: `Bearer ${token}` } : undefined }),
      ]);
      if (resFirst.ok && resLast.ok) {
        const firstStops = (await resFirst.json()) as TripStop[];
        const lastStops = (await resLast.json()) as TripStop[];
        const first = firstStops?.[0];
        const last = lastStops?.[lastStops.length - 1];
        const fromStop = first?.stop_name || '';
        const fromTime = (first?.departure_time || first?.arrival_time || '').slice(0, 5);
        const toStop = last?.stop_name || '';
        const toTime = (last?.arrival_time || last?.departure_time || '').slice(0, 5);
        setShiftEdges((prev) => ({ ...prev, [shift.id]: { fromStop, fromTime, toStop, toTime } }));
      }
    } catch {
      // ignore per-item errors
    } finally {
      setShiftEdgesLoading((prev) => ({ ...prev, [shift.id]: false }));
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function loadShifts() {
      if (!token || !baseUrl || !agencyId) return;
      try {
        setError('');
        setLoading(true);
        const url = joinUrl(baseUrl, `/api/v1/agency/shifts/?skip=0&limit=1000&agency_id=${encodeURIComponent(agencyId)}`);
        const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const all = (await res.json()) as ShiftRead[];
        if (!cancelled) {
          setShifts(Array.isArray(all) ? all : []);
          const toPreload = (Array.isArray(all) ? all.slice(0, 10) : []);
          for (const s of toPreload) void fetchShiftEdges(s);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadShifts();
    return () => { cancelled = true; };
  }, [token, baseUrl, agencyId, refreshNonce]);

  return (
    <div className="space-y-4">
      <div className="p-3 rounded-2xl bg-white shadow-sm border">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-medium">{t('shifts.listTitle', 'Saved shifts')}</h2>
          <div className="flex items-center gap-2">
            {loading && <span className="text-xs text-gray-500">{t('common.loading')}</span>}
            <button
              className="px-2 py-1 rounded text-white text-xs hover:opacity-90 disabled:opacity-50"
              style={{backgroundColor: '#6b7280'}}
              disabled={!token || !agencyId}
              onClick={() => setRefreshNonce((n) => n + 1)}
              title={!token ? (t('depots.authRequired') as any) : (!agencyId ? (t('depots.selectAgencyBackend') as any) : '')}
            >
              {t('common.refresh', 'Refresh')}
            </button>
          </div>
        </div>
        {error && <div className="text-sm text-red-600">{error}</div>}
        {(!loading && shifts.length === 0) ? (
          <div className="text-sm text-gray-600">{t('shifts.empty', 'No shifts')}</div>
        ) : (
          <ul className="space-y-2">
            {shifts.map((s) => (
              <li key={s.id} className="border rounded-lg p-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm">
                    <div className="font-medium">{s.name}</div>
                    <div className="text-gray-600">{t('shifts.tripCount', { count: s.structure?.length || 0 })}</div>
                    <div className="text-gray-600">
                      {(() => {
                        const edge = shiftEdges[s.id];
                        const isLoading = shiftEdgesLoading[s.id];
                        if (isLoading) return <span className="text-xs text-gray-500">{t('common.loading')}</span>;
                        if (!edge) { void fetchShiftEdges(s); return <span className="text-xs text-gray-500">{t('common.loading')}</span>; }
                        return <>{t('shifts.summary', edge as any)}</>;
                      })()}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700" onClick={async () => {
                      if (!baseUrl || !token) return;
                      if (!window.confirm(t('shifts.confirmDelete', { name: s.name }) as any)) return;
                      try {
                        const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/shifts/${encodeURIComponent(s.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        setShifts((prev) => prev.filter((x) => x.id !== s.id));
                      } catch (e: any) { alert(t('shifts.deleteFailed', { error: e?.message || String(e) })); }
                    }}>{t('common.delete')}</button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Embedded planner without agency selector (moved to User page) */}
      <TripShiftPlanner embedded={true} />
    </div>
  );
}


