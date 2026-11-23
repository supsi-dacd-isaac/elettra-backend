# Changelog

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
