import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import TripShiftPlanner from '../../TripShiftPlanner.tsx';
import SavedShiftsPanel from '../components/shifts/SavedShiftsPanel.tsx';

type ShiftStructureItem = { id: string; trip_id: string; shift_id: string; sequence_number: number };
type ShiftRead = { id: string; name: string; bus_id?: string | null; structure: ShiftStructureItem[]; updated_at?: string };
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
  const [shiftEdges, setShiftEdges] = useState<Record<string, { fromStop: string; fromTime: string; toStop: string; toTime: string }>>({});
  const [shiftEdgesLoading, setShiftEdgesLoading] = useState<Record<string, boolean>>({});
  const [refreshNonce, setRefreshNonce] = useState<number>(0);
  const [search, setSearch] = useState<string>('');
  const [filter, setFilter] = useState<'all' | 'mine'>('all');
  const [sort, setSort] = useState<'updatedDesc' | 'nameAsc'>('updatedDesc');

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
        // ignore top-level error display for now
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadShifts();
    return () => { cancelled = true; };
  }, [token, baseUrl, agencyId, refreshNonce]);

  const filtered = useMemo(() => {
    // Filter currently only by search text in name; 'mine' placeholder (no user info yet)
    const base = shifts;
    const withSearch = search ? base.filter((s) => (s.name || '').toLowerCase().includes(search.toLowerCase())) : base;
    const sorted = [...withSearch].sort((a, b) => {
      if (sort === 'nameAsc') return (a.name || '').localeCompare(b.name || '');
      // updatedDesc: use updated_at string desc; fallback by name
      const ua = a.updated_at || '';
      const ub = b.updated_at || '';
      if (ua !== ub) return (ub || '').localeCompare(ua || '');
      return (a.name || '').localeCompare(b.name || '');
    });
    return sorted;
  }, [shifts, search, sort]);

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <SavedShiftsPanel
        shifts={filtered}
        loading={loading}
        search={search}
        onSearch={setSearch}
        filter={filter}
        onFilter={setFilter}
        sort={sort}
        onSort={setSort}
        onRefresh={() => setRefreshNonce((n) => n + 1)}
        onDelete={async (s) => {
          if (!baseUrl || !token) return;
          if (!window.confirm(t('shifts.confirmDelete', { name: s.name }) as any)) return;
          try {
            const res = await fetch(joinUrl(baseUrl, `/api/v1/agency/shifts/${encodeURIComponent(s.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
            if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
            setShifts((prev) => prev.filter((x) => x.id !== s.id));
          } catch (e: any) {
            alert(t('shifts.deleteFailed', { error: e?.message || String(e) }));
          }
        }}
        summaryByShiftId={shiftEdges}
        summaryLoading={shiftEdgesLoading}
      />

      {/* Embedded planner without agency selector (moved to User page) */}
      <TripShiftPlanner embedded={true} />
    </div>
  );
}


