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
  const [rawTrips, setRawTrips] = useState<Trip[]>(SAMPLE_TRIPS);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [onlyValidNext, setOnlyValidNext] = useState<boolean>(true);
  const [hideUsed, setHideUsed] = useState<boolean>(true);
  const [textFilter, setTextFilter] = useState<string>("");

  // Free-URL fetch (optional)
  const [restUrl, setRestUrl] = useState<string>("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Backend integration
  const [baseUrl, setBaseUrl] = useState<string>(ENV_BASE_URL);
  const [routeId, setRouteId] = useState<string>(ENV_ROUTE_ID);
  const [day, setDay] = useState<string>(ENV_DAY);
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [token, setToken] = useState<string>(ENV_TOKEN);
  const [authInfo, setAuthInfo] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);

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
    const data = JSON.stringify(selectedTrips, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shift_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
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
    if (!baseUrl) {
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
      const res = await fetch(`${baseUrl}/auth/login`, {
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
    if (!baseUrl || !routeId) {
      alert("Base URL and Route ID required");
      return;
    }
    try {
      setLoading(true);
      const url = `${baseUrl}/api/v1/gtfs/gtfs-trips/by-route/${routeId}${day ? `?day_of_week=${encodeURIComponent(day)}` : ""}`;
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

      <main className="mx-auto max-w-7xl px-4 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Controls */}
        <section className="lg:col-span-1 space-y-4">
          <div className="p-4 rounded-2xl bg-white shadow-sm border">
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

          <div className="p-4 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">Backend</h2>
            <div className="space-y-2 text-sm">
              <input className="w-full px-3 py-2 border rounded-lg" placeholder="Base URL e.g., http://localhost:8002" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
              <div className="grid grid-cols-2 gap-2">
                <input className="px-3 py-2 border rounded-lg" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
                <input className="px-3 py-2 border rounded-lg" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <input className="flex-1 px-3 py-2 border rounded-lg" placeholder="Paste Bearer token (optional)" value={token} onChange={(e) => setToken(e.target.value)} />
                <button onClick={login} className="px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm hover:bg-indigo-700" disabled={loading}>Login</button>
              </div>
              {authInfo && <div className="text-xs text-gray-600">{authInfo}</div>}

              <div className="grid grid-cols-2 gap-2 mt-2">
                <input className="px-3 py-2 border rounded-lg" placeholder="Route ID" value={routeId} onChange={(e) => setRouteId(e.target.value)} />
                <select className="px-3 py-2 border rounded-lg" value={day} onChange={(e) => setDay(e.target.value)}>
                  {DAYS.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>
              <button onClick={loadByRouteDay} className="px-3 py-2 rounded-lg bg-emerald-600 text-white text-sm hover:bg-emerald-700 w-full" disabled={loading}>
                Load trips by route + day
              </button>
            </div>
          </div>

          <div className="p-4 rounded-2xl bg-white shadow-sm border">
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

          <div className="p-4 rounded-2xl bg-white shadow-sm border">
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
        <section className="lg:col-span-2 p-4 rounded-2xl bg-white shadow-sm border min-h-[60vh] flex flex-col">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-lg font-medium">Available trips</h2>
            <span className="text-sm text-gray-600">(sorted by departure time){loading ? " · loading…" : ""}</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 overflow-auto pr-1">
            {nextCandidates.map((t) => (
              <TripCard
                key={t.id}
                t={t}
                disabled={selectedIds.length > 0 && !computeValidNext(lastSelected, t)}
                used={used.has(t.id)}
                onPick={handlePickTrip(t)}
              />
            ))}
            {nextCandidates.length === 0 && (
              <div className="text-sm text-gray-600">No trips match the current filters. Try disabling "Only show valid next trips" or clearing the search.</div>
            )}
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
            <button onClick={handleExport} className="px-3 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700">Export selection</button>
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
}: {
  t: TripX;
  disabled?: boolean;
  used?: boolean;
  onPick: () => void;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onPick}
      className={
        "text-left p-3 rounded-xl border shadow-sm transition " +
        (disabled
          ? "bg-gray-100 text-gray-400 cursor-not-allowed"
          : used
          ? "bg-yellow-50 hover:bg-yellow-100"
          : "bg-white hover:bg-blue-50")
      }
      title={disabled ? "Doesn't follow from the last selected trip" : "Add to shift"}
    >
      <div className="font-medium truncate">{t.start_stop_name} → {t.end_stop_name}</div>
      <div className="text-sm">
        <span className="inline-block mr-4">Dep: {formatDayHHMM(t.departure_sec)}</span>
        <span>Arr: {formatDayHHMM(t.arrival_sec)}</span>
      </div>
      <div className="text-xs text-gray-600 truncate">Headsign: {t.trip_headsign}</div>
      <div className="text-xs text-gray-600 truncate">Route: {t.route_id} · Trip: {t.trip_short_name || t.trip_id}</div>
    </button>
  );
}


