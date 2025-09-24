# Stringline Diagram Integration Plan

## Context

We need a dynamic stringline (time–stops) diagram in the frontend (`ShiftsPage.tsx`) that previews an in-progress shift while trips are being assembled and that displays existing shifts once selected. Unlike the backend script, this feature must rely on local state plus selective API calls because the shift may not yet be persisted.

## Goals

- Render a live, incremental chart as depot legs, transfer legs, and revenue trips are added or removed.
- Share the capability with existing saved shifts (loaded from `/api/v1/user/shifts/`).
- Reuse cached GTFS stop data wherever possible to minimise network chatter.
- Provide responsive UX (loading indicators, error hints) and keep the planner flow unblocked.

## High-Level Tasks

1. **Data Model Extensions**
   - Standardise `stopsByTrip` shape (include stop id, name, lat, lon, arrival/departure times).
   - Add `stopMeta` map (stop id → metadata) and `stopOrder` array (ordered stop ids).
   - Track synthetic legs (depot/transfer) in local state so they appear on the chart.

2. **Stop Ordering Strategy**
   - Whenever new stops arrive, update `stopMeta`.
   - Compute `stopOrder` by sorting along the dominant geographic axis (lon span ≥ lat span ⇒ sort by lon, otherwise lat), with optional manual override/toggle (`reverseY`).

3. **Derived Stringline Dataset**
   - Memoise transformation from trips + stop data into chart series (`{tripId, label, colourKey, points: [{stopId, minutes}]}`).
   - Use GTFS time >24h safely (convert to minutes).
   - Include depot/transfer legs using locally captured timing.

4. **Chart Component**
   - Create `StringlineChart` (likely SVG with D3 utilities or custom rendering).
   - Inputs: `stopOrder`, `stopMeta`, `series`, `highlightTripId`, `loading`, `error`.
   - Output: hourly vertical gridlines, stop horizontal lines, legend, tooltips.

5. **Data Fetching Pipeline**
   - Extend `ensureStopsForTrip(tripId)` to normalise outputs and populate caches for both candidate and selected trips.
   - Batch fetch when multiple unresolved trips are added (debounced `Promise.all`).
   - Track per-trip load/error flags to inform the chart (e.g., show partial state while data arrives).

6. **Integration Points**
   - `Selected` panel (in-progress shift): show chart once leave depot is set and at least one trip is selected; update on every selection change.
   - `SavedShiftsPanel`: render chart when a saved shift is expanded/selected (fetch stops for its structure on demand).

7. **Performance & UX**
   - Cache stop data in `Map` (persist via context/session) to avoid refetching.
   - Provide loading placeholders when essential trip data is missing.
   - Support long shifts (consider zoom/pan or vertical scroll for many stops).

8. **Testing & Validation**
   - Add utility tests for time parsing, stop ordering, and series derivation (Jest/unit).
   - Add Storybook/Playwright scenario for `StringlineChart` with mock datasets.
   - Manually cross-check chart output against backend Python script for sample shifts.

## Open Questions / Next Steps

- Decide on visual library (pure SVG vs. D3 vs. existing chart lib).
- Define legend/colour strategy (per shift vs. per trip route).
- Confirm how to surface incomplete data (e.g., highlight trips missing GTFS stop times).

_Keep this document updated as implementation decisions are made or tasks are completed._


