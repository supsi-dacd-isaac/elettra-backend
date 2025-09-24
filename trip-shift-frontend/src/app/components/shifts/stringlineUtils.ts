export function toMinutes(hhmm: string | null | undefined): number | null {
  if (!hhmm) return null;
  const parts = hhmm.split(':').map((x) => parseInt(x, 10));
  const h = parts[0] || 0;
  const m = parts[1] || 0;
  return h * 60 + m;
}
export type StopMeta = {
  id: string;
  name: string;
  lat?: number | null;
  lon?: number | null;
};

export type SeriesPoint = {
  stopId: string;
  minutes: number;
  arrivalHHMM?: string | null;
  departureHHMM?: string | null;
};
export type SeriesKind = 'trip' | 'depot' | 'transfer';
export type Series = { id: string; label?: string; colorKey?: string; kind?: SeriesKind; points: SeriesPoint[] };

export type TripStopLite = {
  id: string;
  stop_name: string;
  stop_lat?: number | null;
  stop_lon?: number | null;
  arrival_time?: string | null;
  departure_time?: string | null;
};

export function parseGtfsTimeToMinutes(t?: string | null): number | null {
  if (!t) return null;
  const parts = t.split(":").map((x) => parseInt(x, 10));
  const h = parts[0] || 0;
  const m = parts[1] || 0;
  const s = parts[2] || 0;
  return h * 60 + m + (s >= 30 ? 1 : 0);
}

export function computeDominantStopOrder(stopMeta: Record<string, StopMeta>, reverse = false): string[] {
  const values = Object.values(stopMeta);
  if (values.length === 0) return [];
  const lats = values.map((v) => (v.lat == null ? null : Number(v.lat))).filter((x): x is number => x != null);
  const lons = values.map((v) => (v.lon == null ? null : Number(v.lon))).filter((x): x is number => x != null);
  const latSpan = lats.length ? Math.max(...lats) - Math.min(...lats) : 0;
  const lonSpan = lons.length ? Math.max(...lons) - Math.min(...lons) : 0;
  const useLon = lonSpan >= latSpan;
  const ordered = [...values].sort((a, b) => {
    if (useLon && a.lon != null && b.lon != null) return a.lon - b.lon || ((a.lat || 0) - (b.lat || 0));
    if (a.lat != null && b.lat != null) return a.lat - b.lat || ((a.lon || 0) - (b.lon || 0));
    return (a.name || a.id).localeCompare(b.name || b.id);
  });
  if (reverse) ordered.reverse();
  return ordered.map((v) => v.id);
}

/**
 * Compute a simple sequential stop order based on the first series path.
 * Used as a fallback to avoid zigzag when only one trip is shown but
 * coordinates produce a misleading geographic order.
 */
export function sequentialOrderFromSeries(series: Series[]): string[] | null {
  if (!series || series.length === 0) return null;
  // pick the longest series
  const longest = [...series].sort((a, b) => b.points.length - a.points.length)[0];
  if (!longest || longest.points.length === 0) return null;
  const seen = new Set<string>();
  const order: string[] = [];
  for (const p of longest.points) {
    if (!seen.has(p.stopId)) {
      seen.add(p.stopId);
      order.push(p.stopId);
    }
  }
  return order.length ? order : null;
}

/**
 * Compute stop order anchored to the first selected trip, merging subsequent trips' stops
 * relative to the nearest already-known anchor in the current order.
 */
export function computeMergedStopOrderFromSelection(
  selectedIds: string[],
  stopsByTrip: Record<string, TripStopLite[]>,
  reverse = false
): string[] {
  if (!selectedIds || selectedIds.length === 0) return [];
  const firstId = selectedIds[0];
  const firstStops = (stopsByTrip[firstId] || []).map((s) => s.id).filter(Boolean);
  let order: string[] = [...firstStops];

  for (let i = 1; i < selectedIds.length; i++) {
    const tid = selectedIds[i];
    const stops = stopsByTrip[tid];
    if (!stops || stops.length === 0) continue;

    // Iterate in trip sequence order; insert unknown stops next to the latest known anchor
    let pos = -1; // before start until we meet a known anchor
    for (const s of stops) {
      const sid = s.id;
      if (!sid) continue;
      const existingIdx = order.indexOf(sid);
      if (existingIdx >= 0) {
        pos = existingIdx; // move anchor forward
      } else {
        // insert after current anchor position
        const insertAt = Math.min(Math.max(pos + 1, 0), order.length);
        order.splice(insertAt, 0, sid);
        pos = insertAt;
      }
    }
  }

  if (reverse) return [...order].reverse();
  return order;
}

const toHHMM = (value?: string | null): string | null => {
  if (!value) return null;
  return value.slice(0, 5);
};

export function buildSeriesFromStops(tripId: string, label: string, stops: TripStopLite[]): Series | null {
  if (!stops || stops.length === 0) return null;
  const points: SeriesPoint[] = [];
  for (const s of stops) {
    const canonical = s.departure_time || s.arrival_time || null;
    const m = parseGtfsTimeToMinutes(canonical);
    if (m != null)
      points.push({
        stopId: s.id,
        minutes: m,
        arrivalHHMM: toHHMM(s.arrival_time),
        departureHHMM: toHHMM(s.departure_time),
      });
  }
  if (points.length < 2) return null;
  return { id: tripId, label, colorKey: label, kind: 'trip', points };
}



