# Changelog

## [1.9.0] - 2026-11-24

- Add sequential vehicle-block planning and weekday-aware feasibility. Preserve service trips, empty transfers and intermediate depot stays in the saved programme.
- Calculate typical-day energy prediction intervals using independent trip samples across the fleet, with the same sampling method as feasibility.
- Apply `db/migrations/011_shift_programme.sql` to databases upgrading from 1.8.0. Pair with frontend 1.4.0.

## [1.9.0-rc.2] - 2026-11-24

- Use the feasibility sampler for annual energy uncertainty. Sample each trip occurrence independently across all vehicle blocks at a given temperature and store the daily interval for each block and the fleet.
- Reconstruct intervals for existing analyses when their original prediction distributions are available. Pair with frontend 1.4.0-rc.2.
## [1.9.0-rc.1] - 2026-11-24

- Add a weekday-based vehicle block planner for scheduled trips, empty transfers and depot stays. Save the ordered programme and its movements together and validate the return to the starting depot.
- Group equivalent GTFS calendar entries in the trip catalogue and expose the trip number and stop count for timetable variants.
- Use service weekdays in feasibility studies and calculate passenger loads separately for scheduled trips and empty movements.
- Apply `db/migrations/011_shift_programme.sql` to existing databases before starting the new API. It adds nullable programme and service weekday columns to existing vehicle blocks. Pair with frontend 1.4.0-rc.1.

## [1.8.0] - 2026-11-24

- Price bus bodies by physical length, provide LFP and NMC pack defaults and preserve battery chemistry in annual assessments.
- Calculate project-horizon TCO for annual comparisons, including replacements and residual values. Support read-only cost previews and the optional asset-lifetime method.
- Pair with frontend 1.3.0. This release uses elettra-core 2.3.0 and requires no database migration.

## [1.8.0-rc.1] - 2026-11-23

- Price electric bus bodies by physical length and expose LFP/NMC pack mass and price defaults. Preserve chemistry for annual assessments and historical results.
- Value electric and diesel investments over a common project horizon, including replacements and terminal residuals. The annual cost API supports the previous annualization with `tco_method=asset_lifetime`.
- Add read-only TCO previews for the annual dashboard and exports using the same financial calculation. Pair with frontend 1.3.0-rc.2.

## [1.7.0] - 2026-11-18

- Promote the functionality validated in `1.7.0-rc.2`, with no functional changes.
- Add authenticated feasibility-evaluation renaming while preserving ownership, results and Mean/Q95 pairing.
- Pin pip 26.3 in both Docker build and runtime stages and verify dependency consistency in native release images.
- Preserve the frozen `elettra-core` package at `2.3.0` and the existing model, HVAC and optimization settings.
- No new database migration is required. Existing saved analyses and installation-specific vehicle-block data are preserved.
- Keep long fleet diagnostics and tolerance benchmarks opt-in. Pair with frontend `1.0.0`.

## [1.7.0-rc.2] - 2026-11-17

- Pin pip 26.3 in the shared Docker base used by both builder and runtime stages.
- Verify the installer version and dependency consistency in every native release image before publication.
- Keep the frozen `elettra-core` package at `2.3.0`; prediction-model and HVAC release pins are unchanged.
- No new database migration is required. TPL vehicle-block corrections are changes to installation data and are not distributed in the container image.
- Pair with frontend `1.0.0-rc.2` and publish as a prerelease without moving stable container aliases.

## [1.7.0-rc.1] - 2026-11-17

- Add authenticated renaming of feasibility evaluations while preserving their results, ownership and Mean/Q95 pairing.
- Resolve the configured legacy prediction release in opt-in fleet diagnostics instead of requesting a model name absent from the active registry.
- Keep the frozen `elettra-core` package at `2.3.0`; prediction-model and HVAC release pins are unchanged.
- No new database migration is required for this release. Existing analyses are preserved.
- Pair with frontend `1.0.0-rc.1` and publish as a prerelease without moving stable container aliases.
- Validation note: the 60-vehicle-block joint legacy diagnostic reached the configured 600-second solver limit. The tolerance benchmark was interrupted because of execution time and memory use; OSRM checks were intentionally excluded. Long diagnostics remain opt-in and are not part of the routine release gate.

## [1.6.0] - 2026-11-10

- Promote the functionality validated in `1.6.0-rc.2`, with no functional changes.
- Preserve operational HVAC policies and immutable weather inputs for feasibility and annual analysis, including authenticated reproduction exports.
- Make optimization relative MIP tolerance configurable with `ELETTRA_OPTIMIZATION_MIP_REL_GAP` (default `1e-6`) for sizing and fixed-design verification.
- Keep long fleet diagnostics and tolerance benchmarks opt-in; routine release checks do not run them.
- Require migration `db/migrations/010_hvac_weather_snapshots.sql` on installations without the HVAC snapshot table. Existing results are not rewritten.
- Keep the frozen VECTO core and published HVAC templates unchanged. Pair with frontend `0.11.0`.
