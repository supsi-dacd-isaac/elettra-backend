# Combined QRF forecasting

Annual analysis creation submits the complete scenario × vehicle-block matrix
to `POST /api/v1/simulation/yearly-prediction-runs/`. Each assignment retains
its explicit battery pack count. The ordinary prediction endpoint also runs
all requested vehicle blocks together.

The shared service prepares all rows and invokes the predictor once per
compatible model contract, without a fixed-size chunk limit. Its existing
mean, median and quantile passes are unchanged. Features, physical mass,
QRF reference mass, electric auxiliaries and diesel heating remain attached
to each occurrence. Results are split back into the original per-run records
by row position, not by trip ID (which can repeat).

Optimization uses the same service for initial forecasts and for all missing
forecasts within each exact-mass verification round. Later battery sizes
depend on earlier solver results and cannot be forecast as one upfront request.
Aggregate Q95 support is also recovered for all known references together;
Monte Carlo seeds, sample counts and the complete QRF leaf distribution are
unchanged. Annual weather recalculation uses the combined service as well.

Trip and duty statistics are reused within the request/optimization only.
There is no new persistent cache to invalidate after timetable or profile edits.
CPU inference runs outside the API event loop. A failed combined request marks
all unfinished forecast records failed; result persistence is atomic.

## Local comparison, 2026-11-04

The same saved TPL inputs and frozen model/core were compared with the original
implementation at `090372c`. Measurements include feature/statistics preparation,
inference and database persistence, exclude model startup and browser polling,
and were run without concurrent test suites. These are single measurements,
not statistical performance guarantees.

| Case | Before | Combined | Speedup |
| --- | ---: | ---: | ---: |
| Annual: 2 duties, 8 temperatures, 14/10 packs | 14.51 s | 2.07 s | 7.01× |
| Annual: 9 Line 5 duties, 8 temperatures, 10/14/16 packs | 87.79 s | 14.12 s | 6.22× |
| Optimization forecast preparation: 9 Line 5 duties, −5 °C, including Q95 profiles | 22.86 s | 11.70 s | 1.95× |

For the optimization case (222 trip occurrences), predictor calls fell from
nine to one. Predictor time alone fell from 1.28 s to 0.18 s; Q95 profile
construction fell from 11.22 s to 1.37 s by batching support recovery and reusing
statistics. These figures do not include MILP solving.

All 30,734 compared annual numeric values and all 105,212 optimization numeric
values matched exactly, including consumption components, quantiles, reference
mass/context, aggregate support, Q95 profiles and weighted annual totals.

Permanent regressions cover legacy and hybrid QRF models, different battery
masses, repeated trips, reversed row ordering, request ownership/validation,
terminal failures, forecast reuse, and exact-mass batching for battery-only,
charging-only and joint optimization.
