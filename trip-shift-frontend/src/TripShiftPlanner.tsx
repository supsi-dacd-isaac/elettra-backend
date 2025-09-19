import { useMemo, useState, useEffect, useRef, useCallback } from "react";
import { MapContainer, TileLayer, Polyline, useMap, Marker } from "react-leaflet";
import * as L from "leaflet";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGUAGES, setAppLanguage, type SupportedLanguage } from "./i18n";

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

type CurrentUser = {
  id: string;
  company_id?: string;
  email: string;
  full_name: string;
  role: string;
  created_at?: string;
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
  stop_lat?: number | null;
  stop_lon?: number | null;
};

type ElevationRecord = {
  point_number: number;
  latitude: number;
  longitude: number;
  altitude_m: number;
  cumulative_distance_m: number;
};

type ElevationProfile = {
  shape_id: string;
  records: ElevationRecord[];
};

// Shifts types (backend responses)
type ShiftStructureItem = {
  id: string;
  trip_id: string;
  shift_id: string;
  sequence_number: number;
};
type ShiftRead = {
  id: string;
  name: string;
  bus_id?: string | null;
  structure: ShiftStructureItem[];
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
const ENV_LOGIN_EMAIL: string = VITE.VITE_TEST_LOGIN_EMAIL || VITE.TEST_LOGIN_EMAIL || "";
const ENV_LOGIN_PASSWORD: string = VITE.VITE_TEST_LOGIN_PASSWORD || VITE.TEST_LOGIN_PASSWORD || "";
const ENV_AUTO_LOGIN: boolean = VITE.VITE_AUTO_LOGIN === "true" || VITE.VITE_AUTO_LOGIN === true;

const DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"] as const;

// ---------- Main Component ----------
export default function TripShiftPlanner() {
  const { t, i18n } = useTranslation();
  const [language, setLanguage] = useState<SupportedLanguage>(() => {
    const current = i18n.language as SupportedLanguage;
    return SUPPORTED_LANGUAGES.includes(current) ? current : "en";
  });

  const [rawTrips, setRawTrips] = useState<Trip[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [onlyValidNext, setOnlyValidNext] = useState<boolean>(true);
  const [hideUsed, setHideUsed] = useState<boolean>(true);
  const [textFilter, setTextFilter] = useState<string>("");
  const [allTripsMap, setAllTripsMap] = useState<Map<string, TripX>>(new Map());

  // Removed outdated file upload and free-URL fetch

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
  const [loading, setLoading] = useState<boolean>(false);
  const [mode, setMode] = useState<"planner" | "createDepot">("planner");
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [currentAgencyName, setCurrentAgencyName] = useState<string>("");

  // Shift creation gating
  const [creatingShift, setCreatingShift] = useState<boolean>(false);
  const [shiftName, setShiftName] = useState<string>("");
  const [shiftBusId, setShiftBusId] = useState<string>("");
  const [showStartShiftDialog, setShowStartShiftDialog] = useState<boolean>(false);

  useEffect(() => {
    const handleLanguageChange = (lng: string) => {
      if (SUPPORTED_LANGUAGES.includes(lng as SupportedLanguage)) {
        setLanguage(lng as SupportedLanguage);
      }
    };
    i18n.on("languageChanged", handleLanguageChange);
    return () => {
      i18n.off("languageChanged", handleLanguageChange);
    };
  }, [i18n]);

  const handleLanguageSelect = useCallback((lng: SupportedLanguage) => {
    if (lng === language) return;
    setAppLanguage(lng);
  }, [language]);
  // Persist token across reloads
  const LS_TOKEN_KEY = "elettra_jwt";
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem(LS_TOKEN_KEY);
      if (saved && !token) {
        setToken(saved);
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      if (token) {
        window.localStorage.setItem(LS_TOKEN_KEY, token);
      } else {
        window.localStorage.removeItem(LS_TOKEN_KEY);
      }
    } catch {}
  }, [token]);

  // Load current user and agency name whenever token changes
  useEffect(() => {
    let cancelled = false;
    async function loadMeAndAgency() {
      if (!token || !effectiveBaseUrl) {
        setCurrentUser(null);
        setCurrentAgencyName("");
        return;
      }
      try {
        const meRes = await fetch(joinUrl(effectiveBaseUrl, "/auth/me"), {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!meRes.ok) throw new Error("me failed");
        const me: CurrentUser = await meRes.json();
        if (cancelled) return;
        setCurrentUser(me);
        if (me?.company_id) {
          setAgencyId((prev) => prev || me.company_id!);
          try {
            const agRes = await fetch(
              joinUrl(effectiveBaseUrl, `/api/v1/agency/agencies/${me.company_id}`),
              { headers: { Authorization: `Bearer ${token}` } }
            );
            if (cancelled) return;
            if (agRes.ok) {
              const agency: Agency = await agRes.json();
              if (!cancelled) setCurrentAgencyName(agency?.agency_name || "");
            } else {
              setCurrentAgencyName("");
            }
          } catch {
            if (!cancelled) setCurrentAgencyName("");
          }
        } else {
          setCurrentAgencyName("");
        }
      } catch {
        if (!cancelled) {
          setCurrentUser(null);
          setCurrentAgencyName("");
        }
      }
    }
    void loadMeAndAgency();
    return () => {
      cancelled = true;
    };
  }, [token, effectiveBaseUrl]);
  // Export progress state
  const [exporting, setExporting] = useState<boolean>(false);
  const [exportMessage, setExportMessage] = useState<string>("");
  // Depots management
  type Depot = { id: string; agency_id: string; name: string; address?: string | null; features?: any; stop_id?: string | null; latitude?: number | null; longitude?: number | null };
  const [depots, setDepots] = useState<Depot[]>([]);
  const [depotsLoading, setDepotsLoading] = useState<boolean>(false);
  const [depotsError, setDepotsError] = useState<string>("");
  const [depotsNotice, setDepotsNotice] = useState<string>("");
  const [editingDepotId, setEditingDepotId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Partial<Depot>>({});
  // Bus models management
  type BusModel = { id: string; agency_id: string; name: string; description?: string | null; specs?: any; manufacturer?: string | null };
  const [busModels, setBusModels] = useState<BusModel[]>([]);
  const [busModelsLoading, setBusModelsLoading] = useState<boolean>(false);
  const [busModelsError, setBusModelsError] = useState<string>("");
  const [editingBusModelId, setEditingBusModelId] = useState<string | null>(null);
  const [editingBusModel, setEditingBusModel] = useState<Partial<BusModel & { specsText?: string }>>({});
  const [showCreateBusModel, setShowCreateBusModel] = useState<boolean>(false);
  const [newBusModelName, setNewBusModelName] = useState<string>("");
  const [newBusModelManufacturer, setNewBusModelManufacturer] = useState<string>("");
  const [newBusModelDescription, setNewBusModelDescription] = useState<string>("");
  const [newBusModelSpecsText, setNewBusModelSpecsText] = useState<string>("");
  const [creatingBusModel, setCreatingBusModel] = useState<boolean>(false);
  // Buses management
  type Bus = { id: string; agency_id: string; name: string; specs?: any; bus_model_id?: string | null };
  const [buses, setBuses] = useState<Bus[]>([]);
  const [busesLoading, setBusesLoading] = useState<boolean>(false);
  const [busesError, setBusesError] = useState<string>("");
  const [editingBusId, setEditingBusId] = useState<string | null>(null);
  const [editingBus, setEditingBus] = useState<Partial<Bus & { specsText?: string }>>({});
  const [showCreateBus, setShowCreateBus] = useState<boolean>(false);
  const [newBusName, setNewBusName] = useState<string>("");
  const [newBusModelId, setNewBusModelId] = useState<string>("");
  const [newBusSpecsText, setNewBusSpecsText] = useState<string>("");
  const [creatingBus, setCreatingBus] = useState<boolean>(false);
  // Shifts management
  const [shifts, setShifts] = useState<ShiftRead[]>([]);
  const [shiftsLoading, setShiftsLoading] = useState<boolean>(false);
  const [shiftsError, setShiftsError] = useState<string>("");
  const [shiftEdges, setShiftEdges] = useState<Record<string, { fromStop: string; fromTime: string; toStop: string; toTime: string }>>({});
  const [shiftEdgesLoading, setShiftEdgesLoading] = useState<Record<string, boolean>>({});
  // Depot flow state
  const [hasLoadedForRouteDay, setHasLoadedForRouteDay] = useState<boolean>(false);
  const [leaveDepotInfo, setLeaveDepotInfo] = useState<null | { depotId: string; timeHHMM: string }>(null);
  const [returnDepotInfo, setReturnDepotInfo] = useState<null | { depotId: string; timeHHMM: string }>(null);
  const [showDepotDialog, setShowDepotDialog] = useState<null | "leave" | "return">(null);
  const [modalDepotId, setModalDepotId] = useState<string>("");
  const [modalTime, setModalTime] = useState<string>("");
  const [modalError, setModalError] = useState<string>("");
  // Simple prompt-based input for depot/time (avoid heavy modal UI for now)

  // Transfer flow state (collect immediately when picking non-connected trips)
  const [showTransferDialog, setShowTransferDialog] = useState<null | { prev: TripX; next: TripX }>(null);
  const [transferDepHHMM, setTransferDepHHMM] = useState<string>("");
  const [transferArrHHMM, setTransferArrHHMM] = useState<string>("");
  const [transferModalError, setTransferModalError] = useState<string>("");
  const [transfersByEdge, setTransfersByEdge] = useState<Record<string, { depHHMM: string; arrHHMM: string }>>({});

  // Agency/Route selection
  const [agencies, setAgencies] = useState<Agency[]>([]);
  const [agencyId, setAgencyId] = useState<string>(""); // database UUID for agency
  const [agencyQuery, setAgencyQuery] = useState<string>(""); // typeahead query / display label
  const [agencyOpen, setAgencyOpen] = useState<boolean>(false);
  const [agencyHighlight, setAgencyHighlight] = useState<number>(-1);
  const [routes, setRoutes] = useState<RouteRead[]>([]);
  const [routeDbId, setRouteDbId] = useState<string>(ENV_ROUTE_ID); // database UUID for route

  // Hovered trip and stops cache
  const [hoveredTripId, setHoveredTripId] = useState<string | null>(null); // database trip id (UUID)
  const [stopsByTrip, setStopsByTrip] = useState<Record<string, TripStop[]>>({}); // keyed by database trip id
  const [stopsLoadingTripId, setStopsLoadingTripId] = useState<string | null>(null); // database trip id
  const [stopsErrorByTrip, setStopsErrorByTrip] = useState<Record<string, string>>({});
  // Elevation cache per trip
  const [elevationByTrip, setElevationByTrip] = useState<Record<string, ElevationProfile>>({});
  const [elevationLoadingTripId, setElevationLoadingTripId] = useState<string | null>(null);
  const [elevationErrorByTrip, setElevationErrorByTrip] = useState<Record<string, string>>({});
  const hoverTimerRef = useRef<number | null>(null);

  // HH:MM -> seconds (accept >24h)
  const parseHHMMToSec = useCallback((t: string): number => {
    const parts = (t || "").split(":").map((x) => parseInt(x, 10));
    const h = parts[0] || 0;
    const m = parts[1] || 0;
    return h * 3600 + m * 60;
  }, []);

  // Persist toggle and settings per session
  useEffect(() => {
    try {
      const v = typeof window !== "undefined" ? window.sessionStorage.getItem("ts_onlyValidNext") : null;
      if (v !== null) setOnlyValidNext(v !== "0");
      const h = typeof window !== "undefined" ? window.sessionStorage.getItem("ts_hideUsed") : null;
      if (h !== null) setHideUsed(h !== "0");
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try { if (typeof window !== "undefined") window.sessionStorage.setItem("ts_onlyValidNext", onlyValidNext ? "1" : "0"); } catch {}
  }, [onlyValidNext]);
  useEffect(() => {
    try { if (typeof window !== "undefined") window.sessionStorage.setItem("ts_hideUsed", hideUsed ? "1" : "0"); } catch {}
  }, [hideUsed]);

  // Precompute enriched + sorted list
  const tripsX = useMemo(() => enrichTrips(rawTrips), [rawTrips]);
  const sortedTrips = useMemo(() => {
    return [...tripsX].sort((a, b) => {
      if (a.departure_sec !== b.departure_sec) return a.departure_sec - b.departure_sec;
      return a.id.localeCompare(b.id);
    });
  }, [tripsX]);

  // Merge newly loaded trips into a persistent map so selections survive route changes
  useEffect(() => {
    if (!tripsX || tripsX.length === 0) return;
    setAllTripsMap((prev) => {
      const next = new Map(prev);
      for (const t of tripsX) next.set(t.id, t);
      return next;
    });
  }, [tripsX]);

  const selectedTrips = useMemo(() => {
    return selectedIds
      .map((id) => allTripsMap.get(id))
      .filter((x): x is TripX => Boolean(x));
  }, [selectedIds, allTripsMap]);

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
    // Always enforce time filter
    const minDep = !lastSelected && leaveDepotInfo ? parseHHMMToSec(leaveDepotInfo.timeHHMM) : null;
    const timeFiltered = withText.filter((t) => {
      if (lastSelected) return t.departure_sec >= lastSelected.arrival_sec;
      if (minDep != null) return t.departure_sec >= minDep;
      return true;
    });
    if (!onlyValidNext) return timeFiltered;
    // additionally enforce same-stop continuity when toggle is on
    if (lastSelected) return timeFiltered.filter((t) => computeValidNext(lastSelected, t));
    return timeFiltered;
  }, [sortedTrips, used, hideUsed, textFilter, onlyValidNext, lastSelected, leaveDepotInfo, parseHHMMToSec]);

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
  function handlePickTrip(trip: TripX) {
    // Return a click handler (curried); this fixes prior syntax error
    return () => {
      if (returnDepotInfo) {
        alert(t("alerts.returnDepotSet"));
        return;
      }
      if (!leaveDepotInfo) {
        alert(t("alerts.leaveDepotFirst"));
        return;
      }
      if (selectedIds.length > 0) {
        if (onlyValidNext) {
          if (!computeValidNext(lastSelected, trip)) {
            alert(t("alerts.invalidSequence"));
            return;
          }
        } else {
          if (trip.departure_sec < (lastSelected?.arrival_sec ?? 0)) {
            alert(t("alerts.departBeforeLastArrival"));
            return;
          }
        }
      } else {
        // first selection must not be before leave depot time
        if (trip.departure_sec < parseHHMMToSec(leaveDepotInfo.timeHHMM)) {
          alert(t("alerts.departBeforeLeave"));
          return;
        }
      }
      // If non-connected selection (when relaxed mode), open transfer modal to collect times now
      const needTransfer = !onlyValidNext && !!lastSelected && normalizeStop(trip.start_stop_name) !== normalizeStop(lastSelected.end_stop_name);
      if (needTransfer) {
        setTransferModalError("");
        setTransferDepHHMM("");
        setTransferArrHHMM("");
        setShowTransferDialog({ prev: lastSelected!, next: trip });
        return;
      }
      setSelectedIds((prev) => (prev.includes(trip.id) ? prev : [...prev, trip.id]));
    };
  }

  function handleUndo() {
    // Priority: remove return depot -> remove last trip -> remove leave depot
    if (returnDepotInfo) {
      setReturnDepotInfo(null);
      return;
    }
    if (selectedIds.length > 0) {
      setSelectedIds((prev) => prev.slice(0, -1));
      return;
    }
    if (leaveDepotInfo) {
      setLeaveDepotInfo(null);
      return;
    }
  }

  function handleReset() {
    setSelectedIds([]);
    setLeaveDepotInfo(null);
    setReturnDepotInfo(null);
    setStopsByTrip({});
    setElevationByTrip({});
    setHoveredTripId(null);
    setExporting(false);
    setExportMessage("");
  }

  // Build final trips list: depot + transfers (if any) + core GTFS trips
  async function buildShiftTrips(): Promise<Trip[]> {
    if (!effectiveBaseUrl) {
      alert(t("common.baseUrlRequired"));
      return [];
    }
    if (!token) {
      alert(t("alerts.loginRequired"));
      return [];
    }
    if (!leaveDepotInfo || !returnDepotInfo) {
      alert(t("alerts.setDepotLegs"));
      return [];
    }
    if (!routeDbId && !routeId) {
      alert(t("alerts.selectRouteFirst"));
      return [];
    }
    if (selectedTrips.length === 0) {
      alert(t("alerts.selectTrips"));
      return [];
    }

    const parseHHMM = (hhmm: string) => {
      const [h, m] = (hhmm || "").split(":").map((x) => parseInt(x, 10));
      return (isFinite(h) ? h : 0) * 3600 + (isFinite(m) ? m : 0) * 60;
    };
    const fetchStops = async (tripDbId: string): Promise<TripStop[]> => {
      const url = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(tripDbId)}`);
      const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : undefined });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return (await res.json()) as TripStop[];
    };
    const getEdgeStops = async (tripDbId: string): Promise<{ first: TripStop; last: TripStop }> => {
      const stops = await fetchStops(tripDbId);
      if (!Array.isArray(stops) || stops.length === 0) throw new Error(t("export.errors.tripHasNoStops"));
      return { first: stops[0], last: stops[stops.length - 1] };
    };

    setExportMessage(t("export.fetchingStops"));
    const firstTrip = selectedTrips[0];
    const lastTrip = selectedTrips[selectedTrips.length - 1];
    const { first: firstStop } = await getEdgeStops(firstTrip.id);
    const { last: lastStop } = await getEdgeStops(lastTrip.id);
    const firstArr = (firstStop.arrival_time || firstStop.departure_time || "00:00:00").slice(0, 8);
    const lastDep = (lastStop.departure_time || lastStop.arrival_time || "00:00:00").slice(0, 8);

    const lastArrSec = lastTrip.arrival_sec;
    if (parseHHMM(returnDepotInfo.timeHHMM) <= lastArrSec) {
      alert(t("alerts.returnTimeTooEarly", { time: formatDayHHMM(lastArrSec) }));
      return [];
    }

    const postDepotTrip = async (body: any, stageLabel: string) => {
      setExportMessage(stageLabel);
      const res = await fetch(joinUrl(effectiveBaseUrl, "/api/v1/gtfs/aux-trip"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return (await res.json()) as Trip;
    };
    const routeUuid = routeDbId || routeId;

    // Insert transfer legs between non-connecting trips (use collected times if present)
    const withTransfers: Trip[] = [];
    const stopEdgeCache = new Map<string, { first: TripStop; last: TripStop }>();
    const ensureEdge = async (trip: TripX) => {
      const cached = stopEdgeCache.get(trip.id);
      if (cached) return cached;
      const edge = await getEdgeStops(trip.id);
      stopEdgeCache.set(trip.id, edge);
      return edge;
    };
    for (let i = 0; i < selectedTrips.length; i++) {
      const curr = selectedTrips[i];
      if (i === 0) {
        withTransfers.push(curr);
        continue;
      }
      const prev = selectedTrips[i - 1];
      const sameStop = normalizeStop(curr.start_stop_name) === normalizeStop(prev.end_stop_name);
      if (sameStop) {
        withTransfers.push(curr);
        continue;
      }
      // Need a transfer: read saved times collected during selection
      const key = `${prev.id}__${curr.id}`;
      const saved = transfersByEdge[key];
      if (!saved) {
        alert(t("alerts.missingTransferTimes"));
        setExportMessage(""); setExporting(false); return [];
      }
      const transferDep = saved.depHHMM;
      const transferArr = saved.arrHHMM;
      const prevEdge = await ensureEdge(prev);
      const currEdge = await ensureEdge(curr);
      const transferTrip = await postDepotTrip({
        departure_stop_id: prevEdge.last.id,
        arrival_stop_id: currEdge.first.id,
        departure_time: `${transferDep}:00`,
        arrival_time: `${transferArr}:00`,
        route_id: curr.route_id,
        status: "transfer",
      }, t("export.creatingTransfer"));
      withTransfers.push(transferTrip);
      withTransfers.push(curr);
    }
    const depDepot = depots.find((d) => d.id === leaveDepotInfo.depotId);
    if (!depDepot || !depDepot.stop_id) throw new Error(t("export.errors.departDepotMissing"));
    const depTrip = await postDepotTrip({
      departure_stop_id: depDepot.stop_id,
      arrival_stop_id: firstStop.id,
      departure_time: `${leaveDepotInfo.timeHHMM}:00`,
      arrival_time: firstArr.length === 5 ? `${firstArr}:00` : firstArr,
      route_id: routeUuid,
      status: "depot",
    }, t("export.creatingDeparture"));
    const retDepot = depots.find((d) => d.id === returnDepotInfo.depotId);
    if (!retDepot || !retDepot.stop_id) throw new Error(t("export.errors.returnDepotMissing"));
    const retTrip = await postDepotTrip({
      departure_stop_id: lastStop.id,
      arrival_stop_id: retDepot.stop_id,
      departure_time: lastDep.length === 5 ? `${lastDep}:00` : lastDep,
      arrival_time: `${returnDepotInfo.timeHHMM}:00`,
      route_id: routeUuid,
      status: "depot",
    }, t("export.creatingReturn"));

    const combined: Trip[] = [depTrip, ...withTransfers, retTrip];
    return combined;
  }

  // Fetch and cache shift edges (first and last trip stop/time)
  async function fetchShiftEdges(shift: ShiftRead) {
    try {
      if (!shift || !shift.structure || shift.structure.length === 0) return;
      const firstTripId = shift.structure[0].trip_id;
      const lastTripId = shift.structure[shift.structure.length - 1].trip_id;
      setShiftEdgesLoading((prev) => ({ ...prev, [shift.id]: true }));
      const urlFirst = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(firstTripId)}`);
      const urlLast = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/gtfs-stops/by-trip/${encodeURIComponent(lastTripId)}`);
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

  async function handleSaveShift() {
    try {
      setExporting(true);
      setExportMessage(t("export.preparing"));
      const combined = await buildShiftTrips();
      if (!combined || combined.length === 0) return;

      // Save shift to backend (requires name and bus)
      if (!shiftName.trim() || !shiftBusId) {
        alert(t("selected.exportHintIncomplete"));
        return;
      }
      setExportMessage(t("selected.exporting"));
      const res = await fetch(joinUrl(effectiveBaseUrl, "/api/v1/agency/shifts/"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: shiftName.trim(), bus_id: shiftBusId, trip_ids: combined.map((tr) => tr.id) }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

      // Refresh shifts list
      void loadShiftsForAgency();

      // Keep exporting JSON as before
      setExportMessage(t("export.preparingDownload"));
      const data = JSON.stringify(combined, null, 2);
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

      // Reset creation flow after save
      setCreatingShift(false);
      setShiftName("");
      setShiftBusId("");
      handleReset();
    } catch (e: any) {
      alert(t("export.failed", { error: e?.message || e }));
    } finally {
      setExporting(false);
      setExportMessage("");
    }
  }

  async function performLogin(emailToUse: string, passwordToUse: string) {
    if (!effectiveBaseUrl) {
      alert(t("common.baseUrlRequired"));
      return;
    }
    if (!emailToUse || !passwordToUse) {
      alert(t("auth.provideCredentials"));
      return;
    }
    try {
      setLoading(true);
      const res = await fetch(joinUrl(effectiveBaseUrl, "/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: emailToUse, password: passwordToUse }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data: any = await res.json();
      const tok = data?.access_token;
      if (!tok) throw new Error(t("auth.errors.noToken"));
      setToken(tok);
      // After successful login, fetch current user
      try {
        const meRes = await fetch(joinUrl(effectiveBaseUrl, "/auth/me"), { headers: { Authorization: `Bearer ${tok}` } });
        if (meRes.ok) {
          const me = await meRes.json();
          // If user has a company_id and no agency selected yet, preselect it
          if (me?.company_id && !agencyId) {
            setAgencyId(me.company_id);
          }
        }
      } catch {}
    } catch (e: any) {
      alert(t("auth.loginFailed", { error: e?.message || e }));
    } finally {
      setLoading(false);
    }
  }

  async function login() {
    if (!effectiveBaseUrl) {
      alert(t("common.baseUrlRequired"));
      return;
    }
    await performLogin(email, password);
  }

  function logout() {
    setToken("");
    setEmail("");
    setPassword("");
    try { if (typeof window !== "undefined") window.localStorage.removeItem(LS_TOKEN_KEY); } catch {}
    setAgencyId("");
    setAgencyQuery("");
    setAgencies([]);
    setRoutes([]);
    setCurrentUser(null);
    setCurrentAgencyName("");
  }

  async function loadByRouteDay() {
    const selectedRouteId = routeDbId || routeId; // support legacy env for now
    if (!effectiveBaseUrl || !selectedRouteId || !day || !token) {
      return; // Don't show alerts for automatic loading
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
    } catch (e: any) {
      console.error(`Failed to load trips: ${e?.message || e}`);
      // Don't show alerts for automatic loading, just log the error
    } finally {
      setLoading(false);
      setHasLoadedForRouteDay(true);
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

  const loadDepotsForAgency = useCallback(async () => {
    if (!effectiveBaseUrl || !token || !agencyId) return;
    try {
      setDepotsError("");
      setDepotsLoading(true);
      const url = joinUrl(effectiveBaseUrl, "/api/v1/agency/depots/?skip=0&limit=1000");
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as Depot[];
      setDepots(Array.isArray(all) ? all.filter((d) => d.agency_id === agencyId) : []);
    } catch (e: any) {
      setDepots([]);
      setDepotsError(e?.message || String(e));
    } finally {
      setDepotsLoading(false);
    }
  }, [effectiveBaseUrl, token, agencyId]);

  // Load bus models (global, not per-agency)
  const loadBusModels = useCallback(async () => {
    if (!effectiveBaseUrl || !token || !agencyId) return;
    try {
      setBusModelsError("");
      setBusModelsLoading(true);
      const url = joinUrl(effectiveBaseUrl, "/api/v1/agency/bus-models/?skip=0&limit=1000");
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as BusModel[];
      setBusModels(Array.isArray(all) ? all.filter((m) => m.agency_id === agencyId) : []);
    } catch (e: any) {
      setBusModels([]);
      setBusModelsError(e?.message || String(e));
    } finally {
      setBusModelsLoading(false);
    }
  }, [effectiveBaseUrl, token, agencyId]);

  // Load buses for selected agency
  const loadBusesForAgency = useCallback(async () => {
    if (!effectiveBaseUrl || !token || !agencyId) return;
    try {
      setBusesError("");
      setBusesLoading(true);
      const url = joinUrl(effectiveBaseUrl, "/api/v1/agency/buses/?skip=0&limit=1000");
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as Bus[];
      setBuses(Array.isArray(all) ? all.filter((b) => b.agency_id === agencyId) : []);
    } catch (e: any) {
      setBuses([]);
      setBusesError(e?.message || String(e));
    } finally {
      setBusesLoading(false);
    }
  }, [effectiveBaseUrl, token, agencyId]);

  // Load shifts for selected agency
  const loadShiftsForAgency = useCallback(async () => {
    if (!effectiveBaseUrl || !token || !agencyId) return;
    try {
      setShiftsError("");
      setShiftsLoading(true);
      const url = joinUrl(effectiveBaseUrl, `/api/v1/agency/shifts/?skip=0&limit=1000&agency_id=${encodeURIComponent(agencyId)}`);
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const all = (await res.json()) as ShiftRead[];
      setShifts(Array.isArray(all) ? all : []);
      // Preload edges for first page few items
      const toPreload = (Array.isArray(all) ? all.slice(0, 10) : []);
      for (const s of toPreload) void fetchShiftEdges(s);
    } catch (e: any) {
      setShifts([]);
      setShiftsError(e?.message || String(e));
    } finally {
      setShiftsLoading(false);
    }
  }, [effectiveBaseUrl, token, agencyId]);

  // When token becomes available, load agencies
  useEffect(() => {
    if (token) {
      fetchAgencies();
    }
  }, [token, effectiveBaseUrl]);

  // When token is set (pasted or from env), fetch current user and preselect agency
  useEffect(() => {
    (async () => {
      if (!effectiveBaseUrl || !token) return;
      try {
        const meRes = await fetch(joinUrl(effectiveBaseUrl, "/auth/me"), { headers: { Authorization: `Bearer ${token}` } });
        if (meRes.ok) {
          const me = await meRes.json();
          if (me?.company_id && !agencyId) {
            setAgencyId(me.company_id);
          }
        }
      } catch {}
    })();
  }, [token, effectiveBaseUrl]);

  // Attempt auto-login on first load using env credentials (if enabled)
  useEffect(() => {
    if (!token && ENV_AUTO_LOGIN && ENV_LOGIN_EMAIL && ENV_LOGIN_PASSWORD && effectiveBaseUrl) {
      setEmail(ENV_LOGIN_EMAIL);
      setPassword(ENV_LOGIN_PASSWORD);
      performLogin(ENV_LOGIN_EMAIL, ENV_LOGIN_PASSWORD);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveBaseUrl]);

  // When agency changes, load routes and clear selection
  useEffect(() => {
    if (agencyId) {
      setRouteDbId("");
      fetchRoutesByAgency(agencyId);
      // Load depots for selected agency
      void loadDepotsForAgency();
      // Load buses for selected agency
      void loadBusesForAgency();
      // Load shifts for selected agency
      void loadShiftsForAgency();
    } else {
      setRoutes([]);
      setDepots([]);
      setBuses([]);
      setShifts([]);
      setEditingDepotId(null);
    }
    // Reset depot flow completely on agency change
    setHasLoadedForRouteDay(false);
    setLeaveDepotInfo(null);
    setReturnDepotInfo(null);
    setSelectedIds([]);
    setStopsByTrip({});
    setElevationByTrip({});
  }, [agencyId, token, effectiveBaseUrl, loadDepotsForAgency, loadBusesForAgency, loadShiftsForAgency]);

  // When agency/token available, load bus models for agency
  useEffect(() => {
    if (token && agencyId) void loadBusModels();
  }, [token, agencyId, effectiveBaseUrl, loadBusModels]);

  // Reset depot flow on route change
  useEffect(() => {
    setHasLoadedForRouteDay(false);
    // Keep selected trips and depot info across route changes
    setStopsByTrip({});
    setElevationByTrip({});
  }, [routeDbId]);

  // Auto-load trips when both route and day are selected
  useEffect(() => {
    const selectedRouteId = routeDbId || routeId;
    if (selectedRouteId && day && token && effectiveBaseUrl) {
      loadByRouteDay();
    }
  }, [routeDbId, routeId, day, token, effectiveBaseUrl]);

  // Filter and sort agencies by name/id
  const filteredAgencies = useMemo(() => {
    const label = (a: Agency) => (a.agency_name || a.gtfs_agency_id || "").toString();
    const q = agencyQuery.trim().toLowerCase();
    const list = q ? agencies.filter((a) => label(a).toLowerCase().includes(q)) : agencies.slice();
    return list.sort((a, b) => label(a).localeCompare(label(b), undefined, { sensitivity: "base" }));
  }, [agencies, agencyQuery]);

  function agencyLabel(a?: Agency) {
    return (a?.agency_name || a?.gtfs_agency_id || "").toString();
  }

  // Keep visible query in sync with selected agencyId
  useEffect(() => {
    if (!agencyId) return; // do not overwrite user typing when nothing selected
    const sel = agencies.find((x) => x.id === agencyId);
    if (sel) setAgencyQuery(agencyLabel(sel));
  }, [agencyId, agencies]);

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

  async function ensureElevationForTrip(tripDbId: string) {
    if (!tripDbId) return;
    if (elevationByTrip[tripDbId]) return; // cached
    if (!effectiveBaseUrl) return;
    try {
      setElevationLoadingTripId(tripDbId);
      const url = joinUrl(effectiveBaseUrl, `/api/v1/gtfs/elevation-profile/by-trip/${encodeURIComponent(tripDbId)}`);
      const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : undefined });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = (await res.json()) as ElevationProfile;
      if (!data || !Array.isArray(data.records)) throw new Error("Invalid elevation response");
      setElevationByTrip((prev) => ({ ...prev, [tripDbId]: data }));
    } catch (e: any) {
      setElevationErrorByTrip((prev) => ({ ...prev, [tripDbId]: e?.message || String(e) }));
    } finally {
      setElevationLoadingTripId((prev) => (prev === tripDbId ? null : prev));
    }
  }

  // ---------- Self-tests (lightweight runtime checks) ----------
  type TestResult = { name: string; pass: boolean; msg?: string };
  const [tests, setTests] = useState<TestResult[]>([]);
  useEffect(() => {
    const results: TestResult[] = [];
    // 1) GTFS time parsing
    const parsed = parseGtfsTimeToSeconds("25:13:00");
    results.push({ name: t("selfTests.parse"), pass: parsed === (25 * 3600 + 13 * 60), msg: t("selfTests.parseMsg", { value: parsed }) });
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
    results.push({ name: t("selfTests.validNext"), pass: computeValidNext(a, b) === true });
    const c: TripX = { ...b, start_stop_name: "X" };
    results.push({ name: t("selfTests.invalidNext"), pass: computeValidNext(a, c) === false });
    setTests(results);
  }, [t]);

  // ---------- UI ----------
  return (
    <div className="min-h-screen w-full bg-gray-50 text-gray-900">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
        <div className="mx-auto max-w-7xl px-4 py-3 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div className="flex items-center gap-3">
              <img src="/elettra_icon.svg" alt="Elettra" className="w-12 h-12" />
            <div>
              <h1 className="text-2xl font-semibold">{t("header.title")}</h1>
              <p className="text-sm text-gray-600">{t("header.subtitle")}</p>
            </div>
          </div>

          <div className="flex flex-col gap-2 md:flex-row md:items-center">
            <label className="flex items-center gap-2 text-sm text-gray-600">
              <span className="font-medium text-gray-700">{t("header.languageLabel")}</span>
              <select
                className="px-3 py-2 border rounded-lg bg-white text-gray-900"
                value={language}
                onChange={(e) => handleLanguageSelect(e.target.value as SupportedLanguage)}
              >
                {SUPPORTED_LANGUAGES.map((lng) => (
                  <option key={lng} value={lng}>
                    {t(`languageNames.${lng}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: Controls */}
        <section className="lg:col-span-1 space-y-4">
          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("filters.title")}</h2>
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!onlyValidNext}
                    onChange={(e) => setOnlyValidNext(!e.target.checked)}
                  />
                  {t("filters.showDisconnected")}
                </label>
              </div>
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={hideUsed} onChange={(e) => setHideUsed(e.target.checked)} /> {t("filters.hideUsed")}
                </label>
              </div>
              {!onlyValidNext && (
                <div className="text-xs px-2 py-1 rounded" style={{color: '#3B3C48', backgroundColor: '#f8f9fa', borderColor: '#dee2e6', border: '1px solid'}}>
                  {t("filters.disconnectedHint")}
                </div>
              )}
              <div>
                <input
                  type="text"
                  placeholder={t("filters.searchPlaceholder")}
                  value={textFilter}
                  onChange={(e) => setTextFilter(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg"
                />
              </div>
            </div>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("auth.title")}</h2>
            <div className="space-y-2 text-sm">
              {!token ? (
                <div className="grid grid-cols-2 gap-2">
                  <input className="px-3 py-2 border rounded-lg" placeholder={t("auth.emailPlaceholder")} value={email} onChange={(e) => setEmail(e.target.value)} />
                  <input className="px-3 py-2 border rounded-lg" type="password" placeholder={t("auth.passwordPlaceholder")} value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
              ) : (
                <div className="p-2 rounded-lg border bg-gray-50">
                  <div className="text-sm font-medium mb-1">{t("auth.userInfoTitle")}</div>
                  <div className="text-xs text-gray-700">
                    <div><span className="font-semibold">{t("auth.userName")}:</span> {currentUser?.full_name || "—"}</div>
                    <div><span className="font-semibold">{t("auth.userEmail")}:</span> {currentUser?.email || "—"}</div>
                    <div><span className="font-semibold">{t("auth.agencyName")}:</span> {currentAgencyName || "—"}</div>
                    <div><span className="font-semibold">{t("auth.userRole")}:</span> {currentUser?.role || "—"}</div>
                  </div>
                </div>
              )}
              <div className="flex gap-2">
                {token ? (
                  <button onClick={logout} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} disabled={loading}>{t("auth.logout")}</button>
                ) : (
                  <button onClick={login} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} disabled={loading}>{t(loading ? "auth.loggingIn" : "auth.login")}</button>
                )}
              </div>
            </div>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("shift.title")}</h2>
            {depotsNotice && (
              <div className="mb-2 text-xs px-3 py-2 rounded" style={{backgroundColor: '#f0f9ff', color: '#74C244', borderColor: '#74C244', border: '1px solid'}}>
                {depotsNotice}
              </div>
            )}
            <div className="space-y-2 text-sm">
              {/* Create Shift flow */}
              <div className="p-2 rounded-lg border">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-medium">{t("shifts.createTitle", 'Shift creation')}</div>
                  {!creatingShift ? (
                    <button
                      className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                      style={{backgroundColor: '#002AA7'}}
                      disabled={!token || !agencyId}
                      onClick={() => {
                        setShowStartShiftDialog(true);
                      }}
                      title={!token ? t("depots.authRequired") : (!agencyId ? t("depots.selectAgencyBackend") : t("shifts.startHint", 'Start a new shift'))}
                    >
                      {t("shifts.createButton", 'Create shift')}
                    </button>
                  ) : (
                    <div className="text-xs text-gray-700">
                      {t("shifts.current", 'Creating')}: <span className="font-medium">{shiftName || '—'}</span>
                      <div className="mt-1 flex gap-2">
                        <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200" onClick={() => {
                          setCreatingShift(false);
                          setShiftName("");
                          setShiftBusId("");
                          handleReset();
                        }}>{t("common.cancel")}</button>
                      </div>
                    </div>
                  )}
                </div>
                {!creatingShift && (
                  <div className="text-xs text-gray-600">{t("shifts.createHint", 'Click "Create shift" to begin. You will enter the name and select a bus. Then select day/route and construct the shift.')}</div>
                )}
                {creatingShift && (
                  <div className="text-xs text-gray-600">{t("shifts.enabledHint", 'Day and route are now enabled. Proceed with depot, trips, return then Save shift.')}</div>
                )}
              </div>
              {/* Depot flow */}
              <div className="p-2 rounded-lg border">
                <div className="text-sm font-medium mb-2">{t("shift.depotFlow")}</div>
                <div className="flex items-center gap-2">
                  <button
                    className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                    style={{backgroundColor: '#002AA7'}}
                    disabled={!token || !agencyId || !(routeDbId || routeId) || !hasLoadedForRouteDay || !creatingShift}
                    onClick={() => {
                      setModalError("");
                      setModalDepotId("");
                      setModalTime("");
                      setShowDepotDialog("leave");
                    }}
                    title={!hasLoadedForRouteDay ? t("shift.leaveDisabledHint") : t("shift.pickDepotAndTime")}
                  >
                    {leaveDepotInfo ? t("shift.leaveDepotSet") : t("shift.leaveDepot")}
                  </button>
                  <button
                    className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                    style={{ backgroundColor: returnDepotInfo ? "#6b7280" : "#74C244" }}
                    disabled={!token || !agencyId || !(routeDbId || routeId) || !leaveDepotInfo || selectedIds.length === 0 || !creatingShift}
                    onClick={() => {
                      setModalError("");
                      setModalDepotId("");
                      setModalTime("");
                      setShowDepotDialog("return");
                    }}
                    title={!leaveDepotInfo
                      ? t("shift.returnDisabledNoLeave")
                      : selectedIds.length === 0
                      ? t("shift.returnDisabledNoTrips")
                      : t("shift.pickDepotAndTime")}
                  >
                    {returnDepotInfo ? t("shift.returnDepotSet") : t("shift.returnDepot")}
                  </button>
                </div>
                {leaveDepotInfo && (
                  <div className="mt-2 text-xs text-gray-700">{t("shift.leaveSummary", { time: leaveDepotInfo.timeHHMM })}</div>
                )}
                {returnDepotInfo && (
                  <div className="mt-1 text-xs text-gray-700">{t("shift.returnSummary", { time: returnDepotInfo.timeHHMM })}</div>
                )}
              </div>

              <div className="grid grid-cols-2 gap-2 mt-2">
                <div className="relative">
                  <input
                    className="w-full px-3 py-2 border rounded-lg"
                    placeholder={token ? t("shift.selectAgencyPlaceholder") : t("shift.loginFirstPlaceholder")}
                    value={agencyQuery}
                    disabled={!token}
                    onFocus={() => token && setAgencyOpen(true)}
                    onChange={(e) => {
                      setAgencyQuery(e.target.value);
                      setAgencyOpen(true);
                      setAgencyHighlight(-1);
                      if (agencyId) setAgencyId(""); // clear selection when typing
                    }}
                    onKeyDown={(e) => {
                      if (!agencyOpen && (e.key === "ArrowDown" || e.key === "Enter")) {
                        setAgencyOpen(true);
                        return;
                      }
                      if (!agencyOpen) return;
                      if (e.key === "ArrowDown") {
                        e.preventDefault();
                        setAgencyHighlight((h) => Math.min((filteredAgencies.length - 1), h + 1));
                      } else if (e.key === "ArrowUp") {
                        e.preventDefault();
                        setAgencyHighlight((h) => Math.max(-1, h - 1));
                      } else if (e.key === "Enter") {
                        e.preventDefault();
                        const pick = agencyHighlight >= 0 ? filteredAgencies[agencyHighlight] : filteredAgencies[0];
                        if (pick) {
                          setAgencyId(pick.id);
                          setAgencyQuery(agencyLabel(pick));
                          setAgencyOpen(false);
                          setAgencyHighlight(-1);
                        }
                      } else if (e.key === "Escape") {
                        setAgencyOpen(false);
                        setAgencyHighlight(-1);
                      }
                    }}
                    onBlur={() => {
                      // Close after click selection
                      setTimeout(() => setAgencyOpen(false), 100);
                    }}
                  />
                  {agencyOpen && token && filteredAgencies.length > 0 && (
                    <ul className="absolute z-10 mt-1 w-full max-h-48 overflow-auto border rounded-lg bg-white shadow">
                      {filteredAgencies.map((a, idx) => (
                        <li
                          key={a.id}
                          className={
                            "px-3 py-2 cursor-pointer text-sm " +
                            (idx === agencyHighlight ? "text-white" : "hover:bg-gray-100")
                          }
                          style={idx === agencyHighlight ? {backgroundColor: '#002AA7'} : {}}
                          onMouseEnter={() => setAgencyHighlight(idx)}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            setAgencyId(a.id);
                            setAgencyQuery(agencyLabel(a));
                            setAgencyOpen(false);
                            setAgencyHighlight(-1);
                          }}
                        >
                          {agencyLabel(a)}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <select
                  className="px-3 py-2 border rounded-lg"
                  value={routeDbId}
                  onChange={(e) => setRouteDbId(e.target.value)}
                  disabled={!agencyId || routes.length === 0 || !creatingShift}
                >
                  <option value="">{agencyId
                    ? (routes.length ? t("shift.selectRoutePlaceholder") : t("shift.loadingRoutes"))
                    : t("shift.selectAgencyFirst")}</option>
                  {routes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {(r.route_short_name || r.route_long_name || r.route_id) as string}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <select className="px-3 py-2 border rounded-lg" value={day} onChange={(e) => setDay(e.target.value)} disabled={!creatingShift}>
                  {DAYS.map((d) => (
                    <option key={d} value={d}>{t(`days.${d}`)}</option>
                  ))}
                </select>
              </div>
              {/* Shifts list inside Shift Planning panel */}
              <div className="mt-3 p-2 rounded-lg border">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-medium">{t("shifts.listTitle", 'Saved shifts')}</div>
                  <div className="flex items-center gap-2">
                    {shiftsLoading && <span className="text-xs text-gray-500">{t("common.loading")}</span>}
                    <button
                      className="px-2 py-1 rounded text-white text-xs hover:opacity-90 disabled:opacity-50"
                      style={{backgroundColor: '#6b7280'}}
                      disabled={!token || !agencyId}
                      onClick={() => void loadShiftsForAgency()}
                      title={!token ? t("depots.authRequired") : (!agencyId ? t("depots.selectAgencyBackend") : '')}
                    >
                      {t("common.refresh", 'Refresh')}
                    </button>
                  </div>
                </div>
                {shiftsError && <div className="text-sm text-red-600">{shiftsError}</div>}
                {(!shiftsLoading && shifts.length === 0) ? (
                  <div className="text-sm text-gray-600">{t("shifts.empty", 'No shifts')}</div>
                ) : (
                  <ul className="space-y-2">
                    {shifts.map((s) => (
                      <li key={s.id} className="border rounded-lg p-2">
                        <div className="flex items-start justify-between gap-2">
                          <div className="text-sm">
                            <div className="font-medium">{s.name}</div>
                            <div className="text-gray-600">{t("shifts.tripCount", { count: s.structure?.length || 0 })}</div>
                            <div className="text-gray-600">
                              {(() => {
                                const edge = shiftEdges[s.id];
                                const loading = shiftEdgesLoading[s.id];
                                if (loading) return <span className="text-xs text-gray-500">{t("common.loading")}</span>;
                                if (!edge) { void fetchShiftEdges(s); return <span className="text-xs text-gray-500">{t("common.loading")}</span>; }
                                return <>{t("shifts.summary", edge as any)}</>;
                              })()}
                            </div>
                          </div>
                          <div className="flex gap-2">
                            <button className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700" onClick={async () => {
                              if (!effectiveBaseUrl || !token) return;
                              if (!window.confirm(t("shifts.confirmDelete", { name: s.name }) as any)) return;
                              try {
                                const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/shifts/${encodeURIComponent(s.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                                if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                                setShifts((prev) => prev.filter((x) => x.id !== s.id));
                              } catch (e: any) { alert(t("shifts.deleteFailed", { error: e?.message || String(e) })); }
                            }}>{t("common.delete")}</button>
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("depots.title")}</h2>
            <button
              className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 w-full disabled:opacity-50"
              style={{backgroundColor: '#002AA7'}}
              disabled={!token || !agencyId}
              onClick={() => setMode("createDepot")}
              title={!token ? t("depots.loginFirst") : !agencyId ? t("depots.selectAgencyFirst") : t("depots.createNewHint")}
            >
              {t("depots.createButton")}
            </button>
            {!token || !agencyId ? (
              <div className="mt-2 text-xs text-gray-600">
                {!token ? t("depots.authRequired") : !agencyId ? t("depots.selectAgencyBackend") : null}
              </div>
            ) : null}
            {/* Depots list */}
            {token && agencyId && (
              <div className="mt-3 space-y-2">
                <div className="text-sm text-gray-700 flex items-center justify-between">
                  <span>{t("depots.listTitle")}</span>
                  {depotsLoading && <span className="text-xs text-gray-500">{t("common.loading")}</span>}
                </div>
                {depotsError && <div className="text-sm text-red-600">{depotsError}</div>}
                {(!depotsLoading && depots.length === 0) ? (
                  <div className="text-sm text-gray-600">{t("depots.empty")}</div>
                ) : (
                  <ul className="space-y-2">
                    {depots.map((d) => (
                      <li key={d.id} className="border rounded-lg p-2">
                        {editingDepotId === d.id ? (
                          <div className="space-y-2">
                            <input className="w-full px-2 py-1 border rounded" placeholder={t("depots.form.namePlaceholder")} value={(editing.name as string) ?? d.name} onChange={(e) => setEditing((prev) => ({ ...prev, name: e.target.value }))} />
                            <div>
                              <input className="w-full px-2 py-1 border rounded" placeholder={t("depots.form.addressPlaceholder")} value={(editing.address as string) ?? (d.address || "")} onChange={(e) => setEditing((prev) => ({ ...prev, address: e.target.value }))} />
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              <input className="px-2 py-1 border rounded" placeholder={t("depots.form.latitudePlaceholder")} value={(editing.latitude as any) ?? (typeof d.latitude === "number" ? d.latitude : "")} onChange={(e) => setEditing((prev) => ({ ...prev, latitude: e.target.value ? parseFloat(e.target.value) : null }))} />
                              <input className="px-2 py-1 border rounded" placeholder={t("depots.form.longitudePlaceholder")} value={(editing.longitude as any) ?? (typeof d.longitude === "number" ? d.longitude : "")} onChange={(e) => setEditing((prev) => ({ ...prev, longitude: e.target.value ? parseFloat(e.target.value) : null }))} />
                            </div>
                            <div className="flex gap-2">
                              <button
                                className="px-2 py-1 rounded text-white text-sm hover:opacity-90"
                                style={{backgroundColor: '#74C244'}}
                                onClick={async () => {
                                  if (!effectiveBaseUrl || !token) return;
                                  try {
                                    const payload: any = {};
                                    if (editing.name !== undefined) payload.name = editing.name;
                                    if (editing.address !== undefined) payload.address = editing.address;
                                    if (editing.latitude !== undefined) payload.latitude = editing.latitude;
                                    if (editing.longitude !== undefined) payload.longitude = editing.longitude;
                                    const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/depots/${encodeURIComponent(d.id)}`), {
                                      method: "PUT",
                                      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
                                      body: JSON.stringify(payload),
                                    });
                                    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                                    const updated = (await res.json()) as Depot;
                                    setDepots((prev) => prev.map((x) => (x.id === d.id ? updated : x)));
                                    setEditingDepotId(null);
                                    setEditing({});
                                  } catch (e: any) {
                                    alert(t("depots.saveFailed", { error: e?.message || e }));
                                  }
                                }}
                              >
                                {t("common.save")}
                              </button>
                              <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setEditingDepotId(null); setEditing({}); }}>{t("common.cancel")}</button>
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-start justify-between gap-2">
                            <div className="text-sm">
                              <div className="font-medium">{d.name}</div>
                              <div className="text-gray-600">{[d.address].filter(Boolean).join(", ")}</div>
                              <div className="text-xs text-gray-500">{typeof d.latitude === "number" && typeof d.longitude === "number" ? `${d.latitude.toFixed(6)}, ${d.longitude.toFixed(6)}` : ""}</div>
                            </div>
                            <div className="flex gap-2">
                              <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} onClick={() => { setEditingDepotId(d.id); setEditing({}); }}>{t("common.edit")}</button>
                              <button
                                className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700"
                                onClick={async () => {
                                  if (!effectiveBaseUrl || !token) return;
                                  if (!window.confirm(t("depots.confirmDelete", { name: d.name }))) return;
                                  try {
                                    const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/depots/${encodeURIComponent(d.id)}`), {
                                      method: "DELETE",
                                      headers: { Authorization: `Bearer ${token}` },
                                    });
                                    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                                    setDepots((prev) => prev.filter((x) => x.id !== d.id));
                                  } catch (e: any) {
                                    alert(t("depots.deleteFailed", { error: e?.message || e }));
                                  }
                                }}
                              >
                                {t("common.delete")}
                              </button>
                            </div>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          {/* Bus models frame */}
          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("busModels.title", 'Bus models')}</h2>
            <div className="flex gap-2 mb-2">
              <button
                className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#002AA7'}}
                disabled={!token}
                onClick={() => setShowCreateBusModel((v) => !v)}
                title={!token ? t("depots.authRequired") : ''}
              >
                {t("busModels.createButton", showCreateBusModel ? 'Close' : 'Create model')}
              </button>
              <button
                className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#6b7280'}}
                disabled={!token}
                onClick={() => void loadBusModels()}
              >
                {t("common.refresh", 'Refresh')}
              </button>
            </div>
            {showCreateBusModel && (
              <div className="mb-3 border rounded-lg p-2 space-y-2">
                <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.name", 'Name')} value={newBusModelName} onChange={(e) => setNewBusModelName(e.target.value)} />
                <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.description", 'Description')} value={newBusModelDescription} onChange={(e) => setNewBusModelDescription(e.target.value)} />
                <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.manufacturer", 'Manufacturer')} value={newBusModelManufacturer} onChange={(e) => setNewBusModelManufacturer(e.target.value)} />
                <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t("busModels.form.specs", 'Specs (JSON)')} value={newBusModelSpecsText} onChange={(e) => setNewBusModelSpecsText(e.target.value)} />
                <div className="flex gap-2">
                  <button
                    className="px-2 py-1 rounded text-white text-sm hover:opacity-90"
                    style={{backgroundColor: '#74C244'}}
                    disabled={!token || creatingBusModel || !newBusModelName.trim()}
                    onClick={async () => {
                      if (!effectiveBaseUrl || !token) return;
                      try {
                        setCreatingBusModel(true);
                        let specs: any = {};
                        if (newBusModelSpecsText.trim()) {
                          try { specs = JSON.parse(newBusModelSpecsText); } catch (e) { alert(t("busModels.parseError", 'Invalid JSON in specs')); setCreatingBusModel(false); return; }
                        }
                        const res = await fetch(joinUrl(effectiveBaseUrl, "/api/v1/agency/bus-models/"), {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                          body: JSON.stringify({ agency_id: agencyId, name: newBusModelName, description: newBusModelDescription || null, manufacturer: newBusModelManufacturer || null, specs })
                        });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        const created = (await res.json()) as BusModel;
                        setBusModels((prev) => [created, ...prev]);
                        setNewBusModelName(""); setNewBusModelDescription(""); setNewBusModelManufacturer(""); setNewBusModelSpecsText(""); setShowCreateBusModel(false);
                      } catch (e: any) {
                        alert(t("busModels.createFailed", { error: e?.message || String(e) }));
                      } finally {
                        setCreatingBusModel(false);
                      }
                    }}
                  >{t("common.create", 'Create')}</button>
                  <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setShowCreateBusModel(false); setNewBusModelName(""); setNewBusModelDescription(""); setNewBusModelManufacturer(""); setNewBusModelSpecsText(""); }}>{t("common.cancel")}</button>
                </div>
              </div>
            )}
            <div className="text-sm text-gray-700 flex items-center justify-between">
              <span>{t("busModels.listTitle", 'Models')}</span>
              {busModelsLoading && <span className="text-xs text-gray-500">{t("common.loading")}</span>}
            </div>
            {busModelsError && <div className="text-sm text-red-600">{busModelsError}</div>}
            {(!busModelsLoading && busModels.length === 0) ? (
              <div className="text-sm text-gray-600">{t("busModels.empty", 'No models')}</div>
            ) : (
              <ul className="space-y-2 mt-2">
                {busModels.map((m) => (
                  <li key={m.id} className="border rounded-lg p-2">
                    {editingBusModelId === m.id ? (
                      <div className="space-y-2">
                        <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.name", 'Name')} value={(editingBusModel.name as string) ?? m.name} onChange={(e) => setEditingBusModel((prev) => ({ ...prev, name: e.target.value }))} />
                        <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.description", 'Description')} value={(editingBusModel.description as string) ?? (m.description || '')} onChange={(e) => setEditingBusModel((prev) => ({ ...prev, description: e.target.value }))} />
                        <input className="w-full px-2 py-1 border rounded" placeholder={t("busModels.form.manufacturer", 'Manufacturer')} value={(editingBusModel.manufacturer as string) ?? (m.manufacturer || '')} onChange={(e) => setEditingBusModel((prev) => ({ ...prev, manufacturer: e.target.value }))} />
                        <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t("busModels.form.specs", 'Specs (JSON)')} value={(editingBusModel.specsText as string) ?? JSON.stringify(m.specs ?? {}, null, 2)} onChange={(e) => setEditingBusModel((prev) => ({ ...prev, specsText: e.target.value }))} />
                        <div className="flex gap-2">
                          <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} onClick={async () => {
                            if (!effectiveBaseUrl || !token) return;
                            try {
                              const payload: any = { agency_id: agencyId };
                              if (editingBusModel.name !== undefined) payload.name = editingBusModel.name;
                              if (editingBusModel.description !== undefined) payload.description = editingBusModel.description;
                              if (editingBusModel.manufacturer !== undefined) payload.manufacturer = editingBusModel.manufacturer;
                              if (editingBusModel.specsText !== undefined) {
                                try { payload.specs = editingBusModel.specsText ? JSON.parse(editingBusModel.specsText as string) : {}; } catch { alert(t("busModels.parseError", 'Invalid JSON in specs')); return; }
                              }
                              const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/bus-models/${encodeURIComponent(m.id)}`), {
                                method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload)
                              });
                              if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                              const updated = (await res.json()) as BusModel;
                              setBusModels((prev) => prev.map((x) => x.id === m.id ? updated : x));
                              setEditingBusModelId(null); setEditingBusModel({});
                            } catch (e: any) { alert(t("busModels.saveFailed", { error: e?.message || String(e) })); }
                          }}>{t("common.save")}</button>
                          <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setEditingBusModelId(null); setEditingBusModel({}); }}>{t("common.cancel")}</button>
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
                          <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} onClick={() => { setEditingBusModelId(m.id); setEditingBusModel({}); }}>{t("common.edit")}</button>
                          <button className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700" onClick={async () => {
                            if (!effectiveBaseUrl || !token) return;
                            if (!window.confirm(t("busModels.confirmDelete", { name: m.name }))) return;
                            try {
                              const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/bus-models/${encodeURIComponent(m.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                              if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                              setBusModels((prev) => prev.filter((x) => x.id !== m.id));
                            } catch (e: any) { alert(t("busModels.deleteFailed", { error: e?.message || String(e) })); }
                          }}>{t("common.delete")}</button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Buses frame */}
          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("buses.title", 'Buses')}</h2>
            <div className="flex gap-2 mb-2">
              <button
                className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#002AA7'}}
                disabled={!token || !agencyId}
                onClick={() => setShowCreateBus((v) => !v)}
                title={!token ? t("depots.authRequired") : !agencyId ? t("depots.selectAgencyBackend") : ''}
              >
                {t("buses.createButton", showCreateBus ? 'Close' : 'Create bus')}
              </button>
              <button
                className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#6b7280'}}
                disabled={!token || !agencyId}
                onClick={() => void loadBusesForAgency()}
              >
                {t("common.refresh", 'Refresh')}
              </button>
            </div>
            {showCreateBus && (
              <div className="mb-3 border rounded-lg p-2 space-y-2">
                <input className="w-full px-2 py-1 border rounded" placeholder={t("buses.form.name", 'Name')} value={newBusName} onChange={(e) => setNewBusName(e.target.value)} />
                <select className="w-full px-2 py-1 border rounded" value={newBusModelId} onChange={(e) => setNewBusModelId(e.target.value)}>
                  <option value="">{t("buses.form.selectModel", 'Select model')}</option>
                  {busModels.map((m) => (<option key={m.id} value={m.id}>{m.name}</option>))}
                </select>
                <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t("buses.form.specs", 'Specs (JSON)')} value={newBusSpecsText} onChange={(e) => setNewBusSpecsText(e.target.value)} />
                <div className="flex gap-2">
                  <button
                    className="px-2 py-1 rounded text-white text-sm hover:opacity-90"
                    style={{backgroundColor: '#74C244'}}
                    disabled={!token || !agencyId || creatingBus || !newBusName.trim()}
                    onClick={async () => {
                      if (!effectiveBaseUrl || !token || !agencyId) return;
                      try {
                        setCreatingBus(true);
                        let specs: any = {};
                        if (newBusSpecsText.trim()) {
                          try { specs = JSON.parse(newBusSpecsText); } catch (e) { alert(t("buses.parseError", 'Invalid JSON in specs')); setCreatingBus(false); return; }
                        }
                        const res = await fetch(joinUrl(effectiveBaseUrl, "/api/v1/agency/buses/"), {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                          body: JSON.stringify({ agency_id: agencyId, name: newBusName, bus_model_id: newBusModelId || null, specs })
                        });
                        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                        const created = (await res.json()) as Bus;
                        setBuses((prev) => [created, ...prev]);
                        setNewBusName(""); setNewBusModelId(""); setNewBusSpecsText(""); setShowCreateBus(false);
                      } catch (e: any) {
                        alert(t("buses.createFailed", { error: e?.message || String(e) }));
                      } finally {
                        setCreatingBus(false);
                      }
                    }}
                  >{t("common.create", 'Create')}</button>
                  <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setShowCreateBus(false); setNewBusName(""); setNewBusModelId(""); setNewBusSpecsText(""); }}>{t("common.cancel")}</button>
                </div>
              </div>
            )}
            <div className="text-sm text-gray-700 flex items-center justify-between">
              <span>{t("buses.listTitle", 'Buses for agency')}</span>
              {busesLoading && <span className="text-xs text-gray-500">{t("common.loading")}</span>}
            </div>
            {busesError && <div className="text-sm text-red-600">{busesError}</div>}
            {(!busesLoading && buses.length === 0) ? (
              <div className="text-sm text-gray-600">{t("buses.empty", 'No buses')}</div>
            ) : (
              <ul className="space-y-2 mt-2">
                {buses.map((b) => (
                  <li key={b.id} className="border rounded-lg p-2">
                    {editingBusId === b.id ? (
                      <div className="space-y-2">
                        <input className="w-full px-2 py-1 border rounded" placeholder={t("buses.form.name", 'Name')} value={(editingBus.name as string) ?? b.name} onChange={(e) => setEditingBus((prev) => ({ ...prev, name: e.target.value }))} />
                        <select className="w-full px-2 py-1 border rounded" value={(editingBus.bus_model_id as string) ?? (b.bus_model_id || '')} onChange={(e) => setEditingBus((prev) => ({ ...prev, bus_model_id: e.target.value || null }))}>
                          <option value="">{t("buses.form.selectModel", 'Select model')}</option>
                          {busModels.map((m) => (<option key={m.id} value={m.id}>{m.name}</option>))}
                        </select>
                        <textarea className="w-full px-2 py-1 border rounded font-mono text-xs" rows={3} placeholder={t("buses.form.specs", 'Specs (JSON)')} value={(editingBus.specsText as string) ?? JSON.stringify(b.specs ?? {}, null, 2)} onChange={(e) => setEditingBus((prev) => ({ ...prev, specsText: e.target.value }))} />
                        <div className="flex gap-2">
                          <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#74C244'}} onClick={async () => {
                            if (!effectiveBaseUrl || !token) return;
                            try {
                              const payload: any = {};
                              if (editingBus.name !== undefined) payload.name = editingBus.name;
                              if (editingBus.bus_model_id !== undefined) payload.bus_model_id = editingBus.bus_model_id || null;
                              if (editingBus.specsText !== undefined) {
                                try { payload.specs = editingBus.specsText ? JSON.parse(editingBus.specsText as string) : {}; } catch { alert(t("buses.parseError", 'Invalid JSON in specs')); return; }
                              }
                              const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/buses/${encodeURIComponent(b.id)}`), {
                                method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify(payload)
                              });
                              if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                              const updated = (await res.json()) as Bus;
                              setBuses((prev) => prev.map((x) => x.id === b.id ? updated : x));
                              setEditingBusId(null); setEditingBus({});
                            } catch (e: any) { alert(t("buses.saveFailed", { error: e?.message || String(e) })); }
                          }}>{t("common.save")}</button>
                          <button className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => { setEditingBusId(null); setEditingBus({}); }}>{t("common.cancel")}</button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start justify-between gap-2">
                        <div className="text-sm">
                          <div className="font-medium">{b.name}</div>
                          <div className="text-gray-600">{busModels.find((m) => m.id === (b.bus_model_id || ''))?.name || t("buses.noModel", 'No model')}</div>
                        </div>
                        <div className="flex gap-2">
                          <button className="px-2 py-1 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} onClick={() => { setEditingBusId(b.id); setEditingBus({}); }}>{t("common.edit")}</button>
                          <button className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700" onClick={async () => {
                            if (!effectiveBaseUrl || !token) return;
                            if (!window.confirm(t("buses.confirmDelete", { name: b.name }))) return;
                            try {
                              const res = await fetch(joinUrl(effectiveBaseUrl, `/api/v1/agency/buses/${encodeURIComponent(b.id)}`), { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
                              if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                              setBuses((prev) => prev.filter((x) => x.id !== b.id));
                            } catch (e: any) { alert(t("buses.deleteFailed", { error: e?.message || String(e) })); }
                          }}>{t("common.delete")}</button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>


          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("summary.title")}</h2>
            <ul className="space-y-2 text-sm">
              <li>
                <span className="text-gray-600">{t("summary.tripsLoaded")}</span> {sortedTrips.length}
              </li>
              <li>
                <span className="text-gray-600">{t("summary.selected")}</span> {selectedTrips.length}
              </li>
              <li>
                <span className="text-gray-600">{t("summary.nextCandidates")}</span> {nextCandidates.length}
              </li>
              {lastSelected && (
                <li>
                  <span className="text-gray-600">{t("summary.lastArrival")}</span> {formatDayHHMM(lastSelected.arrival_sec)} {t("summary.atStop", { stop: normalizeStop(lastSelected.end_stop_name) })}
                </li>
              )}
            </ul>
          </div>

          <div className="p-3 rounded-2xl bg-white shadow-sm border">
            <h2 className="text-lg font-medium mb-3">{t("selfTests.title")}</h2>
            <ul className="text-xs space-y-1">
              {tests.map((t, i) => (
                <li key={i} style={{color: '#000'}}>
                  {t.pass ? "✔" : "✘"} {t.name}{t.msg ? ` — ${t.msg}` : ""}
                </li>
              ))}
            </ul>
          </div>
        </section>

        {mode === "planner" ? (
          <>
            {/* Middle: Available trips */}
            <section className="relative lg:col-span-2 p-3 rounded-2xl bg-white shadow-sm border min-h-[60vh] flex flex-col">
              <div className="flex items-baseline justify-between mb-3">
                <h2 className="text-lg font-medium">{t("available.title")}</h2>
                <span className="text-sm text-gray-600">
                  {t("available.subtitle")}
                  {loading ? ` · ${t("common.loading")}` : ""}
                </span>
              </div>
              <div className="flex items-center justify-between mb-2 text-sm">
                <div>
                  {t("available.showingRange", {
                    from: nextCandidates.length === 0 ? 0 : startIndex + 1,
                    to: endIndex,
                    total: nextCandidates.length,
                  })}
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-gray-600">{t("available.perPage")}</label>
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
                      {t("common.prev")}
                    </button>
                    <span className="text-gray-600">{t("available.pageStatus", { current: currentPage, total: totalPages })}</span>
                    <button
                      className="px-2 py-1 border rounded disabled:opacity-50"
                      onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                      disabled={currentPage >= totalPages}
                    >
                      {t("common.next")}
                    </button>
                  </div>
                </div>
              </div>

              {/* Overlay hint when trips are loaded but Leave depot not set or shift not started */}
              {((rawTrips.length > 0 && !leaveDepotInfo) || !creatingShift) && (
                <div className="absolute inset-0 z-10 flex items-start justify-center pointer-events-none">
                  <div className="mt-12 px-4 py-2 rounded-lg text-sm shadow" style={{color: '#3B3C48', backgroundColor: '#f8f9fa', borderColor: '#dee2e6', border: '1px solid'}}>
                    {!creatingShift ? t("shifts.overlayCreateFirst", 'Click "Create shift" and set name + bus to begin') : t("available.leaveDepotOverlay")}
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 gap-1 overflow-auto pr-1">
                {rawTrips.length > 0 && pagedNextCandidates.map((trip) => (
                  <TripCard
                    key={trip.id}
                    trip={trip}
                    disabled={
                      !creatingShift ||
                      !leaveDepotInfo ||
                      (selectedIds.length === 0 && leaveDepotInfo !== null && trip.departure_sec < parseHHMMToSec(leaveDepotInfo.timeHHMM)) ||
                      (selectedIds.length > 0 && (onlyValidNext ? !computeValidNext(lastSelected, trip) : (trip.departure_sec < (lastSelected?.arrival_sec ?? 0))))
                    }
                    used={used.has(trip.id)}
                    transferNeeded={(!onlyValidNext && !!lastSelected && normalizeStop(trip.start_stop_name) !== normalizeStop(lastSelected.end_stop_name))}
                    onPick={handlePickTrip(trip)}
                    onHover={() => {
                      if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current);
                      hoverTimerRef.current = window.setTimeout(() => {
                        setHoveredTripId(trip.id); // use database id (UUID)
                        ensureStopsForTrip(trip.id);
                        ensureElevationForTrip(trip.id);
                      }, 1000);
                    }}
                    onLeave={() => {
                      if (hoverTimerRef.current) {
                        window.clearTimeout(hoverTimerRef.current);
                        hoverTimerRef.current = null;
                      }
                      setHoveredTripId((prev) => (prev === trip.id ? null : prev));
                    }}
                    hovered={hoveredTripId === trip.id}
                    stops={stopsByTrip[trip.id]}
                    stopsLoading={stopsLoadingTripId === trip.id}
                    stopsError={stopsErrorByTrip[trip.id]}
                    elevation={elevationByTrip[trip.id]}
                    elevationLoading={elevationLoadingTripId === trip.id}
                    elevationError={elevationErrorByTrip[trip.id]}
                  />
                ))}
                {rawTrips.length === 0 ? (
                  <div className="text-sm text-gray-600">{t("available.noTripsLoaded")}</div>
                ) : pagedNextCandidates.length === 0 ? (
                  <div className="text-sm text-gray-600">{t("available.noMatches", { filterLabel: t("filters.showDisconnected") })}</div>
                ) : null}
              </div>
            </section>

            {/* Right: Selected shift */}
            <section className="lg:col-span-3 p-4 rounded-2xl bg-white shadow-sm border">
              <div className="flex items-baseline justify-between mb-3">
                <h2 className="text-lg font-medium">{t("selected.title")}</h2>
                <span className="text-sm text-gray-600">{t("selected.subtitle")}</span>
              </div>
              <div className="space-y-3">
                {!leaveDepotInfo && selectedTrips.length === 0 && (
                  <div className="text-sm text-gray-600">{t("selected.empty")}</div>
                )}
                {leaveDepotInfo && (
                  <div className="p-3 border rounded-xl bg-blue-50">
                    <div className="flex items-center justify-between">
                      <div className="font-medium">{t("selected.depotDepartureTitle")}</div>
                      <div className="text-xs text-gray-600">{t("selected.timeLabel", { time: leaveDepotInfo.timeHHMM })}</div>
                    </div>
                    <div className="text-xs text-gray-600">
                      {t("selected.depotDetails", {
                        name: depots.find((d) => d.id === leaveDepotInfo.depotId)?.name || t("selected.unknownDepot"),
                        id: leaveDepotInfo.depotId,
                      })}
                    </div>
                  </div>
                )}
                {selectedTrips.length > 0 && (
                  <ol className="space-y-3">
                    {selectedTrips.map((trip, idx) => (
                      <li key={trip.id} className="p-3 border rounded-xl flex flex-col gap-1">
                        <div className="flex items-center justify-between">
                          <div className="font-medium">{trip.start_stop_name} → {trip.end_stop_name}</div>
                          <div className="text-xs text-gray-600">{t("selected.tripNumber", { index: idx + 1 })}</div>
                        </div>
                        <div className="text-sm">
                          <span className="inline-block mr-4">{t("selected.departure", { time: formatDayHHMM(trip.departure_sec) })}</span>
                          <span>{t("selected.arrival", { time: formatDayHHMM(trip.arrival_sec) })}</span>
                        </div>
                        <div className="text-xs text-gray-600">
                          {(() => {
                            const r = routes.find((x) => x.id === trip.route_id);
                            const rLabel = (r?.route_short_name || r?.route_long_name || r?.route_id || trip.route_id) as string;
                            return (
                              <>{t("selected.routeDetails", {
                                route: rLabel,
                                id: trip.route_id,
                                trip: trip.trip_short_name || trip.trip_id,
                                headsign: trip.trip_headsign,
                              })}</>
                            );
                          })()}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
                {returnDepotInfo && (
                  <div className="p-3 border rounded-xl" style={{backgroundColor: '#f0f9ff', borderColor: '#74C244'}}>
                    <div className="flex items-center justify-between">
                      <div className="font-medium">{t("selected.depotReturnTitle")}</div>
                      <div className="text-xs text-gray-600">{t("selected.timeLabel", { time: returnDepotInfo.timeHHMM })}</div>
                    </div>
                    <div className="text-xs text-gray-600">
                      {t("selected.depotDetails", {
                        name: depots.find((d) => d.id === returnDepotInfo.depotId)?.name || t("selected.unknownDepot"),
                        id: returnDepotInfo.depotId,
                      })}
                    </div>
                  </div>
                )}
              </div>
            <div className="mt-3 flex gap-2 text-sm">
              <button onClick={handleUndo} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200">{t("selected.undo")}</button>
              <button onClick={handleReset} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200">{t("selected.reset")}</button>
              {(() => {
                const hasCore = selectedTrips.length > 0 && !!leaveDepotInfo && !!returnDepotInfo;
                const lastArr = selectedTrips.length > 0 ? selectedTrips[selectedTrips.length - 1].arrival_sec : undefined;
                const retOk = hasCore && lastArr !== undefined && parseHHMMToSec(returnDepotInfo!.timeHHMM) > lastArr;
                const canExport = Boolean(retOk);
                return (
                  <button
                    onClick={handleSaveShift}
                    className="px-3 py-2 rounded-lg text-white hover:opacity-90 disabled:opacity-50"
                    style={{backgroundColor: '#74C244'}}
                    disabled={!canExport || !creatingShift || !shiftName.trim() || !shiftBusId}
                    title={!creatingShift ? (t("shifts.createFirst", 'Create shift to enable saving') as any) : (!hasCore ? t("selected.exportHintIncomplete") : (!retOk ? t("selected.exportHintReturn") : (!shiftName.trim() || !shiftBusId ? (t("shifts.nameBusRequired", 'Name and bus are required') as any) : "")))}
                  >
                    {exporting ? (exportMessage || t("shifts.saving", 'Saving shift...')) : t("shifts.saveButton", 'Save shift')}
                  </button>
                );
              })()}
            </div>
            </section>
          </>
        ) : (
          <section className="lg:col-span-2 p-3 rounded-2xl bg-white shadow-sm border min-h-[60vh] flex flex-col">
            <CreateDepotView
              token={token}
              agencyId={agencyId}
              baseUrl={effectiveBaseUrl}
              onCancel={() => setMode("planner")}
              onCreated={(dep?: any) => {
                // Non-blocking success notice
                setDepotsNotice(dep?.name ? t("depots.createdWithName", { name: dep.name }) : t("depots.created"));
                setTimeout(() => setDepotsNotice(""), 3000);
                // Reload list immediately so it appears without a page refresh
                void loadDepotsForAgency();
                setMode("planner");
              }}
            />
          </section>
        )}
      </main>
      {/* Depot modal */}
      {showDepotDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-lg w-full max-w-md p-4 border">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-medium">{showDepotDialog === "leave" ? t("depotModal.leaveTitle") : t("depotModal.returnTitle")}</h3>
              <button className="text-sm px-2 py-1 rounded bg-gray-100 hover:bg-gray-200" onClick={() => setShowDepotDialog(null)}>{t("common.close")}</button>
            </div>
            {modalError && <div className="mb-2 text-sm text-red-600">{modalError}</div>}
            <div className="space-y-2">
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("depotModal.depotLabel")}</label>
                <select className="w-full px-3 py-2 border rounded-lg" value={modalDepotId} onChange={(e) => setModalDepotId(e.target.value)}>
                  <option value="">{t("depotModal.selectDepot")}</option>
                  {depots.filter((d) => d.agency_id === agencyId && d.stop_id).map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("depotModal.timeLabel")}</label>
                <input className="w-full px-3 py-2 border rounded-lg" placeholder={t("depotModal.timePlaceholder")} value={modalTime} onChange={(e) => setModalTime(e.target.value)} />
              </div>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button className="px-3 py-2 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => setShowDepotDialog(null)}>{t("common.cancel")}</button>
              <button
                className="px-3 py-2 rounded text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#002AA7'}}
                disabled={!modalDepotId || !/^\d{1,2}:\d{2}$/.test(modalTime)}
                onClick={() => {
                  setModalError("");
                  if (!/^\d{1,2}:\d{2}$/.test(modalTime)) { setModalError(t("depotModal.invalidTime")); return; }
                  if (showDepotDialog === "leave") {
                    setLeaveDepotInfo({ depotId: modalDepotId, timeHHMM: modalTime });
                    setShowDepotDialog(null);
                  } else {
                    if (selectedTrips.length > 0) {
                      const lastArr = selectedTrips[selectedTrips.length - 1].arrival_sec;
                      const [h, m] = modalTime.split(":").map((x) => parseInt(x, 10));
                      const sec = (h * 3600) + (m * 60);
                      if (sec <= lastArr) { setModalError(t("depotModal.returnLater", { time: formatDayHHMM(lastArr) })); return; }
                    }
                    setReturnDepotInfo({ depotId: modalDepotId, timeHHMM: modalTime });
                    setShowDepotDialog(null);
                  }
                }}
              >
                {t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Start Shift modal */}
      {showStartShiftDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-lg w-full max-w-md p-4 border">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-medium">{t("shifts.startTitle", 'Create shift')}</h3>
              <button className="text-sm px-2 py-1 rounded bg-gray-100 hover:bg-gray-200" onClick={() => setShowStartShiftDialog(false)}>{t("common.close")}</button>
            </div>
            <div className="space-y-2">
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("shifts.nameLabel", 'Shift name')}</label>
                <input className="w-full px-3 py-2 border rounded-lg" value={shiftName} onChange={(e) => setShiftName(e.target.value)} placeholder={t("shifts.namePlaceholder", 'e.g. Morning peak 1')} />
              </div>
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("shifts.busLabel", 'Bus')}</label>
                <select className="w-full px-3 py-2 border rounded-lg" value={shiftBusId} onChange={(e) => setShiftBusId(e.target.value)}>
                  <option value="">{t("shifts.selectBus", 'Select bus')}</option>
                  {buses.filter((b) => b.agency_id === agencyId).map((b) => (
                    <option key={b.id} value={b.id}>{b.name}</option>
                  ))}
                </select>
              </div>
              <div className="text-xs text-gray-600">{t("shifts.startHint2", 'After starting, select day and route, then build the shift.')}</div>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button className="px-3 py-2 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => setShowStartShiftDialog(false)}>{t("common.cancel")}</button>
              <button
                className="px-3 py-2 rounded text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#002AA7'}}
                disabled={!shiftName.trim() || !shiftBusId}
                onClick={() => {
                  setShowStartShiftDialog(false);
                  setCreatingShift(true);
                  // Clear any previous selections
                  handleReset();
                }}
              >
                {t("shifts.startButton", 'Start')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Transfer modal */}
      {showTransferDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-lg w-full max-w-md p-4 border">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-medium">{t("transferModal.title")}</h3>
              <button className="text-sm px-2 py-1 rounded bg-gray-100 hover:bg-gray-200" onClick={() => setShowTransferDialog(null)}>{t("common.close")}</button>
            </div>
            {transferModalError && <div className="mb-2 text-sm text-red-600">{transferModalError}</div>}
            <div className="text-sm text-gray-700 mb-2">
              {t("transferModal.summary", {
                from: normalizeStop(showTransferDialog.prev.end_stop_name),
                to: normalizeStop(showTransferDialog.next.start_stop_name),
              })}
            </div>
            <div className="space-y-2">
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("transferModal.departureLabel")}</label>
                <input className="w-full px-3 py-2 border rounded-lg" placeholder={t("transferModal.departurePlaceholder")} value={transferDepHHMM} onChange={(e) => setTransferDepHHMM(e.target.value)} />
              </div>
              <div>
                <label className="block text-sm text-gray-700 mb-1">{t("transferModal.arrivalLabel")}</label>
                <input className="w-full px-3 py-2 border rounded-lg" placeholder={t("transferModal.arrivalPlaceholder")} value={transferArrHHMM} onChange={(e) => setTransferArrHHMM(e.target.value)} />
              </div>
              <div className="text-xs text-gray-600">
                {t("transferModal.constraints", {
                  depMin: formatDayHHMM(showTransferDialog.prev.arrival_sec),
                  arrMax: formatDayHHMM(showTransferDialog.next.departure_sec),
                })}
              </div>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button className="px-3 py-2 rounded bg-gray-100 hover:bg-gray-200 text-sm" onClick={() => setShowTransferDialog(null)}>{t("common.cancel")}</button>
              <button
                className="px-3 py-2 rounded text-white text-sm hover:opacity-90 disabled:opacity-50"
                style={{backgroundColor: '#002AA7'}}
                disabled={!/^\d{1,2}:\d{2}$/.test(transferDepHHMM) || !/^\d{1,2}:\d{2}$/.test(transferArrHHMM)}
                onClick={() => {
                  setTransferModalError("");
                  if (!/^\d{1,2}:\d{2}$/.test(transferDepHHMM) || !/^\d{1,2}:\d{2}$/.test(transferArrHHMM)) { setTransferModalError(t("transferModal.invalidFormat")); return; }
                  const [dh, dm] = transferDepHHMM.split(":").map((x) => parseInt(x, 10));
                  const [ah, am] = transferArrHHMM.split(":").map((x) => parseInt(x, 10));
                  const depSec = dh * 3600 + dm * 60;
                  const arrSec = ah * 3600 + am * 60;
                  if (depSec < showTransferDialog.prev.arrival_sec) { setTransferModalError(t("transferModal.departureBeforePrev")); return; }
                  if (arrSec < depSec) { setTransferModalError(t("transferModal.arrivalBeforeDep")); return; }
                  if (arrSec > showTransferDialog.next.departure_sec) { setTransferModalError(t("transferModal.arrivalAfterNext")); return; }
                  const key = `${showTransferDialog.prev.id}__${showTransferDialog.next.id}`;
                  setTransfersByEdge((prev) => ({ ...prev, [key]: { depHHMM: transferDepHHMM, arrHHMM: transferArrHHMM } }));
                  // Append the selected trip now that transfer has been collected
                  setSelectedIds((prev) => (prev.includes(showTransferDialog.next.id) ? prev : [...prev, showTransferDialog.next.id]));
                  setShowTransferDialog(null);
                }}
              >
                {t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

// ---------- Card ----------
function TripCard({
  trip,
  disabled,
  used,
  transferNeeded,
  onPick,
  onHover,
  onLeave,
  hovered,
  stops,
  stopsLoading,
  stopsError,
  elevation,
  elevationLoading,
  elevationError,
}: {
  trip: TripX;
  disabled?: boolean;
  used?: boolean;
  transferNeeded?: boolean;
  onPick: () => void;
  onHover?: () => void;
  onLeave?: () => void;
  hovered?: boolean;
  stops?: TripStop[];
  stopsLoading?: boolean;
  stopsError?: string;
  elevation?: ElevationProfile;
  elevationLoading?: boolean;
  elevationError?: string;
}) {
  const { t: tr } = useTranslation();
  function renderMiniElevationChart(profile?: ElevationProfile) {
    if (!profile || !profile.records || profile.records.length === 0) return null;
    const pts = profile.records;
    const minAlt = Math.min(...pts.map((p) => p.altitude_m));
    const maxAlt = Math.max(...pts.map((p) => p.altitude_m));
    const maxX = Math.max(...pts.map((p) => p.cumulative_distance_m));
    // Match map size: 260x180
    const W = 260;
    const H = 180;
    // Add padding so tick labels and unit labels do not overlap
    const padLeft = 44;
    const padBottom = 28;
    const padTop = 8;
    const padRight = 10;
    const innerHeight = H - padTop - padBottom;
    const scaleX = (x: number) => padLeft + (maxX === 0 ? 0 : (x / maxX) * (W - padLeft - padRight));
    const scaleY = (y: number) => {
      const norm = (y - minAlt) / (maxAlt - minAlt || 1);
      return H - padBottom - norm * innerHeight;
    };
    const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${scaleX(p.cumulative_distance_m)},${scaleY(p.altitude_m)}`).join(" ");

    // Axis ticks
    const xTicks: number[] = [];
    const numXTicks = 4;
    for (let i = 0; i <= numXTicks; i++) xTicks.push((maxX / numXTicks) * i);
    const yTicks: number[] = [];
    const numYTicks = 4;
    for (let i = 0; i <= numYTicks; i++) yTicks.push(minAlt + ((maxAlt - minAlt) / numYTicks) * i);

    return (
      <svg width={W} height={H} className="block">
        {/* Axes */}
        <line x1={padLeft} y1={padTop} x2={padLeft} y2={H - padBottom} stroke="#e5e7eb" />
        <line x1={padLeft} y1={H - padBottom} x2={W - padRight} y2={H - padBottom} stroke="#e5e7eb" />
        {/* Ticks and labels */}
        {xTicks.map((x, i) => (
          <g key={`xt-${i}`}>
            <line x1={scaleX(x)} y1={H - padBottom} x2={scaleX(x)} y2={H - padBottom + 4} stroke="#9ca3af" />
            <text x={scaleX(x)} y={H - padBottom + 16} textAnchor="middle" fontSize="10" fill="#6b7280">
              {(x / 1000).toFixed(1)}
            </text>
          </g>
        ))}
        <text x={W - padRight} y={H - 6} textAnchor="end" fontSize="10" fill="#6b7280">km</text>
        {yTicks.map((y, i) => (
          <g key={`yt-${i}`}>
            <line x1={padLeft - 4} y1={scaleY(y)} x2={padLeft} y2={scaleY(y)} stroke="#9ca3af" />
            <text x={padLeft - 6} y={scaleY(y) + 3} textAnchor="end" fontSize="10" fill="#6b7280">
              {Math.round(y)}
            </text>
          </g>
        ))}
        {/* Y-axis unit label, vertically centered and kept clear of tick labels */}
        <text x={12} y={padTop + innerHeight / 2} textAnchor="middle" fontSize="10" fill="#6b7280" transform={`rotate(-90 12 ${padTop + innerHeight / 2})`}>
          m
        </text>
        {/* Line */}
        <path d={d} stroke="#059669" strokeWidth={1.6} fill="none" />
      </svg>
    );
  }

  function renderMiniMap(profile?: ElevationProfile, stops?: TripStop[]) {
    if (!profile || !profile.records || profile.records.length === 0) return null;
    const positions = profile.records.map((r) => [r.latitude, r.longitude]) as [number, number][];
    const validStops = (stops || []).filter((s) => typeof s.stop_lat === "number" && typeof s.stop_lon === "number");
    const start = validStops.length > 0 ? [validStops[0].stop_lat as number, validStops[0].stop_lon as number] as [number, number] : null;
    const end = validStops.length > 0 ? [validStops[validStops.length - 1].stop_lat as number, validStops[validStops.length - 1].stop_lon as number] as [number, number] : null;

    const playIcon = L.divIcon({
      className: "",
      html: '<div style="background:#10b981;color:#fff;border-radius:9999px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;border:2px solid #065f46"><i class="fa-solid fa-play" style="font-size:12px;margin-left:2px"></i></div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    });
    const stopIcon = L.divIcon({
      className: "",
      html: '<div style="background:#ef4444;color:#fff;border-radius:9999px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;border:2px solid #7f1d1d"><i class="fa-solid fa-stop" style="font-size:12px"></i></div>',
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    });
    function FitBounds({ positions }: { positions: [number, number][] }) {
      const map = useMap();
      useEffect(() => {
        if (!positions || positions.length === 0) return;
        map.fitBounds(positions as any, { padding: [6, 6] });
      }, [map, positions]);
      return null;
    }
    return (
      <MapContainer {...({ className: "w-[260px] h-[180px] rounded border", center: positions[0] } as any)}>
        <TileLayer {...({ url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", maxZoom: 19 } as any)} />
        <Polyline positions={positions} pathOptions={{ color: "#2563eb", weight: 3 }} />
        {start && <Marker position={start} {...({ icon: playIcon } as any)} />}
        {end && <Marker position={end} {...({ icon: stopIcon } as any)} />}
        <FitBounds positions={positions} />
      </MapContainer>
    );
  }
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
      title={disabled ? tr("tripCard.disabledHint") : tr("tripCard.addHint")}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-medium truncate">{trip.start_stop_name} → {trip.end_stop_name}</div>
        <div className="text-xs whitespace-nowrap text-gray-700">
          {formatDayHHMM(trip.departure_sec)} → {formatDayHHMM(trip.arrival_sec)}
        </div>
      </div>
      {!disabled && transferNeeded && hovered && (
        <div className="text-[11px] text-amber-700">{tr("tripCard.transferRequired")}</div>
      )}
      <div className="mt-0.5 text-[11px] text-gray-600 truncate">
        {tr("tripCard.headsign", { headsign: trip.trip_headsign })}
      </div>
      <div className="text-[11px] text-gray-600 truncate">
        {tr("tripCard.routeTrip", { route: trip.route_id, trip: trip.trip_short_name || trip.trip_id })}
      </div>
      {hovered && (
        <div className="mt-2 border-t pt-2">
          <div className="text-[11px] font-medium text-gray-700 mb-1">{tr("tripCard.stopsTitle")}</div>
          {stopsLoading && <div className="text-[11px] text-gray-500">{tr("tripCard.loadingStops")}</div>}
          {stopsError && <div className="text-[11px] text-red-600">{stopsError}</div>}
          {!stopsLoading && !stopsError && stops && stops.length > 0 && (
            <ul className="max-h-32 overflow-auto space-y-1 pr-1">
              {stops.map((s, i) => (
                <li key={s.id || i} className="text-[11px] text-gray-700 flex items-center justify-between gap-2">
                  <span className="truncate">{s.stop_name}</span>
                  <span className="whitespace-nowrap text-gray-600">{tr("tripCard.stopTimes", { arrival: (s.arrival_time || "").slice(0, 5), departure: (s.departure_time || "").slice(0, 5) })}</span>
                </li>
              ))}
            </ul>
          )}
          {!stopsLoading && !stopsError && (!stops || stops.length === 0) && (
            <div className="text-[11px] text-gray-500">{tr("tripCard.noStops")}</div>
          )}
          <div className="mt-2">
            <div className="text-[11px] font-medium text-gray-700 mb-1">{tr("tripCard.mapTitle")}</div>
            {elevationLoading && <div className="text-[11px] text-gray-500">{tr("tripCard.loadingElevation")}</div>}
            {elevationError && <div className="text-[11px] text-red-600">{elevationError}</div>}
            {!elevationLoading && !elevationError && elevation && (
              <div className="flex items-start gap-3">
                <div>{renderMiniMap(elevation, stops)}</div>
                <div>{renderMiniElevationChart(elevation)}</div>
              </div>
            )}
          </div>
        </div>
      )}
    </button>
  );
}


// ---------- Create Depot View ----------
function CreateDepotView({ token, agencyId, baseUrl, onCancel, onCreated }: {
  token: string;
  agencyId: string;
  baseUrl: string;
  onCancel: () => void;
  onCreated: (dep?: any) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState<string>("");
  const [address, setAddress] = useState<string>("");
  // city field removed from backend; keep local for UI address search only
  // city removed from backend; no longer collected
  const [latitude, setLatitude] = useState<number | null>(null);
  const [longitude, setLongitude] = useState<number | null>(null);
  const [center, setCenter] = useState<[number, number]>([46.0037, 8.9511]); // Lugano
  const [zoom] = useState<number>(13);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [suggestions, setSuggestions] = useState<Array<{ label: string; lat: number; lon: number }>>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const searchTimer = useRef<number | null>(null);

  function stripHtml(input: string): string {
    try {
      return (input || "").replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();
    } catch {
      return input;
    }
  }

  useEffect(() => {
    const q = searchQuery.trim();
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    if (q.length < 3) {
      setSuggestions([]);
      return;
    }
    searchTimer.current = window.setTimeout(async () => {
      try {
        const url = `https://api3.geo.admin.ch/rest/services/api/SearchServer?sr=4326&type=locations&origins=address&lang=en&searchText=${encodeURIComponent(q)}`;
        const res = await fetch(url);
        const data = await res.json();
        const results = Array.isArray(data?.results) ? data.results : [];
        const mapped = results.map((r: any) => {
          const attrs = r?.attrs || {};
          const lat = typeof attrs.lat === "number" ? attrs.lat : (typeof attrs.y === "number" ? attrs.y : null);
          const lon = typeof attrs.lon === "number" ? attrs.lon : (typeof attrs.x === "number" ? attrs.x : null);
          const label = stripHtml((attrs.label || r?.label || "").toString());
          return lat != null && lon != null ? { label, lat, lon } : null;
        }).filter(Boolean) as Array<{ label: string; lat: number; lon: number }>;
        setSuggestions(mapped.slice(0, 8));
      } catch {
        setSuggestions([]);
      }
    }, 300);
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
    };
  }, [searchQuery]);

  function SetView({ center }: { center: [number, number] }) {
    const map = useMap();
    useEffect(() => {
      map.setView(center);
    }, [map, center]);
    return null;
  }

  function ClickCapture() {
    const map = useMap();
    useEffect(() => {
      function onClick(e: any) {
        const lat = e.latlng?.lat;
        const lon = e.latlng?.lng;
        if (typeof lat === "number" && typeof lon === "number") {
          setLatitude(lat);
          setLongitude(lon);
        }
      }
      (map as any).on("click", onClick);
      return () => {
        (map as any).off("click", onClick);
      };
    }, [map]);
    return null;
  }

  async function submit() {
    setError("");
    if (!token) {
      setError(t("createDepot.errors.loginFirst"));
      return;
    }
    if (!agencyId) {
      setError(t("createDepot.errors.selectAgency"));
      return;
    }
    if (!name.trim()) {
      setError(t("createDepot.errors.nameRequired"));
      return;
    }
    if (latitude != null && (latitude < -90 || latitude > 90)) {
      setError(t("createDepot.errors.latitudeRange"));
      return;
    }
    if (longitude != null && (longitude < -180 || longitude > 180)) {
      setError(t("createDepot.errors.longitudeRange"));
      return;
    }
    try {
      setLoading(true);
      const payload: any = {
        agency_id: agencyId,
        name: name.trim(),
      };
      if (address.trim()) payload.address = address.trim();
      // city is not part of backend payload anymore
      if (latitude != null) payload.latitude = latitude;
      if (longitude != null) payload.longitude = longitude;
      const res = await fetch(joinUrl(baseUrl, "/api/v1/agency/depots/"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      onCreated(data);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t("createDepot.title")}</h2>
        <button onClick={onCancel} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm">{t("common.back")}</button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="space-y-2">
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t("createDepot.form.nameLabel")}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("createDepot.form.namePlaceholder")} />
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t("createDepot.form.searchLabel")}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder={t("createDepot.form.searchPlaceholder")} />
            {suggestions.length > 0 && (
              <ul className="mt-1 max-h-48 overflow-auto border rounded-lg bg-white shadow text-sm">
                {suggestions.map((s, i) => (
                  <li
                    key={`${s.label}-${i}`}
                    className="px-3 py-2 hover:bg-gray-100 cursor-pointer"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setAddress(s.label);
                      setCenter([s.lat, s.lon]);
                      setLatitude(s.lat);
                      setLongitude(s.lon);
                      setSuggestions([]);
                      setSearchQuery(s.label);
                    }}
                  >
                    {s.label}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t("createDepot.form.addressLabel")}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={address} onChange={(e) => setAddress(e.target.value)} placeholder={t("createDepot.form.addressPlaceholder")} />
          </div>
          {/* City field removed: backend no longer stores it */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-sm text-gray-700 mb-1">{t("createDepot.form.latitudeLabel")}</label>
              <input className="w-full px-3 py-2 border rounded-lg" value={latitude ?? ""} onChange={(e) => setLatitude(e.target.value ? parseFloat(e.target.value) : null)} placeholder={t("createDepot.form.latitudePlaceholder")} />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">{t("createDepot.form.longitudeLabel")}</label>
              <input className="w-full px-3 py-2 border rounded-lg" value={longitude ?? ""} onChange={(e) => setLongitude(e.target.value ? parseFloat(e.target.value) : null)} placeholder={t("createDepot.form.longitudePlaceholder")} />
            </div>
          </div>
        </div>
        <div>
          <MapContainer {...({ className: "w-full h-[380px] rounded border", center } as any)} zoom={zoom as any}>
            <TileLayer {...({ url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", maxZoom: 19 } as any)} />
            <SetView center={center} />
            <ClickCapture />
            {latitude != null && longitude != null && <Marker position={[latitude, longitude] as any} />}
          </MapContainer>
          <div className="mt-1 text-xs text-gray-600">{t("createDepot.mapHelp")}</div>
        </div>
      </div>
      {error && <div className="text-sm text-red-600">{error}</div>}
      <div className="flex gap-2">
        <button onClick={submit} disabled={loading || !token || !agencyId || !name.trim()} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#74C244'}}>
          {loading ? t("createDepot.creating") : t("createDepot.create")}
        </button>
        <button onClick={onCancel} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm">{t("common.cancel")}</button>
      </div>
    </div>
  );
}
