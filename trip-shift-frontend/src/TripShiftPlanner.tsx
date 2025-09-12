import React, { useMemo, useState, useEffect, useRef } from "react";

/**
 * Trip Shift Planner — single‑file React demo (TypeScript + Tailwind)
 *
 * ✔ Sorts trips by departure time (supports GTFS times > 24h)
 * ✔ User builds a bus shift; valid next trips must:
 *      - start at the previous trip's end stop, and
 *      - have departure_time >= previous arrival_time
 * ✔ File upload, sample data, free‑URL fetch
 * ✔ NEW: Backend integration (login with email/password or paste Bearer token)
 * ✔ NEW: Route + day filter, like your Python snippet
 * ✔ Self‑tests for core utilities (see bottom of the page)
 */

// ---------- Types ----------
export type Trip = {
  id: string;
  route_id: string;
  service_id: string;
  gtfs_service_id: string;
  trip_id: string;
  trip_headsign: string;
  trip_short_name: string;
  direction_id: number;
  block_id?: string | null;
  shape_id: string;
  wheelchair_accessible: number;
  bikes_allowed: number;
  start_stop_name: string;
  end_stop_name: string;
  departure_time: string; // GTFS HH:MM:SS, hours may exceed 24
  arrival_time: string;   // GTFS HH:MM:SS, hours may exceed 24
};

export type TripX = Trip & {
  departure_sec: number;
  arrival_sec: number;
};

type Agency = {
  id: string;
  gtfs_agency_id: string;
  agency_name: string;
};

type RouteRead = {
  id: string; // database UUID for route
  route_id: string; // GTFS route_id
  agency_id: string;
  route_short_name?: string | null;
  route_long_name?: string | null;
};

type TripStop = {
  id: string;
  stop_id: string;
  stop_name: string;
  arrival_time?: string | null;
  departure_time?: string | null;
};

// ---------- Utils ----------
function parseGtfsTimeToSeconds(t: string): number {
  // Accepts strings like "08:42:00" or "25:13:00" (hours > 24 allowed)
  if (!t) return Number.NaN;
  const parts = t.split(":").map((x) => parseInt(x, 10));
  const h = parts[0] || 0;
  const m = parts[1] || 0;
  const s = parts[2] || 0;
  return h * 3600 + m * 60 + s;
}

