# Operational HVAC policies, version 1

The operational adapter is outside the published `elettra_core` and VECTO
declaration templates. Neither their bytes nor their global constants change.
New VECTO feasibility requests default to
`feasibility-ghi-temperature-cap10-v1`; annual requests default to
`annual-pvgis-local-cap10-v1`. `auxiliary_policy_id` is optional in prediction
requests and in optimization `prediction_params`. Explicit historical VECTO
reproduction uses `legacy-fixed-100-v1`. Unversioned existing predictions retain
their original estimator and results. The legacy non-VECTO stack retains its
existing auxiliary function.

## Assumption register

| Item | Feasibility | Annual | Historical VECTO |
|---|---|---|---|
| Physical solar input | One deterministic GHI per selected temperature | Effective stored PVGIS all-sky GHI | Fixed 100 W/m² |
| GHI curve | ≤10 °C: 50; 17 °C: 800; ≥25 °C: 1000 W/m²; linear between knots | Not used | Not used |
| Interpretation | Engineering stress assumption, not a meteorological percentile | Global horizontal irradiance; no route-orientation transposition | Original fixed-input interpretation |
| Clouding | 1 | 1 | Original 0.65 below 17 °C, otherwise 0.80 |
| Solar multiplier / glazing transmission | 0.25 / 0.95 | 0.25 / 0.95 | Unchanged |
| Comfort | Published VECTO default policy | Published VECTO default policy | Unchanged |
| Passenger count | Selected occupancy × bus capacity | Selected occupancy × bus capacity | Original calculation |
| Terminal HVAC | Last min(gap, 10) minutes, empty bus | Same, using minute GHI | None |
| Fixed auxiliaries | Trips only, current greybox/template ownership | Same | Unchanged |
| Thermal inertia | None: steady-state power | Same | Unchanged |

The solar adaptation selects the published climatic row on **physical** GHI
first, then passes GHI / original-clouding into that row. This cancels clouding
without changing the 0.25 multiplier, glazing transmission, COP, or published
model. All-sky GHI is used under the agreed engineering interpretation of the
inherited window factor; this does not claim a geometric window transposition.

Terminals must be between consecutive occurrences at the same stop, with neither
boundary marked as a depot for the owner. There is no HVAC allowance before the
first trip, after the last trip, at a depot or during a transfer between stops.
Fixed auxiliaries are never added to these intervals. Electricity, fuel energy,
canonical diesel litres and uncovered thermal demand remain distinct.

## Time and annual aggregation

The annual calculation preserves scenario temperatures, occurrence counts and
fixed fleet configuration. It reconstructs the saved temperature-cluster
membership using KMeans (`random_state=42`, `n_init=10`) and the existing UTC
clustering window, validating centroids and counts. It does not reinterpret the
representative timetable as a new GTFS service-calendar model.

Each cluster date uses the GTFS agency timezone. Its service origin is local noon
minus 12 elapsed hours; hours ≥24 extend that service day. Resolved UTC origins
are saved so later timezone-rule changes cannot alter replay. PVGIS observation
times include `irradiance_time_offset`. GHI is interpolated linearly at service
minute midpoints with cyclic TMY year boundaries. Partial minutes are integrated
proportionally. Each occurrence receives the mean of its energies over its
cluster dates, then annual reporting multiplies by the saved occurrence count.
Temperature remains constant at the original scenario centroid.

## Reproducibility and performance

Resolved policy and inputs are persisted before inference. The saved inputs
contain ordered occurrences, stop/depot classification, local intervals, cluster
dates, UTC origins and the weather checksum. Migration 010 adds an immutable,
content-addressed, deduplicated `hvac_weather_snapshots` archive. It stores the
actual effective T/GHI values, timestamp axis and available provider provenance.
For historical weather imports it explicitly does not infer unavailable GHI
source years. New `elettra-hybrid-temperature-solar-v2` imports update temperature,
GHI, other PVGIS fields and provenance together. Their raw-temperature rollback
baseline comes from those same PVGIS months. No saved analysis is backfilled.

Policy and input fingerprint participate in prediction reuse and battery-variant
compatibility. A changed current timetable invalidates a prediction for a new
optimization. Historical exports continue to use saved inputs, never live rows.

Within a request, weather, schedules, GHI minute arrays, HVAC response tables and
cumulative energy integrals are shared across compatible duties and battery
variants. Annual response tables use a 1 W/m² grid, interpolation within a cell,
and direct VECTO calculation at mode/climatic-row transitions. Feasibility uses
one direct response per equivalent bus/occupancy/temperature/heater configuration,
without weather requests or additional QRF or optimizer invocations.

Terminal electricity is a deterministic minute load in the solver and every SOC
replay, including simultaneous charging. It is not assigned to a trip or added
to its mass sensitivity. Statistical support remains trip-only; terminal energy
shifts total statistics and histograms and is replayed unchanged in each existing
Monte Carlo trajectory. Mean/Q95 retain their existing solver workflow.

Prediction `inference_batch` records preparation/inference time and model-call
count. `forecast_verification.solver_seconds` separately sums sizing and fixed
design solver time; the existing overall elapsed-time field is retained.

## Audit interface and regression evidence

Authenticated `auxiliary-assumptions` endpoints exist below prediction runs,
optimization runs and yearly analyses. Normal responses omit large input arrays;
`include_inputs=true` includes the archived weather once per checksum and the
saved service intervals/calendar. Ownership is checked before any snapshot is
read. The result pages show compact assumptions, expandable details, separate
trip/terminal HVAC electricity and both JSON downloads.

The anonymous permanent `tests/fixtures/hvac_405.json` contains only weather and
required schedule geometry, with synthetic occurrence/stop IDs. Its 222 trips
total 6688 bus-minutes/day. Terminal HVAC totals 955 bus-minutes/day. Without
terminal HVAC, regression totals are 93997.530363 kWh/year for historical VECTO
and 103748.197043 kWh/year for PVGIS with Clouding=1 (tolerance 1 kWh/year against
the direct audit). The terminal contribution is 13617.519091 kWh/year.

Tests cover the curve, direct VECTO equivalence, ventilation/row transitions,
saturation, diesel heating, timezone/DST/extended times/PVGIS offset, missing
weather, input fingerprints, terminal caps/depot exclusion, simultaneous charging,
SOC and statistical replay, QRF batching, lazy exports and authentication.
Private deployment records, credentials and raw operational snapshots belong
outside this repository. For backend `1.6.0` and frontend `0.11.0`, validate the immutable release images
in `elettra-user-staging` before promoting the same digests to production.
Back up production before applying missing migration 010; preserve existing
results, runtime configuration and the frozen VECTO core.