function formatDayHHMM(totalSec: number): string {
  if (!Number.isFinite(totalSec)) return "";
  const day = Math.floor(totalSec / 86400);
  const within = totalSec % 86400;
  const hh = Math.floor(within / 3600);
  const mm = Math.floor((within % 3600) / 60);
  const dayPrefix = day > 0 ? `D+${day} ` : "";
  return `${dayPrefix}${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function normalizeStop(s: string): string {
  return (s || "").trim();
}

function computeValidNext(prev: TripX | null, cand: TripX): boolean {
  if (!prev) return true; // any first trip is valid
  const sameStop = normalizeStop(cand.start_stop_name) === normalizeStop(prev.end_stop_name);
  const timeOk = cand.departure_sec >= prev.arrival_sec;
  return sameStop && timeOk;
}

function enrichTrips(trips: Trip[]): TripX[] {
  return trips
    .map((t) => ({
      ...t,
      departure_sec: parseGtfsTimeToSeconds(t.departure_time),
      arrival_sec: parseGtfsTimeToSeconds(t.arrival_time),
    }))
    .filter((t) => Number.isFinite(t.departure_sec) && Number.isFinite(t.arrival_sec));
}

function joinUrl(base: string, path: string): string {
  const cleanBase = (base || "").replace(/\/+$/, "");
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
}

// ---------- Sample (small) ----------
const SAMPLE_TRIPS: Trip[] = [
  {
    id: "d97bd032-fbde-44c9-a9ca-92466c8f99c0",
    route_id: "55f151ad-63f8-40dc-85e7-330becb51c75",
    service_id: "ab8a4b2b-f94e-44d3-9708-74a872dc77fc",
    gtfs_service_id: "TA+qo500",
    trip_id: "1.TA.91-15-E-j25-1.1.H",
    trip_headsign: "Zürich, Bahnhofplatz/HB",
    trip_short_name: "12148",
    direction_id: 0,
    block_id: null,
    shape_id: "shp_900_905",
    wheelchair_accessible: 0,
    bikes_allowed: 0,
    start_stop_name: "Zürich, Bucheggplatz",
    end_stop_name: "Zürich, Bahnhofplatz/HB",
    departure_time: "12:58:00",
    arrival_time: "13:09:00",
  },
  {
    id: "e02003e8-0fae-4dbb-acbc-b0bf2fd5b0e5",
    route_id: "55f151ad-63f8-40dc-85e7-330becb51c75",
    service_id: "97c3685f-be31-4849-b3db-74ed8fbdf786",
    gtfs_service_id: "TA+js8b0",
    trip_id: "102.TA.91-15-E-j25-1.5.H",
    trip_headsign: "Zürich Stadelhofen, Bahnhof",
    trip_short_name: "16199",
    direction_id: 0,
    block_id: null,
    shape_id: "shp_900_906",
    wheelchair_accessible: 0,
    bikes_allowed: 0,
    start_stop_name: "Zürich, Bucheggplatz",
    end_stop_name: "Zürich Stadelhofen, Bahnhof",
    departure_time: "14:58:00",
    arrival_time: "15:13:00",
  },
  {
    id: "fb3dc4e4-8dc3-4f4d-926d-aedf995014b2",
    route_id: "55f151ad-63f8-40dc-85e7-330becb51c75",
    service_id: "791ce0e2-e1dc-4559-899c-49a79932c546",
    gtfs_service_id: "TA+7T",
    trip_id: "1020.TA.91-15-E-j25-1.14.H",
    trip_headsign: "Zürich, Klusplatz",
    trip_short_name: "4886",
    direction_id: 0,
    block_id: null,
    shape_id: "shp_900_792",
    wheelchair_accessible: 0,
    bikes_allowed: 0,
    start_stop_name: "Stettbach, Bahnhof",
    end_stop_name: "Zürich, Klusplatz",
    departure_time: "08:42:00",
    arrival_time: "09:16:00",
  },
  {
    id: "d2884621-b6ce-4522-b3d3-5492a77607f8",
    route_id: "55f151ad-63f8-40dc-85e7-330becb51c75",
    service_id: "791ce0e2-e1dc-4559-899c-49a79932c546",
    gtfs_service_id: "TA+7T",
    trip_id: "1021.TA.91-15-E-j25-1.14.H",
    trip_headsign: "Zürich, Klusplatz",
    trip_short_name: "13577",
    direction_id: 0,
    block_id: null,
    shape_id: "shp_900_792",
    wheelchair_accessible: 0,
    bikes_allowed: 0,
    start_stop_name: "Stettbach, Bahnhof",
    end_stop_name: "Zürich, Klusplatz",
    departure_time: "13:42:00",
    arrival_time: "14:16:00",
  },
  // add a cross‑day example
  {
    id: "x-overnight-001",
    route_id: "demo",
    service_id: "demo",
    gtfs_service_id: "demo",
    trip_id: "overnight.1",
    trip_headsign: "Overnight Hub",
    trip_short_name: "ON1",
    direction_id: 0,
    block_id: null,
    shape_id: "demo",
    wheelchair_accessible: 1,
    bikes_allowed: 1,
    start_stop_name: "Zürich Stadelhofen, Bahnhof",
    end_stop_name: "Overnight Hub",
    departure_time: "25:10:00", // D+1 01:10
    arrival_time: "25:55:00",   // D+1 01:55
  },
];

// ---------- Env helpers (Vite/Next) ----------
const VITE = (typeof import.meta !== "undefined" ? (import.meta as any).env : {}) || {};
const ENV_BASE_URL: string = VITE.VITE_API_BASE_URL || "";
const ENV_TOKEN: string = VITE.VITE_API_TOKEN || "";
const ENV_ROUTE_ID: string = VITE.VITE_TEST_ROUTE_ID || "";
const ENV_DAY: string = VITE.VITE_DAY || "monday";

const DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"] as const;

// ---------- Main Component ----------
export default function TripShiftPlanner() {
  const [rawTrips, setRawTrips] = useState<Trip[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [onlyValidNext, setOnlyValidNext] = useState<boolean>(true);
  const [hideUsed, setHideUsed] = useState<boolean>(true);
  const [textFilter, setTextFilter] = useState<string>("");

  // Free-URL fetch (optional)
  const [restUrl, setRestUrl] = useState<string>("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Backend integration — auto-configured base URL
  const effectiveBaseUrl = useMemo(() => {
    if (ENV_BASE_URL) return ENV_BASE_URL;
    if (typeof window !== "undefined") {
      const host = window.location.hostname;
      if (host === "localhost" || host === "127.0.0.1") return "http://localhost:8002";
      if (/^10\./.test(host)) return `http://${host}:8002`;
      if (host === "isaac-elettra.dacd.supsi.ch") return "http://isaac-elettra.dacd.supsi.ch:8002";
    }
    // fallback sensible default
    return "http://localhost:8002";
  }, []);
  const [routeId] = useState<string>(ENV_ROUTE_ID);
  const [day, setDay] = useState<string>(ENV_DAY);
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [token, setToken] = useState<string>(ENV_TOKEN);
  const [authInfo, setAuthInfo] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);

  // Agency/Route selection
  const [agencies, setAgencies] = useState<Agency[]>([]);
  const [agencyId, setAgencyId] = useState<string>(""); // database UUID for agency
  const [agencySearch, setAgencySearch] = useState<string>("");
  const [routes, setRoutes] = useState<RouteRead[]>([]);
  const [routeDbId, setRouteDbId] = useState<string>(ENV_ROUTE_ID); // database UUID for route

  // Hovered trip and stops cache
  const [hoveredTripId, setHoveredTripId] = useState<string | null>(null); // database trip id (UUID)
  const [stopsByTrip, setStopsByTrip] = useState<Record<string, TripStop[]>>({}); // keyed by database trip id
  const [stopsLoadingTripId, setStopsLoadingTripId] = useState<string | null>(null); // database trip id
  const [stopsErrorByTrip, setStopsErrorByTrip] = useState<Record<string, string>>({});
  const hoverTimerRef = useRef<number | null>(null);

  // Precompute enriched + sorted list
  const tripsX = useMemo(() => enrichTrips(rawTrips), [rawTrips]);
  const sortedTrips = useMemo(() => {
    return [...tripsX].sort((a, b) => {
      if (a.departure_sec !== b.departure_sec) return a.departure_sec - b.departure_sec;
      return a.id.localeCompare(b.id);
    });
  }, [tripsX]);

  const selectedTrips = useMemo(() => {
    const map = new Map(sortedTrips.map((t) => [t.id, t] as const));
    return selectedIds.map((id) => map.get(id)).filter((x): x is TripX => Boolean(x));
  }, [selectedIds, sortedTrips]);

  const lastSelected: TripX | null = selectedTrips.length > 0 ? selectedTrips[selectedTrips.length - 1] : null;

  const used = useMemo(() => new Set(selectedIds), [selectedIds]);

  const nextCandidates = useMemo(() => {
    const base = sortedTrips.filter((t) => (hideUsed ? !used.has(t.id) : true));
    const withText = textFilter
      ? base.filter((t) =>
          (t.start_stop_name + " " + t.end_stop_name + " " + t.trip_headsign + " " + t.trip_short_name)
            .toLowerCase()
            .includes(textFilter.toLowerCase())
        )
      : base;
    return onlyValidNext && lastSelected
      ? withText.filter((t) => computeValidNext(lastSelected, t))
      : withText;
  }, [sortedTrips, used, hideUsed, textFilter, onlyValidNext, lastSelected]);

  // Pagination for available trips
  const [pageSize, setPageSize] = useState<number>(20);
  const [page, setPage] = useState<number>(1);
  const totalPages = Math.max(1, Math.ceil(nextCandidates.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, nextCandidates.length);
  const pagedNextCandidates = useMemo(
    () => nextCandidates.slice(startIndex, endIndex),
    [nextCandidates, startIndex, endIndex]
  );
  useEffect(() => {
    setPage(1);
  }, [onlyValidNext, hideUsed, textFilter, lastSelected, pageSize]);

  // --- Actions ---
  function handlePickTrip(t: TripX) {
    // Return a click handler (curried); this fixes prior syntax error
    return () => {
      if (selectedIds.length > 0 && !computeValidNext(lastSelected, t)) {
        alert("This trip doesn't follow the last selection (stop or time rules).");
        return;
      }
      setSelectedIds((prev) => (prev.includes(t.id) ? prev : [...prev, t.id]));
    };
  }

  function handleUndo() {
    setSelectedIds((prev) => prev.slice(0, -1));
  }

  function handleReset() {
    setSelectedIds([]);
  }

  function handleExport() {
    if (selectedTrips.length === 0) {
      alert("No trips selected to export.");
      return;
    }
    try {
      const data = JSON.stringify(selectedTrips, null, 2);
      const blob = new Blob([data], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `shift_${Date.now()}.json`;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        if (a.parentNode) a.parentNode.removeChild(a);
      }, 1000);
    } catch (e: any) {
      alert(`Export failed: ${e?.message || e}`);
    }
  }

  async function handleFetchFreeUrl() {
    if (!restUrl) return;
    try {
      setLoading(true);
      const res = await fetch(restUrl);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as Trip[];
      if (!Array.isArray(data)) throw new Error("Response is not an array");
      setRawTrips(data);
      setSelectedIds([]);
    } catch (err: any) {
      alert(`Fetch failed: ${err?.message || err}`);
    } finally {
      setLoading(false);
    }
  }

  function handleFileUpload(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(String(reader.result));
        if (!Array.isArray(data)) throw new Error("File JSON is not an array");
        setRawTrips(data);
        setSelectedIds([]);
      } catch (e: any) {
        alert(`Invalid JSON: ${e?.message || e}`);
      }
    };
    reader.readAsText(file);
  }

  async function login() {
    if (!effectiveBaseUrl) {
      alert("Base URL required");
      return;
    }
    if (token) {
      setAuthInfo(`Using pasted token (${token.slice(0, 8)}… )`);
      return;
    }
    if (!email || !password) {
      alert("Provide email and password or paste a token");
      return;
    }
    try {
      setLoading(true);
      const res = await fetch(joinUrl(effectiveBaseUrl, "/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data: any = await res.json();
      const tok = data?.access_token;
      if (!tok) throw new Error("No access_token in response");
      setToken(tok);
      setAuthInfo(`Logged in. Token ${tok.slice(0, 12)}…`);
    } catch (e: any) {
      alert(`Login failed: ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadByRouteDay() {
    const selectedRouteId = routeDbId || routeId; // support legacy env for now
    if (!effectiveBaseUrl || !selectedRouteId) {
      alert("Base URL and Route selection required");
      return;
    }
    try {
      setLoading(true);
      const url = joinUrl(
        effectiveBaseUrl,
        `/api/v1/gtfs/gtfs-trips/by-route/${selectedRouteId}${day ? `?day_of_week=${encodeURIComponent(day)}` : ""}`
      );
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as Trip[];
      if (!Array.isArray(data)) throw new Error("Response is not an array");
      setRawTrips(data);
      setSelectedIds([]);
    } catch (e: any) {
      alert(`Fetch failed: ${e?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  // ---- Agencies/Routes fetching ----
  async function fetchAgencies() {
    if (!effectiveBaseUrl || !token) return;
    try {
      const url = joinUrl(effectiveBaseUrl, "/api/v1/agency/agencies/?limit=1000");
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as Agency[];
      setAgencies(Array.isArray(data) ? data : []);
    } catch (e) {
      // silent fail; user might not be logged in yet
    }
  }

  async function fetchRoutesByAgency(aid: string) {
    if (!effectiveBaseUrl || !token || !aid) return;
    try {
      const url = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/gtfs-routes/by-agency/${encodeURIComponent(aid)}`);
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as RouteRead[];
      const sorted = (Array.isArray(data) ? data : []).sort((a, b) => {
        const sa = (a.route_short_name || a.route_long_name || a.route_id || "").toString();
        const sb = (b.route_short_name || b.route_long_name || b.route_id || "").toString();
        return sa.localeCompare(sb, undefined, { numeric: true, sensitivity: "base" });
      });
      setRoutes(sorted);
    } catch (e) {
      setRoutes([]);
    }
  }

  // When token becomes available, load agencies
  useEffect(() => {
    if (token) {
      fetchAgencies();
    }
  }, [token, effectiveBaseUrl]);

  // When agency changes, load routes and clear selection
  useEffect(() => {
    if (agencyId) {
      setRouteDbId("");
      fetchRoutesByAgency(agencyId);
    } else {
      setRoutes([]);
    }
  }, [agencyId, token, effectiveBaseUrl]);

  // Filter and sort agencies by name/id
  const filteredAgencies = useMemo(() => {
    const label = (a: Agency) => (a.agency_name || a.gtfs_agency_id || "").toString();
    const q = agencySearch.trim().toLowerCase();
    const list = q ? agencies.filter((a) => label(a).toLowerCase().includes(q)) : agencies.slice();
    return list.sort((a, b) => label(a).localeCompare(label(b), undefined, { sensitivity: "base" }));
  }, [agencies, agencySearch]);

  async function ensureStopsForTrip(tripDbId: string) {
    if (!tripDbId) return;
    if (stopsByTrip[tripDbId]) return; // cached
    if (!effectiveBaseUrl) return;
    try {
      setStopsLoadingTripId(tripDbId);
      const url = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(tripDbId)}`);
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as TripStop[];
      setStopsByTrip((prev) => ({ ...prev, [tripDbId]: data }));
    } catch (e: any) {
      setStopsErrorByTrip((prev) => ({ ...prev, [tripDbId]: e?.message || String(e) }));
    } finally {
      setStopsLoadingTripId((prev) => (prev === tripDbId ? null : prev));
    }
  }

  // ---------- Self-tests (lightweight runtime checks) ----------
  type TestResult = { name: string; pass: boolean; msg?: string };
  const [tests, setTests] = useState<TestResult[]>([]);
  useEffect(() => {
    const out: TestResult[] = [];
    // 1) GTFS time parsing
    const t = parseGtfsTimeToSeconds("25:13:00");
    out.push({ name: "parse 25:13:00", pass: t === (25 * 3600 + 13 * 60), msg: `got ${t}` });
    // 2) Valid next rule — same end/start stop and time ordering
    const a: TripX = {
      ...(SAMPLE_TRIPS[0] as Trip),
      departure_sec: parseGtfsTimeToSeconds("08:00:00"),
      arrival_sec: parseGtfsTimeToSeconds("09:00:00"),
      start_stop_name: "A",
      end_stop_name: "B",
    } as TripX;
    const b: TripX = {
      ...(SAMPLE_TRIPS[1] as Trip),
      departure_sec: parseGtfsTimeToSeconds("09:05:00"),
      arrival_sec: parseGtfsTimeToSeconds("09:30:00"),
      start_stop_name: "B",
      end_stop_name: "C",
    } as TripX;
    out.push({ name: "valid next B after A->B", pass: computeValidNext(a, b) === true });
    const c: TripX = { ...b, start_stop_name: "X" };
    out.push({ name: "invalid next different stop", pass: computeValidNext(a, c) === false });
    setTests(out);
  }, []);

  // ---------- UI ----------
  return (
    <div className="min-h-screen w-full bg-gray-50 text-gray-900">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
        <div className="mx-auto max-w-7xl px-4 py-3 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Trip Shift Planner</h1>
            <p className="text-sm text-gray-600">Select trips sequentially to build a bus shift. Times support GTFS hours &gt; 24.</p>
          </div>

          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <div className="flex items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept="application/json"
                onChange={handleFileUpload}
                className="block text-sm"
                title="Upload trips JSON"
              />
              <button
                onClick={() => {
                  setRawTrips(SAMPLE_TRIPS);
                  setSelectedIds([]);
                  if (fileRef.current) fileRef.current.value = "";
                }}
                className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm"
                title="Load sample"
              >
                Load sample
              </button>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="url"
                placeholder="https://api.example.com/trips"
                value={restUrl}
                onChange={(e) => setRestUrl(e.target.value)}
                className="px-3 py-2 border rounded-lg text-sm w-72"
              />
              <button onClick={handleFetchFreeUrl} className="px-3 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700">
                Fetch
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: Controls */}
        <section className="lg:col-span-1 space-y-4">
          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">Filters</h2>
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={onlyValidNext}
                    onChange={(e) => setOnlyValidNext(e.target.checked)}
                  />
                  Only show valid next trips
                </label>
              </div>
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={hideUsed} onChange={(e) => setHideUsed(e.target.checked)} /> Hide already selected
                </label>
              </div>
              <div>
                <input
                  type="text"
                  placeholder="Search text"
                  value={textFilter}
                  onChange={(e) => setTextFilter(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg"
                />
              </div>
            </div>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">Backend</h2>
            <div className="space-y-2 text-sm">
              <div className="w-full px-3 py-2 border rounded-lg text-xs text-gray-600">Backend: auto-configured</div>
              <div className="grid grid-cols-2 gap-2">
                <input className="px-3 py-2 border rounded-lg" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
                <input className="px-3 py-2 border rounded-lg" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <input className="flex-1 px-3 py-2 border rounded-lg" placeholder="Paste Bearer token (optional)" value={token} onChange={(e) => setToken(e.target.value)} />
                <button onClick={login} className="px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm hover:bg-indigo-700" disabled={loading}>Login</button>
              </div>
              {authInfo && <div className="text-xs text-gray-600">{authInfo}</div>}

              <div className="mt-2">
                <input
                  className="w-full px-3 py-2 border rounded-lg"
                  placeholder="Search agency"
                  value={agencySearch}
                  onChange={(e) => setAgencySearch(e.target.value)}
                  disabled={!token}
                />
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <select
                  className="px-3 py-2 border rounded-lg"
                  value={agencyId}
                  onChange={(e) => setAgencyId(e.target.value)}
                  disabled={!token}
                >
                  <option value="">{token ? "Select agency" : "Login or paste token first"}</option>
                  {filteredAgencies.map((a) => (
                    <option key={a.id} value={a.id}>{a.agency_name || a.gtfs_agency_id}</option>
                  ))}
                </select>
                <select
                  className="px-3 py-2 border rounded-lg"
                  value={routeDbId}
                  onChange={(e) => setRouteDbId(e.target.value)}
                  disabled={!agencyId || routes.length === 0}
                >
                  <option value="">{agencyId ? (routes.length ? "Select route" : "Loading routes…") : "Select agency first"}</option>
                  {routes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {(r.route_short_name || r.route_long_name || r.route_id) as string}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <select className="px-3 py-2 border rounded-lg" value={day} onChange={(e) => setDay(e.target.value)}>
                  {DAYS.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>
              <button onClick={loadByRouteDay} className="px-3 py-2 rounded-lg bg-emerald-600 text-white text-sm hover:bg-emerald-700 w-full" disabled={loading || !(routeDbId || routeId)}>
                Load trips by route + day
              </button>
            </div>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">Selection summary</h2>
            <ul className="space-y-2 text-sm">
              <li>
                <span className="text-gray-600">Trips loaded:</span> {sortedTrips.length}
              </li>
              <li>
                <span className="text-gray-600">Selected:</span> {selectedTrips.length}
              </li>
              <li>
                <span className="text-gray-600">Next candidates:</span> {nextCandidates.length}
              </li>
              {lastSelected && (
                <li>
                  <span className="text-gray-600">Last arrival:</span> {formatDayHHMM(lastSelected.arrival_sec)} at {normalizeStop(lastSelected.end_stop_name)}
                </li>
              )}
            </ul>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">Self‑tests</h2>
            <ul className="text-xs space-y-1">
              {tests.map((t, i) => (
                <li key={i} className={t.pass ? "text-emerald-700" : "text-red-700"}>
                  {t.pass ? "✔" : "✘"} {t.name}{t.msg ? ` — ${t.msg}` : ""}
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* Middle: Available trips */}
        <section className="lg:col-span-2 p-3 rounded-2xl bg-white shadow-sm border min-h-[60vh] flex flex-col">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-lg font-medium">Available trips</h2>
            <span className="text-sm text-gray-600">(sorted by departure time){loading ? " · loading…" : ""}</span>
          </div>
          <div className="flex items-center justify-between mb-2 text-sm">
            <div>
              Showing {nextCandidates.length === 0 ? 0 : startIndex + 1}–{endIndex} of {nextCandidates.length}
            </div>
            <div className="flex items-center gap-2">
              <label className="text-gray-600">Per page</label>
              <select
                className="px-2 py-1 border rounded"
                value={pageSize}
                onChange={(e) => setPageSize(parseInt(e.target.value, 10) || 20)}
              >
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
              <div className="ml-2 flex items-center gap-2">
                <button
                  className="px-2 py-1 border rounded disabled:opacity-50"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage <= 1}
                >
                  Prev
                </button>
                <span className="text-gray-600">Page {currentPage} / {totalPages}</span>
                <button
                  className="px-2 py-1 border rounded disabled:opacity-50"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={currentPage >= totalPages}
                >
                  Next
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-1 overflow-auto pr-1">
            {rawTrips.length > 0 && pagedNextCandidates.map((t) => (
              <TripCard
                key={t.id}
                t={t}
                disabled={selectedIds.length > 0 && !computeValidNext(lastSelected, t)}
                used={used.has(t.id)}
                onPick={handlePickTrip(t)}
                onHover={() => {
                  if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current);
                  hoverTimerRef.current = window.setTimeout(() => {
                    setHoveredTripId(t.id); // use database id (UUID)
                    ensureStopsForTrip(t.id);
                  }, 1000);
                }}
                onLeave={() => {
                  if (hoverTimerRef.current) {
                    window.clearTimeout(hoverTimerRef.current);
                    hoverTimerRef.current = null;
                  }
                  setHoveredTripId((prev) => (prev === t.id ? null : prev));
                }}
                hovered={hoveredTripId === t.id}
                stops={stopsByTrip[t.id]}
                stopsLoading={stopsLoadingTripId === t.id}
                stopsError={stopsErrorByTrip[t.id]}
              />
            ))}
            {rawTrips.length === 0 ? (
              <div className="text-sm text-gray-600">No trips loaded yet. Login and select an agency and route, then click "Load trips by route + day".</div>
            ) : pagedNextCandidates.length === 0 ? (
              <div className="text-sm text-gray-600">No trips match the current filters. Try disabling "Only show valid next trips" or clearing the search.</div>
            ) : null}
          </div>
        </section>

        {/* Right: Selected shift */}
        <section className="lg:col-span-3 p-4 rounded-2xl bg-white shadow-sm border">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-lg font-medium">Selected shift</h2>
            <span className="text-sm text-gray-600">Click a trip on the left to append here</span>
          </div>
          {selectedTrips.length === 0 ? (
            <div className="text-sm text-gray-600">Nothing selected yet.</div>
          ) : (
            <ol className="space-y-3">
              {selectedTrips.map((t, idx) => (
                <li key={t.id} className="p-3 border rounded-xl flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <div className="font-medium">{t.start_stop_name} → {t.end_stop_name}</div>
                    <div className="text-xs text-gray-600">#{idx + 1}</div>
                  </div>
                  <div className="text-sm">
                    <span className="inline-block mr-4">Dep: {formatDayHHMM(t.departure_sec)}</span>
                    <span>Arr: {formatDayHHMM(t.arrival_sec)}</span>
                  </div>
                  <div className="text-xs text-gray-600">
                    Route: {t.route_id} · Trip: {t.trip_short_name || t.trip_id} · Headsign: {t.trip_headsign}
                  </div>
                </li>
              ))}
            </ol>
          )}
          <div className="mt-3 flex gap-2 text-sm">
            <button onClick={handleUndo} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200">Undo last</button>
            <button onClick={handleReset} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200">Reset</button>
            <button onClick={handleExport} className="px-3 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700" disabled={selectedTrips.length === 0}>Export selection</button>
          </div>
        </section>
      </main>
    </div>
  );
}

// ---------- Card ----------
function TripCard({
  t,
  disabled,
  used,
  onPick,
  onHover,
  onLeave,
  hovered,
  stops,
  stopsLoading,
  stopsError,
}: {
  t: TripX;
  disabled?: boolean;
  used?: boolean;
  onPick: () => void;
  onHover?: () => void;
  onLeave?: () => void;
  hovered?: boolean;
  stops?: TripStop[];
  stopsLoading?: boolean;
  stopsError?: string;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onPick}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      className={
        "text-left p-2 rounded-lg border shadow-sm transition " +
        (disabled
          ? "bg-gray-100 text-gray-400 cursor-not-allowed"
          : used
          ? "bg-yellow-50 hover:bg-yellow-100"
          : "bg-white hover:bg-blue-50")
      }
      title={disabled ? "Doesn't follow from the last selected trip" : "Add to shift"}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-medium truncate">{t.start_stop_name} → {t.end_stop_name}</div>
        <div className="text-xs whitespace-nowrap text-gray-700">
          {formatDayHHMM(t.departure_sec)} → {formatDayHHMM(t.arrival_sec)}
        </div>
      </div>
      <div className="mt-0.5 text-[11px] text-gray-600 truncate">
        Headsign: {t.trip_headsign}
      </div>
      <div className="text-[11px] text-gray-600 truncate">
        Route: {t.route_id} · Trip: {t.trip_short_name || t.trip_id}
      </div>
      {hovered && (
        <div className="mt-2 border-t pt-2">
          <div className="text-[11px] font-medium text-gray-700 mb-1">Stops (Arr · Dep)</div>
          {stopsLoading && <div className="text-[11px] text-gray-500">Loading stops…</div>}
          {stopsError && <div className="text-[11px] text-red-600">{stopsError}</div>}
          {!stopsLoading && !stopsError && stops && stops.length > 0 && (
            <ul className="max-h-32 overflow-auto space-y-1 pr-1">
              {stops.map((s, i) => (
                <li key={s.id || i} className="text-[11px] text-gray-700 flex items-center justify-between gap-2">
                  <span className="truncate">{s.stop_name}</span>
                  <span className="whitespace-nowrap text-gray-600">Arr {(s.arrival_time || "").slice(0,5)} · Dep {(s.departure_time || "").slice(0,5)}</span>
                </li>
              ))}
            </ul>
          )}
          {!stopsLoading && !stopsError && (!stops || stops.length === 0) && (
            <div className="text-[11px] text-gray-500">No stops found</div>
          )}
        </div>
      )}
    </button>
  );
}


